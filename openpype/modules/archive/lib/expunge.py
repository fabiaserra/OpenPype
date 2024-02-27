"""
Main cleaner module. Includes functions for active project cleaning and archiving.
"""
import clique
import fnmatch
import glob
import logging
import os
import re
import shutil
import time
import pandas as pd

from ast import literal_eval
from datetime import datetime, timedelta

from openpype import AYON_SERVER_ENABLED
from openpype import client as op_cli
from openpype.lib import Logger, run_subprocess
from openpype.pipeline import Anatomy
from openpype.tools.utils import paths as path_utils
from openpype.client.operations import OperationsSession
if AYON_SERVER_ENABLED:
    from ayon_shotgrid.lib import credentials
else:
    from openpype.modules.shotgrid.lib import credentials
from openpype.modules.delivery.scripts.media import (
    SG_FIELD_OP_INSTANCE_ID,
    SG_FIELD_MEDIA_GENERATED,
    SG_FIELD_MEDIA_PATH,
)

from . import utils
from . import const

# Thresholds to warn about files that are older than this time to be marked for deletion
# lower numbers is less caution, higher numbers for files we want to be more careful about
# deleting
WARNING_THRESHOLDS = {
    0: datetime.today() - timedelta(days=3),
    1: datetime.today() - timedelta(days=5),
    2: datetime.today() - timedelta(days=10),
}

# Thresholds to keep files marked for deletion before they get deleted
# lower numbers is less caution, higher numbers for files we want to be more careful about
# deleting
DELETE_THRESHOLDS = {
    0: timedelta(days=5),
    1: timedelta(days=7),
    2: timedelta(days=10)
}

# Prefix to use for files that are marked for deletion
DELETE_PREFIX = "__DELETE__"

# Format to use for the date in the delete prefix
DATE_FORMAT = "%Y-%m-%d"

# Object that holds the current time
TIME_NOW = datetime.today()

# String that represents the current time
TIME_NOW_STR = TIME_NOW.strftime(DATE_FORMAT)

# Prefix to use for files that are marked for deletion with the current time
TIME_DELETE_PREFIX = f"{DELETE_PREFIX}({TIME_NOW_STR})"

# Regular expression used to remove the delete prefix from a path
DELETE_PREFIX_RE = re.compile(rf"{DELETE_PREFIX}\(.*\)")

# Set of file patterns to delete if we find them in the project and they are
# older than a certain time
TEMP_FILE_PATTERNS = {
    ".*.nk~",
    ".*nk_history",
    ".*nk.autosave.*",
    "*_auto*.hip",
    "*_bak*.hip",
    "*.hrox.autosave",
}

logger = Logger.get_logger(__name__)


