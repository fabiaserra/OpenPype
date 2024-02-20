"""
Main cleaner module. Includes functions for active project cleaning and archiving.
Only three functions should be called externally.

    - clean_all()
        Performs a routine cleaning of all active projects

    - clean_project(proj_code)
        Routine cleaning of a specified project

    - purge_project(proj_code)
        Performs a deep cleaning of the project and preps if for archival
"""
import clique
import fnmatch
import glob
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta

from . import utils
from . import const

from openpype import AYON_SERVER_ENABLED
from openpype import client as op_cli
from openpype.lib import Logger
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

logger = Logger.get_logger(__name__)

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

# Prefix to use for files that are marked for deletion with the current time
TIME_DELETE_PREFIX = f"{DELETE_PREFIX}({datetime.today().strftime(DATE_FORMAT)})"

if const._debug:
    logger.info("<!>Running in Developer Mode<!>\n")


# ------------// Callable Functions //------------
def clean_all():
    total_size = 0
    scan_start = time.time()

    summary_dir = os.path.join("/pipe", "archive_logs")
    if not os.path.exists(summary_dir):
        os.makedirs(summary_dir)

    timestamp = time.strftime("%Y%m%d%H%M")
    summary_file = os.path.join(summary_dir, f"{timestamp}.txt")

    # Create a file handler which logs even debug messages
    file_handler = logging.FileHandler(summary_file)
    file_handler.setLevel(logging.info)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )

    logger.addHandler(file_handler)

    logger.info("======= CLEAN ALL PROJECTS =======")

    for proj in sorted(os.listdir(const.PROJECTS_DIR)):
        total_size += clean_project(proj, archive=False)

    elapsed_time = time.time() - scan_start
    logger.info("Total Clean Time %s", utils.time_elapsed(elapsed_time))


def clean_project(proj_code, archive=False):
    """Performs a routine cleaning of an active project"""
    target_root = "{0}/{1}".format(const.PROJECTS_DIR, proj_code)

    sg = credentials.get_shotgrid_session()
    sg_project = sg.find_one("Project", [["sg_code", "is", proj_code]], ["name"])
    if not sg_project:
        logger.error("SG Project with code '%s' not found, can't proceed", proj_code)
        return None

    summary_dir = os.path.join(target_root, "archive_logs")
    if not os.path.exists(summary_dir):
        os.makedirs(summary_dir)

    timestamp = time.strftime("%Y%m%d%H%M")
    summary_file = os.path.join(summary_dir, f"{timestamp}.txt")

    # Create a file handler which logs even debug messages
    file_handler = logging.FileHandler(summary_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )

    logger.addHandler(file_handler)

    project_name = sg_project["name"]
    logger.info("======= Cleaning project '%s' (%s) ======= \n\n", project_name, proj_code)

    scan_start = time.time()

    # Prep
    # finaled_shots, sent_versions, breakdown_shots, breakdown_assets = get_shotgrid_data(
    #     proj_code
    # )

    total_size = 0
    total_size += clean_published_files(project_name, force_delete=archive)
    total_size += clean_work_files(target_root, force_delete=archive)
    total_size += clean_io_files(target_root, force_delete=archive)

    elapsed_time = time.time() - scan_start
    logger.info("Clean Time %s", utils.time_elapsed(elapsed_time))
    logger.info("More logging details at '%s'", summary_file)

    if archive:
        compress_workfiles(target_root)

    logger.removeHandler(file_handler)
    return total_size


def purge_project(proj_code):
    """
    Performs a deep cleaning of the project and preps if for archival by deleting
    all the unnecessary files and compressing the work directories. This should only
    be executed after a project has been finaled and no one is actively working on it.
    """
    clean_project(proj_code, archive=True)


