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
import re
import os
import shutil
import time
import fnmatch
import logging
import glob
from datetime import datetime, timedelta

from . import utils
from . import const

from openpype import AYON_SERVER_ENABLED
from openpype import client as op_cli
from openpype.lib import Logger
from openpype.pipeline import Anatomy
from openpype.tools.utils import paths as path_utils

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

# Threshold to warn about files that are older than this time to be marked for deletion
WARNING_THRESHOLD = datetime.today() - timedelta(days=7)

# Threshold to keep files marked for deletion before they get deleted
DELETE_THRESHOLD = timedelta(days=7)

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
        total_size += clean_project(proj, calculate_size=True, archive=False)

    elapsed_time = time.time() - scan_start
    logger.info("Total Clean Time %s", utils.time_elapsed(elapsed_time))


def clean_project(proj_code, calculate_size=False, archive=False):
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
    logger.info("======= Cleaning project '%s' (%s) =======", project_name, proj_code)

    scan_start = time.time()

    # Prep
    # finaled_shots, sent_versions, breakdown_shots, breakdown_assets = get_shotgrid_data(
    #     proj_code
    # )

    total_size = 0
    total_size += clean_published_files(project_name, calculate_size, force_delete=archive)
    total_size += clean_work_files(target_root, calculate_size, force_delete=archive)
    total_size += clean_io_files(target_root, calculate_size, force_delete=archive)

    elapsed_time = time.time() - scan_start
    logger.info("Clean Time %s", utils.time_elapsed(elapsed_time))
    logger.info("More logging details at '%s'", summary_file)

    if archive:
        compress_workfiles(target_root)

    logger.removeHandler(file_handler)
    return total_size


def purge_project(proj_code, calculate_size=False):
    """
    Performs a deep cleaning of the project and preps if for archival by deleting
    all the unnecessary files and compressing the work directories. This should only
    be executed after a project has been finaled and no one is actively working on it.
    """
    clean_project(proj_code, calculate_size=calculate_size, archive=True)


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


def delete_filepath(filepath):
    """Delete a file or directory"""
    try:
        if os.path.isfile(filepath):
            if not const._debug:
                os.remove(filepath)  # Remove the file
            logger.info(f"Deleted file: '{filepath}'.")
        elif os.path.isdir(filepath):
            if not const._debug:
                shutil.rmtree(filepath)  # Remove the dir and all its contents
            logger.info(f"Deleted directory: '{filepath}'.")
        else:
            logger.info(f"'{filepath}' is not a valid file or directory.")
    except Exception as e:
        logger.error(f"Error deleting '{filepath}': {e}")


def parse_date_from_filename(filename):
    """Parse the date from the filename if it has the DELETE_PREFIX in it."""
    match = re.search(rf'{DELETE_PREFIX}\((.*?)\).*', filename)
    if match:
        date_string = match.group(1)
        return datetime.strptime(date_string, DATE_FORMAT)


def consider_file_for_deletion(filepath, calculate_size=False, force_delete=False):
    """Consider a file for deletion based on its age"""
    size = 0

    try:
        filepath_stat = os.stat(filepath)
    except FileNotFoundError:
        logger.warning(f"File not found: '{filepath}'")
        return False, size

    if force_delete:
        delete_filepath(filepath)
        if calculate_size:
            size = filepath_stat.st_size
        return True, size

    # Extract the directory path and the original name
    dir_path, original_name = os.path.split(filepath)

    if DELETE_PREFIX in original_name:
        date_marked_for_delete = parse_date_from_filename(original_name)
        # if the file has been marked for deletion more than 7 days, delete it
        logger.debug(
            "Found date marked for deletion to be '%s'", date_marked_for_delete
        )
        logger.debug("Current date is '%s'", datetime.today())
        if datetime.today() - date_marked_for_delete < DELETE_THRESHOLD:
            logger.debug("File has been marked for deletion enough time to be deleted")
            delete_filepath(filepath)
            if calculate_size:
                size = filepath_stat.st_size
        return True, size

    # If file is newer than warning, ignore
    elif filepath_stat.st_mtime > WARNING_THRESHOLD.timestamp():
        return False, size

    # Create the new name with the prefix
    new_name = f"{TIME_DELETE_PREFIX}{original_name}"

    # Construct the full path for the new name
    new_filepath = os.path.join(dir_path, new_name)

    # Rename the file or folder
    if not const._debug:
        os.rename(filepath, new_filepath)

    logger.info(f"Marked for deletion: '{filepath}' -> '{new_name}'")

    return True, size