class ArchiveProject:

    def __init__(self, proj_code) -> None:

        self.sg = credentials.get_shotgrid_session()
        self.proj_code = proj_code

        sg_project = self.sg.find_one(
            "Project", [["sg_code", "is", proj_code]], ["name"]
        )
        if not sg_project:
            msg = f"SG Project with code '{proj_code}' not found, can't proceed"
            logger.error(msg)
            raise ValueError(msg)

        self.project_name = sg_project["name"]
        self.anatomy = Anatomy(self.project_name)

        self.target_root = os.path.join(const.PROJECTS_DIR, proj_code)

        self.summary_dir = os.path.join(self.target_root, "archive_logs")
        if not os.path.exists(self.summary_dir):
            os.makedirs(self.summary_dir)

        timestamp = time.strftime("%Y%m%d%H%M")
        self.summary_file = os.path.join(self.summary_dir, f"{timestamp}{'_debug' if const._debug else ''}.txt")

        self.delete_data_file = os.path.join(
            self.summary_dir, f"delete_data{'_debug' if const._debug else ''}.csv"
        )

        # Populate the self.archive_entries with the existing CSV document
        # in the project if it exists
        self.read_archive_data()

        self.total_size_deleted = 0

    def clean(self, archive=False):
        """Performs a routine cleaning of an active project"""
        # Create a file handler which logs the execution of this function
        file_handler = logging.FileHandler(self.summary_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

        logger.addHandler(file_handler)

        logger.info(
            "======= Cleaning project '%s' (%s) ======= \n\n",
            self.project_name,
            self.proj_code,
        )
        start_time = time.time()

        # Comment out since it actually takes longer to pre-process
        # the existing entries to try delete them early than letting
        # the other functions discover the files again at this point
        # NOTE: this could change in the future once this script
        # runs daily and the archive is up to date
        # self.clean_existing_entries()

        self.clean_published_file_sources(force_delete=archive)
        self.clean_work_files(force_delete=archive)
        self.clean_io_files(force_delete=archive)

        # Delete assets based on shot status in SG
        shots_status = self.get_shotgrid_data()
        if shots_status:
            self.clean_shots_by_status(shots_status)

        if archive:
            self.compress_workfiles()

        elapsed_time = time.time() - start_time
        logger.info("\n\nMore logging details at '%s'", self.summary_file)
        logger.info("Clean Time: %s", utils.time_elapsed(elapsed_time))
        logger.info(
            "Deleted %s", utils.format_bytes(self.total_size_deleted)
        )

        self.write_archive_data()

        logger.removeHandler(file_handler)

    def purge(self):
        """
        Performs a deep cleaning of the project and preps if for archival by deleting
        all the unnecessary files and compressing the work directories. This should only
        be executed after a project has been finaled and no one is actively working on
        it.
        """
        self.clean(archive=True)

    def read_archive_data(self):
        """Read the archive data from the CSV file in the project as a dictionary"""
        self.archive_entries = {}

        if not os.path.exists(self.delete_data_file):
            logger.info(f"CSV file '{self.delete_data_file}' does not exist yet")
            return

        data_frame = pd.read_csv(self.delete_data_file)
        non_deleted_data = data_frame[~data_frame["is_deleted"]]
        data_list = non_deleted_data.to_dict(orient="records")
        for data_entry in data_list:
            data_entry["marked_time"] = pd.to_datetime(data_entry["marked_time"])
            data_entry["delete_time"] = pd.to_datetime(data_entry["delete_time"])
            data_entry["paths"] = literal_eval(data_entry["paths"])
            self.archive_entries[data_entry.pop("path")] = data_entry

    def write_archive_data(self):
        """Stores the archive data dictionary as a CSV file in the project.

        This allows us to retrieve the data in the archive dialog and keep
        a history of all the files archived in the project.
        """
        start_time = time.time()

        # Create final dictionary to store in csv
        data_dict = {
            "path": [],
            "delete_time": [],
            "marked_time": [],
            "size": [],
            "is_deleted": [],
            "publish_dir": [],
            "publish_id": [],
            "reason": [],
            "paths": [],
        }
        for path, data_entries in self.archive_entries.items():
            data_dict["path"].append(path)
            data_dict["delete_time"].append(data_entries["delete_time"])
            data_dict["marked_time"].append(data_entries["marked_time"])
            data_dict["size"].append(data_entries["size"])
            data_dict["is_deleted"].append(data_entries["is_deleted"])
            data_dict["publish_dir"].append(data_entries.get("publish_dir", ""))
            data_dict["publish_id"].append(data_entries.get("publish_id", ""))
            data_dict["reason"].append(data_entries.get("reason", ""))
            data_dict["paths"].append(data_entries.get("paths", set()))

        # Create a pandas data frame from current archive data dictionary
        df = pd.DataFrame(data_dict)

        # Make sure we don't overwrite existing entries from the main CSV file
        if os.path.exists(self.delete_data_file):
            existing_df = pd.read_csv(self.delete_data_file)
            combined_df = pd.concat([existing_df, df])
            df = combined_df.drop_duplicates(subset=["path"])

        # Write out data to CSV file
        df.to_csv(self.delete_data_file, index=False)

        elapsed_time = time.time() - start_time
        logger.info(
            "Saved CSV data in '%s', it took %s",
            self.delete_data_file,
            utils.time_elapsed(elapsed_time)
        )

    def get_archive_data(self):
        """Retrieves the data stored in the project as a pd.DataFrame object
        """
        return pd.read_csv(self.delete_data_file)

    # ------------// Common Functions //------------
    def get_shotgrid_data(self):
        """Get the necessary data from Shotgrid for getting more info about how to
        clean the project.
        """
        logger.info(" - Getting Final list from Shotgrid")

        # Find if project is restricted from clean up
        if self.sg.find(
            "Project",
            [["sg_code", "is", self.proj_code], ["sg_auto_cleanup", "is", False]],
        ):
            return False

        shots_status = {}

        statuses_to_check = [
            "snt", "fin", "omt"
        ]

        # Find all entities that have been finaled
        filters = [
            ["project.Project.sg_code", "is", self.proj_code],
            ["sg_status_list", "in", statuses_to_check],
        ]
        fields = [
            "code",
            "entity",
            "sg_status_list",
            SG_FIELD_MEDIA_GENERATED,
            SG_FIELD_MEDIA_PATH,
            SG_FIELD_OP_INSTANCE_ID,
        ]
        sg_versions = self.sg.find("Version", filters, fields)

        for sg_version in sg_versions:
            try:
                shot_name = sg_version["entity"]["name"]
            except TypeError:
                shot_name = "unassigned"

            version_status = sg_version["sg_status_list"]
            if version_status not in shots_status:
                shots_status[version_status] = {}
            if shot_name not in shots_status[version_status]:
                shots_status[version_status][shot_name] = []

            shots_status[version_status][shot_name].append(
                sg_version[SG_FIELD_OP_INSTANCE_ID]
            )

        return shots_status

    def clean_existing_entries(self, force_delete=False):
        """Clean existing entries from self.archive_entries"""
        logger.info(" \n---- Cleaning files marked for archive from CSV ---- \n")

        for _, data_entry in self.archive_entries.items():
            # Skip entries that have already been marked deleted
            if data_entry["is_deleted"]:
                continue

            self.consider_filepaths_for_deletion(
                data_entry["paths"],
                caution_level=None,
                force_delete=force_delete,
            )

    def get_version_path(self, version_id):
        """Get the path on disk of the version id by checking the path of the
        first representation found for that version.
        """
        # Create filepath from published file
        repre_docs = op_cli.get_representations(
            self.project_name, version_ids=[version_id]
        )
        version_path = None
        for repre_doc in repre_docs:
            repre_name_path = os.path.dirname(
                repre_doc["data"]["path"]
            )
            version_path = os.path.dirname(repre_name_path)
            break

        return version_path

    def clean_published_file_sources(self, force_delete=False):
        """Cleans the source of the published files of the project.

        Args:
            force_delete (bool, optional): Whether to force delete the files.
                Defaults to False.
        """
        logger.info(" \n---- Finding already published files ---- \n")

        # Level of caution for published files
        caution_level_default = 1

        # TODO: enable after a while since `stagingDir` integrate on the
        # representations was just added recently
        # repre_docs = op_cli.get_representations(
        #     project_name
        # )
        # # Iterate over all representations in the project and check if
        # # stagingDir is stored in its data and consider it for deletion
        # # if it's old enough
        # for repre_doc in repre_docs:
        #     staging_dir = repre_doc["data"].get("stagingDir")
        #     if staging_dir:
        #         staging_dir = anatomy.fill_root(staging_dir)
        #         # TODO: make sure to check if the staging dir is older than the publish!
        #         deleted, _, size = self.consider_file_for_deletion(
        #             staging_dir, caution_level=caution_level, force_delete
        #         )
        #         if deleted:
        #             logger.info(" - Published file in '%s'", )
        #             if calculate_size:
        #                 total_size += size

        version_docs = op_cli.get_versions(self.project_name)
        for version_doc in version_docs:

            # Reset caution level every time
            caution_level_ = caution_level_default

            version_id = version_doc["_id"]

            if version_doc["data"].get("source_deleted"):
                logger.debug(
                    "Skipping version '%s' as 'source_deleted' is true and that means it was already archived",
                    version_id
                )
                continue

            version_path = self.get_version_path(version_id)

            rootless_source_path = version_doc["data"].get("source")
            source_path = self.anatomy.fill_root(rootless_source_path)

            # Create a path of what we want to symlink the source path
            # to if we want to keep the source path but not the files
            symlink_paths = None

            # If source path is a Hiero workfile, we can infer that the publish
            # was a plate publish and a 'temp_transcode' folder was created next
            # to the workfile to store the transcodes before publish
            if source_path.endswith(".hrox"):
                subset_doc = op_cli.get_subset_by_id(
                    self.project_name, subset_id=version_doc["parent"]
                )
                if not subset_doc:
                    logger.warning(
                        "Couldn't find subset for version '%s' with id '%s for source path '%s'",
                        version_doc["name"], version_doc["parent"], source_path
                    )
                    continue
                # Hard-code the path to the temp_transcode folder
                source_files = glob.glob(os.path.join(
                    os.path.dirname(source_path),
                    "temp_transcode",
                    f"{subset_doc['name']}*",
                ))
                # Override caution file for temp_transcode files to be very low caution
                caution_level_ = 0
            # If source path is a Nuke work file, we can infer that the publish is
            # likely to be a render publish and the renders are stored in a
            # folder called 'renders' next to the Nuke file
            # NOTE: ignore the 'io' folder as it's used for the I/O of the project
            elif source_path.endswith(".nk") and "/io/" not in source_path:
                subset_doc = op_cli.get_subset_by_id(
                    self.project_name, subset_id=version_doc["parent"]
                )
                if not subset_doc:
                    logger.warning(
                        "Couldn't find subset for version '%s' with id '%s for source path '%s'",
                        version_doc["name"], version_doc["parent"], source_path
                    )
                    continue
                if subset_doc["data"]["family"] == "workfile":
                    continue
                asset_doc = op_cli.get_asset_by_id(
                    self.project_name, asset_id=subset_doc["parent"]
                )
                if not asset_doc:
                    logger.warning(
                        "Couldn't find asset for subset '%s' with id '%s'",
                        subset_doc["name"], subset_doc["parent"]
                    )
                    continue
                # Hard-code the path to the renders for Nuke files
                source_files = [os.path.join(
                    os.path.dirname(source_path),
                    "renders",
                    "nuke",
                    f"{asset_doc['name']}_{subset_doc['name'].replace(' ', '_')}",
                    "v{:03}".format(version_doc["name"]),
                )]
                symlink_paths = [version_path]
            # Otherwise, we just check the 'source' directly assuming that's
            # directly the source of the publish
            else:
                # Override caution file for I/O published files to be very low
                # caution
                if "/io/" in source_path:
                    caution_level_ = 0

                source_files, _, _, _ = path_utils.convert_to_sequence(
                    source_path
                )
                if source_path.endswith(".exr"):
                    symlink_paths = [
                        glob.glob(os.path.join(version_path, "exr", "*"))
                    ]

                if not source_files:
                    logger.warning(
                        "Couldn't find files for file pattern '%s'.",
                        source_path
                    )
                    continue

            logger.info(
                "Published files in version with id '%s': '%s'",
                version_id,
                version_path,
            )

            version_created = datetime.strptime(
                version_doc["data"]["time"], "%Y%m%dT%H%M%SZ"
            )

            # If we found files, we consider them for deletion
            deleted, _ = self.consider_filepaths_for_deletion(
                source_files,
                caution_level=caution_level_,
                force_delete=force_delete,
                create_time=version_created,
                extra_data={
                    "publish_id": version_id,
                    "publish_dir": version_path,
                    "reason": "Already published"
                },
                symlink_paths=symlink_paths
            )

            if deleted:
                # Add metadata to version so we can skip from inspecting it
                # in the future
                session = OperationsSession()
                session.update_entity(
                    self.project_name,
                    "version",
                    version_doc["_id"],
                    {"data.source_deleted": True}
                )
                session.commit()


    def clean_io_files(self, force_delete=False):
        """Cleans the I/O directories of the project.

        Args:
            force_delete (bool, optional): Whether to force delete the files.
                Defaults to False.

        Returns:
            float: The total size of the deleted files in bytes.
        """
        logger.info(" \n---- Finding old files in I/O ----\n")

        # Level of caution for I/O files
        caution_level = 1

        if force_delete:
            target_folders = ["incoming", "outgoing", "delivery", "outsource"]
        else:
            target_folders = ["outgoing", "delivery", "outsource"]

        for folder in target_folders:
            target = os.path.join(self.target_root, "io", folder)
            if os.path.exists(target):
                logger.debug(f"Scanning {target} folder")
            else:
                logger.warning(f"{target} folder does not exist")
                continue

            if force_delete:
                # Add entire folder
                self.consider_file_for_deletion(
                    target,
                    force_delete=True,
                    extra_data={
                        "reason": "Force delete on archive"
                    }
                )
            else:
                for dirpath, dirnames, filenames in os.walk(target, topdown=True):
                    # Check each subdirectory in the current directory
                    for dirname in list(
                        dirnames
                    ):  # Use a copy of the list for safe modification
                        subdirpath = os.path.join(dirpath, dirname)
                        deleted, marked = self.consider_file_for_deletion(
                            subdirpath,
                            caution_level=caution_level,
                            force_delete=force_delete,
                            extra_data={
                                "reason": "Routine clean up"
                            }
                        )
                        if deleted or marked:
                            # Remove from dirnames to prevent further exploration
                            dirnames.remove(dirname)

                    # Check each file in the current directory
                    filepaths = [os.path.join(dirpath, filename) for filename in filenames]
                    deleted, marked  = self.consider_filepaths_for_deletion(
                        filepaths,
                        caution_level=caution_level,
                        force_delete=force_delete,
                        extra_data={
                            "reason": "Routine clean up"
                        }
                    )

    def clean_work_files(self, force_delete=False):
        """Cleans the work directories of the project by removing old files and folders
        that we consider not relevant to keep for a long time.
        """
        logger.info(" \n---- Cleaning work files ----\n")

        # Folders that we want to clear all the files from inside them
        # that are older than our threshold and the number of caution
        # of removal to take for each
        folders_to_clean = {
            "ass": 2,
            "backup": 0,
            # "cache": 2,
            "ifd": 0,
            "ifds": 0,
            "img": 2,
            # "render": 2,
            # "renders": 2,
            # "nuke": 2,
            "temp_transcode": 0,
        }

        for folder in ["assets", "shots"]:
            target = os.path.join(self.target_root, folder)
            if os.path.exists(target):
                logger.debug(f" - Scanning {target} folder")
            else:
                logger.warning(f" - {target} folder does not exist")
                continue

            for dirpath, dirnames, filenames in os.walk(target, topdown=True):
                # Skip all folders that aren't within a 'work' directory
                if "/work" not in dirpath:
                    continue

                # Add files from the potential archive folders that are
                # older than 7 days
                for folder, caution_level in folders_to_clean.items():
                    if folder not in dirnames:
                        continue

                    filepaths = glob.glob(os.path.join(dirpath, folder, "*"))
                    deleted, marked = self.consider_filepaths_for_deletion(
                        filepaths,
                        caution_level=caution_level,
                        force_delete=force_delete,
                        extra_data={
                            "reason": "Transient file"
                        }
                    )
                    if deleted or marked:
                        # Remove from dirnames to prevent further exploration
                        dirnames.remove(folder)

                # Delete all files that match the patterns that we have decided
                # we should delete
                for pattern in TEMP_FILE_PATTERNS:
                    for filename in fnmatch.filter(filenames, pattern):
                        filepath = os.path.join(dirpath, filename)
                        deleted, marked = self.consider_file_for_deletion(
                            filepath,
                            caution_level=0,
                            force_delete=force_delete,
                            extra_data={
                                "reason": "Transient file"
                            }
                        )


    def clean_shots_by_status(self, shots_status, force_delete=False):
        """Cleans publishes by having information about the status of shots in SG.

        If we know that a version was omitted, we delete that version.
        For final statuses, we delete all the versions that are not final.
        """
        logger.info(" \n---- Cleaning shots based on its SG status ----\n")

        # Level of caution for archive based on status
        caution_level = 0

        # For final status, we add all versions but the ones listed
        for shot_name, version_ids in shots_status.get("fin", {}).items():

            # TODO: add more logic to delete other versions from shot
            #asset_doc = op_cli.get_asset_by_name(project_name, shot)

            for version_id in version_ids:
                version_doc = op_cli.get_version_by_id(
                    self.project_name, version_id=version_id, fields=["parent"]
                )
                subset_doc = op_cli.get_subset_by_id(
                    self.project_name, subset_id=version_doc["parent"], fields=["_id"]
                )
                version_docs = op_cli.get_versions(
                    self.project_name, subset_ids=[subset_doc["_id"]], fields=["_id"]
                )

                for version_doc in version_docs:
                    # Skip all the versions that were marked as final
                    other_version_id = str(version_doc["_id"])
                    if other_version_id in version_ids:
                        continue

                    # Add the directory where all the representations live
                    version_path = self.get_version_path(other_version_id)

                    self.consider_file_for_deletion(
                        version_path,
                        caution_level=caution_level,
                        force_delete=force_delete,
                        extra_data={
                            "publish_id": other_version_id,
                            "reason": "Old versions in final status"
                        }
                    )

        # For omitted status, we add the versions listed directly
        for shot_name, version_ids in shots_status.get("omt", {}).items():
            version_docs = op_cli.get_versions(
                self.project_name, version_ids=version_ids, fields=["_id"]
            )

            for version_doc in version_docs:
                version_id = version_doc["_id"]
                # Delete the directory where all the representations for that
                # version exist
                version_path = self.get_version_path(version_id)
                self.consider_file_for_deletion(
                    version_path,
                    caution_level=caution_level,
                    force_delete=force_delete,
                    extra_data={
                        "publish_id": version_id,
                        "reason": "Omitted status"
                    }
                )

    # ------------// Archival Functions //------------
    def compress_workfiles(self):
        """Compresses the work directories for a project."""

        logger.info(" \n---- Compressing work files ----\n")
        for dirpath, dirnames, _ in os.walk(self.target_root):
            if "work" in dirnames:
                work_dir = os.path.join(dirpath, "work")
                if const._debug:
                    logger.info(f" + Dry compress -- {work_dir}")
                else:
                    logger.info(f" + {work_dir}")
                    os.system(f"cd {os.path.dirname(work_dir)} && zip -rmT work work")

    def delete_filepath(self, filepath, silent=False):
        """Delete a file or directory"""
        try:
            if not const._debug:
                if os.path.isfile(filepath):
                    os.remove(filepath)  # Remove the file
                elif os.path.isdir(filepath):
                    shutil.rmtree(filepath)  # Remove the dir and all its contents
                else:
                    logger.info(f"'{filepath}' is not a valid file or directory.")

            if not silent:
                logger.info(f"Deleted path: '{filepath}'.")

            return True
        except Exception as e:
            logger.error(f"Error deleting '{filepath}': {e}")

        return False

    def parse_date_from_filename(self, filename):
        """Parse the date from the filename if it has the DELETE_PREFIX in it."""
        match = re.search(rf'{DELETE_PREFIX}\((.*?)\).*', filename)
        if match:
            date_string = match.group(1)
            return datetime.strptime(date_string, DATE_FORMAT)

    def consider_filepaths_for_deletion(
        self,
        filepaths,
        caution_level=2,
        force_delete=False,
        create_time=None,
        extra_data=None,
        symlink_paths=None,
    ):
        """Consider a clique.filepaths for deletion based on its age"""
        deleted = False
        marked = False

        if symlink_paths and len(symlink_paths) != len(filepaths):
            logger.error(
                "The number of symlink paths should be the same as the number of filepaths"
            )
            return False, False

        collections, remainders = clique.assemble(filepaths)
        for collection in collections:
            deleted_, marked_ = self.consider_collection_for_deletion(
                collection,
                caution_level,
                force_delete,
                create_time,
                extra_data=extra_data,
                symlink_paths=symlink_paths
            )
            if deleted_:
                deleted = True
            if marked_:
                marked = True

        for index, remainder in enumerate(remainders):
            deleted_, marked_ = self.consider_file_for_deletion(
                remainder,
                caution_level,
                force_delete,
                create_time,
                extra_data=extra_data,
                symlink_path=symlink_paths[index] if symlink_paths else None
            )
            if deleted_:
                deleted = True
            if marked_:
                marked = True

        return deleted, marked

    def consider_collection_for_deletion(
        self,
        collection,
        caution_level=2,
        force_delete=False,
        create_time=None,
        extra_data=None,
        symlink_paths=None,
    ):
        """Consider a clique.collection for deletion based on its age"""
        deleted = False
        marked = False

        for index, filepath in enumerate(collection):
            deleted_, marked_ = self.consider_file_for_deletion(
                filepath,
                caution_level,
                force_delete,
                create_time,
                silent=True,
                extra_data=extra_data,
                symlink_path=symlink_paths[index] if symlink_paths else None
            )
            if deleted_:
                deleted = True
            if marked_:
                marked = True

        if deleted:
            logger.info(f"Deleted collection '{collection}'")
        elif marked:
            logger.info(f"Marked collection for deletion: '{collection}' (caution: {caution_level})")

        return deleted, marked

    def get_filepath_size(self, filepath, filepath_stat):
        """Util function to retun size of a file by using 'du' if it's a directory or the stat
        object if it's a file
        """
        if os.path.isdir(filepath):
            return int(run_subprocess(["du", "-s", filepath]).split("\t")[0]) * 1024

        return filepath_stat.st_size

    def consider_file_for_deletion(
        self,
        filepath,
        caution_level=2,
        force_delete=False,
        create_time=None,
        silent=False,
        extra_data=None,
        symlink_path=None,
    ):
        """Consider a file for deletion based on its age

        Args:
            filepath (str): The path to the file
            calculate_size (bool, optional): Whether to calculate the size of the deleted
                files. Defaults to False.
            force_delete (bool, optional): Whether to force delete the files.
                Defaults to False.
            silent (bool, optional): Whether to suppress the log messages. Defaults to False.

        Returns:
            bool: Whether the file was deleted
            bool: Whether the file was marked for deletion
            float: The size of the deleted file
        """
        try:
            filepath_stat = os.stat(filepath)
        except FileNotFoundError:
            logger.warning(f"File not found: '{filepath}'")
            return False, False

        if os.path.islink(filepath):
            logger.debug(f"Skipping symlink: '{filepath}'")
            return False, False

        # Extract the directory path and the original name
        dir_path, original_name = os.path.split(filepath)

        # Replace frame with token to save file ranges under the same entry
        path_entry = path_utils.replace_frame_number_with_token(filepath, "*")
        if DELETE_PREFIX in original_name:
            # Replace delete prefix so we store the entry in the same data entry
            path_entry = DELETE_PREFIX_RE.sub(filepath, "")

        # Add entry to archive entries dictionary
        if path_entry in self.archive_entries:
            data_entry = self.archive_entries[path_entry]
            if filepath not in data_entry.get("paths"):
                data_entry["size"] += self.get_filepath_size(filepath, filepath_stat)
                data_entry["paths"].add(filepath)
        else:
            if caution_level is None:
                logger.error(
                    "No caution level was passed to the function, probably due "
                    "to assuming the file was already marked for deletion but it "
                    "wasn't found on the existing entries. Skipping!")
                return False, False

            data_entry = {
                "marked_time": TIME_NOW,
                "delete_time": TIME_NOW + DELETE_THRESHOLDS[caution_level],
                "is_deleted": False,
                "paths": {filepath}
            }
            if extra_data:
                data_entry.update(extra_data)

        if DELETE_PREFIX in original_name or force_delete:
            # If we are passed the time marked for deletion or force_delete is True, delete it
            if datetime.today() > data_entry.get("delete_time") or force_delete:
                if not silent:
                    logger.debug(
                        f"File has been marked for deletion enough time, deleting it."
                    )
                success = self.delete_filepath(filepath, silent=silent)
                if success:
                    data_entry["is_deleted"] = True
                    size_deleted = self.get_filepath_size(filepath, filepath_stat)
                    self.total_size_deleted += size_deleted
                    self.archive_entries[path_entry] = data_entry
                    return path_entry, data_entry
                return False, False

            return False, True
        # If file was modified after the creation time (publish), ignore removal to be safe
        elif create_time and filepath_stat.st_mtime > create_time.timestamp():
            logger.debug(
                "File '%s' was modified after it was published, ignoring the removal",
                filepath
            )
            return False, False
        # If file is newer than warning, ignore
        elif filepath_stat.st_mtime > WARNING_THRESHOLDS.get(caution_level, WARNING_THRESHOLDS[2]).timestamp():
            return False, False

        # Create the new name with the prefix
        new_name = f"{TIME_DELETE_PREFIX}{original_name}"

        # Construct the full path for the new name
        new_filepath = os.path.join(dir_path, new_name)

        # Rename the file or folder
        if not const._debug:
            os.rename(filepath, new_filepath)

        # If we are passing a symlink path, we want to create a symlink from
        # the source path to the new path
        if symlink_path:
            os.symlink(symlink_path, filepath)
            logger.debug(
                "Created symlink from '%s' to '%s'", symlink_path, filepath
            )

        if not silent:
            logger.info(
                f"Marked for deletion: '{filepath}' -> '{new_name}' (caution: {caution_level})"
            )

        data_entry["paths"].remove(filepath)
        data_entry["paths"].add(new_filepath)

        # Calculate the file size only at the end if it's marked for deletion
        data_entry["size"] = self.get_filepath_size(filepath, filepath_stat)

        self.archive_entries[path_entry] = data_entry

        return False, True


# ------------// Callable Functions //------------
def clean_all():
    scan_start = time.time()

    timestamp = time.strftime("%Y%m%d%H%M")
    summary_file = os.path.join(const.EXPORT_DIR, f"{timestamp}{'_debug' if const._debug else ''}.txt")

    # Create a file handler which logs even debug messages
    file_handler = logging.FileHandler(summary_file)
    file_handler.setLevel(logging.info)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )

    logger.addHandler(file_handler)

    logger.info("======= CLEAN ALL PROJECTS =======")

    for proj in sorted(os.listdir(const.PROJECTS_DIR)):
        archive_project = ArchiveProject(proj)
        archive_project.clean(archive=False)

    elapsed_time = time.time() - scan_start
    logger.info("Total Clean Time %s", utils.time_elapsed(elapsed_time))