# ------------// Common Functions //------------
def get_shotgrid_data(proj_code):
    """Get the necessary data from Shotgrid for getting more info about how to
    clean the project.

    Args:
        proj_code (str): The project code

    Returns:
        dict: A dictionary with the following keys:
            - finaled_shots: A dictionary with the finaled shots and their final versions
            - sent_versions: A dictionary with the sent versions
            - breakdown_shots: A list of shots marked for breakdown
            - breakdown_assets: A list of assets marked for breakdown
    """
    logger.info(" - Getting Final list from Shotgrid")

    # Authenticate Shotgrid
    sg = credentials.get_shotgrid_session()

    # Find if project is restricted from clean up
    if sg.find(
        "Project",
        [["sg_code", "is", proj_code], ["sg_auto_cleanup", "is", False]],
    ):
        return False

    breakdown_shots = {}
    finaled_shots = {}
    sent_versions = {}

    # Find Shots marked for Breakdown
    for shot in sg.find(
        "Shot",
        [
            ["project.Project.sg_code", "is", proj_code],
            ["sg_shots_breakdown", "is", True],
        ],
        ["code"],
    ):
        breakdown_shots.append(shot["code"])

    # Find all entities that have been finaled
    filters = [
        ["project.Project.sg_code", "is", proj_code],
        ["sg_status_list", "in", ["snt", "fin"]],
    ]
    fields = [
        "code",
        "sg_delivery_name",
        "sg_final_version",
        "entity",
        "entity.Shot.sg_delivery_name",
        "sg_status_list",
        SG_FIELD_MEDIA_GENERATED,
        SG_FIELD_MEDIA_PATH,
        SG_FIELD_OP_INSTANCE_ID,
    ]
    finished_versions = sg.find("Version", filters, fields)

    for version in finished_versions:
        try:
            shot_name = version["entity"]["name"]
        except TypeError:
            shot_name = "unassigned"
        delivery_name = version.get("entity.Shot.sg_delivery_name") or None

        if version["sg_status_list"] == "fin":
            if shot_name not in finaled_shots:
                finaled_shots[shot_name] = {
                    "code": [],
                    "op_id": [],
                    "delivery_name": delivery_name,
                }
            finaled_shots[shot_name]["final"].append(version["code"])
        elif version["sg_status_list"] == "snt":
            if shot_name not in sent_versions:
                sent_versions[shot_name] = []
            sent_versions[shot_name].append(version["code"])

    return finaled_shots, sent_versions, breakdown_shots


def delete_filepath(filepath, silent=False):
    """Delete a file or directory"""
    try:
        if os.path.isfile(filepath):
            if not const._debug:
                os.remove(filepath)  # Remove the file
            if not silent:
                logger.info(f"Deleted file: '{filepath}'.")
            return True
        elif os.path.isdir(filepath):
            if not const._debug:
                shutil.rmtree(filepath)  # Remove the dir and all its contents
            if not silent:
                logger.info(f"Deleted directory: '{filepath}'.")
            return True
        else:
            if not silent:
                logger.info(f"'{filepath}' is not a valid file or directory.")
    except Exception as e:
        logger.error(f"Error deleting '{filepath}': {e}")

    return False


def parse_date_from_filename(filename):
    """Parse the date from the filename if it has the DELETE_PREFIX in it."""
    match = re.search(rf'{DELETE_PREFIX}\((.*?)\).*', filename)
    if match:
        date_string = match.group(1)
        return datetime.strptime(date_string, DATE_FORMAT)


def consider_filepaths_for_deletion(filepaths, caution_level=2, force_delete=False, create_time=None):
    """Consider a clique.filepaths for deletion based on its age"""
    total_size = 0
    deleted = False
    marked = False

    collections, remainders = clique.assemble(filepaths)
    for collection in collections:
        deleted_, marked_, size_ = consider_collection_for_deletion(
            collection, caution_level, force_delete, create_time
        )
        if size_:
            total_size += size_
        if deleted_:
            deleted = True
        if marked_:
            marked = True

    for remainder in remainders:
        deleted_, marked_, size_ = consider_file_for_deletion(
            remainder, caution_level, force_delete, create_time
        )
        if size_:
            total_size += size_
        if deleted_:
            deleted = True
        if marked_:
            marked = True

    return deleted, marked, total_size


def consider_collection_for_deletion(collection, caution_level=2, force_delete=False, create_time=None):
    """Consider a clique.collection for deletion based on its age"""
    size = 0
    deleted = False
    marked = False
    for filepath in collection:
        deleted_, marked_, size_ = consider_file_for_deletion(
            filepath, caution_level, force_delete, create_time, silent=True
        )
        if size_:
            size += size_
        if deleted_:
            deleted = True
        if marked_:
            marked = True

    if deleted:
        logger.info(f"Deleted collection '{collection}'")
    elif marked:
        logger.info(f"Marked collection for deletion: '{collection}' (caution: {caution_level})")

    return deleted, marked, size