def clean_published_files(project_name, calculate_size=False, force_delete=False):
    """Cleans the source of the published files of the project.

    Args:
        project_name (str): The name of the project
        calculate_size (bool, optional): Whether to calculate the size of the deleted
            files. Defaults to False.
        force_delete (bool, optional): Whether to force delete the files.
            Defaults to False.
    """
    logger.info(" - Finding already published files")
    total_size = 0

    anatomy = Anatomy(project_name)

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
    #         file_deleted, size = consider_file_for_deletion(
    #             staging_dir, force_delete
    #         )
    #         if file_deleted:
    #             logger.info(" - Published file in '%s'", )
    #             if calculate_size:
    #                 total_size += size

    version_docs = op_cli.get_versions(project_name)
    for version_doc in version_docs:
        rootless_source_path = version_doc["data"].get("source")
        source_path = anatomy.fill_root(rootless_source_path)

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
                return
            asset_doc = op_cli.get_asset_by_id(
                project_name, asset_id=subset_doc["parent"]
            )
            if not asset_doc:
                logger.warning(
                    "Couldn't find asset for subset '%s' with id '%s",
                    subset_doc["name"], subset_doc["parent"]
                )
                continue
            # Hard-code the path to the renders for Nuke files
            source_files = [os.path.join(
                os.path.dirname(source_path),
                "renders",
                "nuke",
                f"{asset_doc['name']}_{subset_doc['name']}",
                "v{:03}".format(version_doc["name"]),
            )]
        # Otherwise, we just check the 'source' directly assuming that's
        # directly the source of the publish
        else:
            source_files, _, _, _ = path_utils.convert_to_sequence(source_path)
            if not source_files:
                logger.warning(
                    "Couldn't find files for file pattern '%s'.",
                    source_path
                )
                continue

        # If we found files, we consider them for deletion
        one_file_deleted = False
        for source_file in source_files:
            file_deleted, size = consider_file_for_deletion(
                source_file, force_delete
            )
            if file_deleted:
                one_file_deleted = True
                if calculate_size:
                    total_size += size

        # If any file gets deleted, try to infer the path where the
        # version was published so it's easier to find the corresponding
        # publish in the future
        if one_file_deleted:
            repre_docs = op_cli.get_representations(
                project_name, version_ids=[version_doc["_id"]]
            )
            repre_name_path = os.path.dirname(
                repre_docs[0]["data"].get("path")
            )
            version_path = os.path.dirname(repre_name_path)
            logger.info(" - Published files in '%s'", version_path)

    if calculate_size:
        logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size


def clean_io_files(target_root, calculate_size=False, force_delete=False):
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
    logger.info(" - Finding old files in I/O")
    total_size = 0

    for folder in ["incoming", "outgoing", "delivery", "outsource"]:
        target = os.path.join(target_root, "io", folder)
        if os.path.exists(target):
            logger.debug(f"Scanning {target} folder")
        else:
            logger.debug(f"{target} folder does not exist")
            continue

        if force_delete:
            # Add entire folder
            file_deleted, size = consider_file_for_deletion(target, force_delete)
            if file_deleted and calculate_size:
                total_size += os.path.getsize(target)
        else:
            for dirpath, dirnames, filenames in os.walk(target, topdown=True):
                file_deleted, size = consider_file_for_deletion(dirpath, force_delete)
                if file_deleted:
                    # Prevent further exploration of this directory
                    dirnames.clear()
                    if calculate_size:
                        total_size += size

                # Check each file in the current directory
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    file_deleted, size = consider_file_for_deletion(
                        filepath, force_delete
                    )
                    if file_deleted and calculate_size:
                        total_size += size

                # Check each subdirectory in the current directory
                for dirname in list(
                    dirnames
                ):  # Use a copy of the list for safe modification
                    subdirpath = os.path.join(dirpath, dirname)
                    file_deleted, size = consider_file_for_deletion(
                        subdirpath, force_delete
                    )
                    if file_deleted:
                        # Remove from dirnames to prevent further exploration
                        dirnames.remove(dirname)
                        if calculate_size:
                            total_size += size

    if calculate_size:
        logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size


def clean_work_files(target_root, calculate_size=False, force_delete=False):
    """Cleans the work directories of the project by removing old files and folders
    that we consider not relevant to keep for a long time.

    """
    logger.info(" - Cleaning work files")

    # Folders that we want to clear all the files from inside them
    # that are older than our threshold
    folders_to_clean = {
        "ass",
        "backup",
        "cache",
        "ifd",
        "ifds",
        "img",
        "render",
        "renders",
        "temp_transcode",
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
            logger.debug(f" - {target} folder does not exist")
            continue

        for dirpath, dirnames, filenames in os.walk(target, topdown=True):
            # Skip all folders that aren't within a 'work' directory
            if "/work" not in dirpath:
                continue

            # Add files from the potential archive folders that are
            # older than 7 days
            for folder in folders_to_clean:
                if folder not in dirnames:
                    continue
                folder_path = os.path.join(dirpath, folder)
                for filename in os.listdir(folder_path):
                    file_deleted, size = consider_file_for_deletion(
                        os.path.join(folder_path, filename), force_delete
                    )
                    if file_deleted and calculate_size:
                        total_size += size

                    # Remove from dirnames to prevent further exploration
                    logger.debug(
                        "Clearing folder '%s' from dirnames '%s'",
                        folder_path,
                        dirnames
                    )
                    dirnames.remove(folder)

            # Delete all files that match the patterns that we have decided
            # we should delete
            for pattern in file_patterns_to_remove:
                for filename in fnmatch.filter(filenames, pattern):
                    filepath = os.path.join(dirpath, filename)
                    file_deleted, size = consider_file_for_deletion(
                        filepath, force_delete
                    )
                    if file_deleted and calculate_size:
                        total_size += size

        if calculate_size:
            logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size


# ------------// Archival Functions //------------
def compress_workfiles(target_root):
    """Compresses the work directories for a project."""

    logger.info(" - Compressing work files")
    for dirpath, dirnames, filenames in os.walk(target_root):
        if "work" in dirnames:
            work_dir = os.path.join(dirpath, "work")
            if const._debug:
                logger.info(f" + Dry compress -- {work_dir}")
            else:
                logger.info(f" + {work_dir}")
                os.system(f"cd {os.path.dirname(work_dir)} && zip -rmT work work")