def consider_file_for_deletion(filepath, caution_level=2, force_delete=False, create_time=None, silent=False):
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
    size = 0

    try:
        filepath_stat = os.stat(filepath)
    except FileNotFoundError:
        logger.warning(f"File not found: '{filepath}'")
        return False, False, size

    if force_delete:
        success = delete_filepath(filepath, silent=silent)
        if success:
            size = filepath_stat.st_size
        return True, False, size

    # Extract the directory path and the original name
    dir_path, original_name = os.path.split(filepath)

    if DELETE_PREFIX in original_name:
        date_marked_for_delete = parse_date_from_filename(original_name)
        # if the file has been marked for deletion more than the threshold, delete it
        if datetime.today() - date_marked_for_delete > DELETE_THRESHOLDS[caution_level]:
            if not silent:
                logger.debug(
                    f"File has been marked for deletion enough time, deleting it (caution: {caution_level})."
                )
            success = delete_filepath(filepath, silent=silent)
            if success:
                size = filepath_stat.st_size
            return True, False, size

        return False, True, size
    # If file was modified after the creation time (publish), ignore removal to be safe
    elif create_time and filepath_stat.st_mtime > create_time.timestamp():
        logger.debug(
            "File '%s' was modified after it was published, ignoring the removal",
            filepath
        )
        return False, False, size
    # If file is newer than warning, ignore
    elif filepath_stat.st_mtime > WARNING_THRESHOLDS[caution_level].timestamp():
        return False, False, size

    # Create the new name with the prefix
    new_name = f"{TIME_DELETE_PREFIX}{original_name}"

    # Construct the full path for the new name
    new_filepath = os.path.join(dir_path, new_name)

    # Rename the file or folder
    if not const._debug:
        os.rename(filepath, new_filepath)

    if not silent:
        logger.info(f"Marked for deletion: '{filepath}' -> '{new_name}' (caution: {caution_level})")

    return False, True, size


def clean_published_files(project_name, force_delete=False):
    """Cleans the source of the published files of the project.

    Args:
        project_name (str): The name of the project
        calculate_size (bool, optional): Whether to calculate the size of the deleted
            files. Defaults to False.
        force_delete (bool, optional): Whether to force delete the files.
            Defaults to False.
    """
    logger.info(" \n---- Finding already published files ---- \n")
    total_size = 0

    anatomy = Anatomy(project_name)

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
    #         deleted, _, size = consider_file_for_deletion(
    #             staging_dir, caution_level=caution_level, force_delete
    #         )
    #         if deleted:
    #             logger.info(" - Published file in '%s'", )
    #             if calculate_size:
    #                 total_size += size

    version_docs = op_cli.get_versions(project_name)
    for version_doc in version_docs:

        # Reset caution level every time
        caution_level_ = caution_level_default

        if version_doc["data"].get("source_deleted"):
            logger.debug(
                "Skipping version '%s' as 'source_deleted' is true and that means it was already archived",
                version_doc["_id"]
            )
            continue
        rootless_source_path = version_doc["data"].get("source")
        source_path = anatomy.fill_root(rootless_source_path)

        version_created = datetime.strptime(version_doc["data"]["time"], "%Y%m%dT%H%M%SZ")

        # If source path is a Hiero workfile, we can infer that the publish
        # was a plate publish and a 'temp_transcode' folder was created next
        # to the workfile to store the transcodes before publish
        if source_path.endswith(".hrox"):
            subset_doc = op_cli.get_subset_by_id(
                project_name, subset_id=version_doc["parent"]
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
        # If source path is a Nuke work file, we can infer that the publish is
        # likely to be a render publish and the renders are stored in a
        # folder called 'renders' next to the Nuke file
        # NOTE: ignore the 'io' folder as it's used for the I/O of the project
        elif source_path.endswith(".nk") and "/io/" not in source_path:
            subset_doc = op_cli.get_subset_by_id(
                project_name, subset_id=version_doc["parent"]
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
                project_name, asset_id=subset_doc["parent"]
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
        # Otherwise, we just check the 'source' directly assuming that's
        # directly the source of the publish
        else:
            # Override caution file for I/O published files to be very low caution
            if "/io/" in source_path:
                caution_level_ = 0
            source_files, _, _, _ = path_utils.convert_to_sequence(source_path)
            if not source_files:
                logger.warning(
                    "Couldn't find files for file pattern '%s'.",
                    source_path
                )
                continue

        # If we found files, we consider them for deletion
        deleted, marked, size = consider_filepaths_for_deletion(
            source_files, caution_level=caution_level_, force_delete=force_delete, create_time=version_created
        )

        # If any file gets deleted, try to infer the path where the
        # version was published so it's easier to find the corresponding
        # publish in the future
        if deleted or marked:
            total_size += size
            repre_docs = op_cli.get_representations(
                project_name, version_ids=[version_doc["_id"]]
            )
            repre_name_path = os.path.dirname(
                repre_docs[0]["data"].get("path")
            )
            version_path = os.path.dirname(repre_name_path)
            logger.info(
                "Published files in '%s' with id '%s'",
                version_path,
                version_doc["_id"]
            )
            if deleted:
                # Add metadata to version so we can skip from inspecting it
                # in the future
                session = OperationsSession()
                session.update_entity(
                    project_name, "version", version_doc["_id"], {"data.source_deleted": True}
                )
                session.commit()

            logger.info("\n")

    logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size


def clean_io_files(target_root, force_delete=False):
    """Cleans the I/O directories of the project.

    Args:
        target_root (str): The root directory of the project
        calculate_size (bool, optional): Whether to calculate the size of the deleted
            files. Defaults to False.
        force_delete (bool, optional): Whether to force delete the files.
            Defaults to False.

    Returns:
        float: The total size of the deleted files in bytes.
    """
    logger.info(" \n---- Finding old files in I/O ----\n")
    total_size = 0

    # Level of caution for I/O files
    caution_level = 1

    for folder in ["incoming", "outgoing", "delivery", "outsource"]:
        target = os.path.join(target_root, "io", folder)
        if os.path.exists(target):
            logger.debug(f"Scanning {target} folder")
        else:
            logger.warning(f"{target} folder does not exist")
            continue

        if force_delete:
            # Add entire folder
            deleted, _, size = consider_file_for_deletion(
                target, force_delete=True
            )
            if deleted:
                total_size += size
        else:
            for dirpath, dirnames, filenames in os.walk(target, topdown=True):
                deleted, marked, size = consider_file_for_deletion(
                    dirpath, caution_level=caution_level, force_delete=force_delete
                )
                if deleted or marked:
                    # Prevent further exploration of this directory
                    dirnames.clear()
                    filenames.clear()
                    logger.info("\n")
                    if deleted:
                        total_size += size

                # Check each subdirectory in the current directory
                for dirname in list(
                    dirnames
                ):  # Use a copy of the list for safe modification
                    subdirpath = os.path.join(dirpath, dirname)
                    deleted, marked, size = consider_file_for_deletion(
                        subdirpath, caution_level=caution_level, force_delete=force_delete
                    )
                    if deleted or marked:
                        # Remove from dirnames to prevent further exploration
                        dirnames.remove(dirname)
                        logger.info("\n")
                        if deleted:
                            total_size += size

                # Check each file in the current directory
                filepaths = [os.path.join(dirpath, filename) for filename in filenames]
                deleted, marked, size = consider_filepaths_for_deletion(
                    filepaths, caution_level=caution_level, force_delete=force_delete
                )
                if deleted or marked:
                    logger.info("\n")
                    if deleted:
                        total_size += size

    logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size


def clean_work_files(target_root, force_delete=False):
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
        "cache": 2,
        "ifd": 0,
        "ifds": 0,
        "img": 2,
        "render": 2,
        "renders": 2,
        "temp_transcode": 0,
    }
    file_patterns_to_remove = {
        ".*.nk~",
        ".*nk_history",
        ".*nk.autosave.*",
        "*_auto*.hip",
        "*_bak*.hip",
        "*.hrox.autosave",
    }
    total_size = 0

    for folder in ["assets", "shots"]:
        target = os.path.join(target_root, folder)
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
                deleted, marked, size = consider_filepaths_for_deletion(
                    filepaths, caution_level=caution_level, force_delete=force_delete
                )
                if deleted or marked:
                    # Remove from dirnames to prevent further exploration
                    dirnames.remove(folder)
                    logger.info("\n")
                    total_size += size

            # Delete all files that match the patterns that we have decided
            # we should delete
            for pattern in file_patterns_to_remove:
                for filename in fnmatch.filter(filenames, pattern):
                    filepath = os.path.join(dirpath, filename)
                    deleted, marked, size = consider_file_for_deletion(
                        filepath, caution_level=0, force_delete=force_delete
                    )
                    if deleted or marked:
                        total_size += size
                        logger.info("\n")

    logger.info("Removed {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size


# ------------// Archival Functions //------------
def compress_workfiles(target_root):
    """Compresses the work directories for a project."""

    logger.info(" \n---- Compressing work files ----\n")
    for dirpath, dirnames, _ in os.walk(target_root):
        if "work" in dirnames:
            work_dir = os.path.join(dirpath, "work")
            if const._debug:
                logger.info(f" + Dry compress -- {work_dir}")
            else:
                logger.info(f" + {work_dir}")
                os.system(f"cd {os.path.dirname(work_dir)} && zip -rmT work work")
