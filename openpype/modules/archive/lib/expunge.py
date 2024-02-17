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
import os
import re
import datetime
import shutil
import time
import fnmatch
import logging
from glob import glob

from . import utils
from . import const

from openpype import AYON_SERVER_ENABLED
from openpype import client as op_cli
from openpype.lib import Logger
from openpype.pipeline import Anatomy
if AYON_SERVER_ENABLED:
    from ayon_shotgrid.lib import credentials
else:
    from openpype.modules.shotgrid.lib import credentials
from openpype.modules.delivery.scripts.media import (
    SG_FIELD_OP_INSTANCE_ID,
    SG_FIELD_MEDIA_GENERATED,
    SG_FIELD_MEDIA_PATH
)

logger = Logger.get_logger(__name__)

NOW = time.time()

WARNING_THRESHOLD = NOW - 7 * 24 * 60 * 60  # 7 days ago

DELETE_THRESHOLD = NOW - 14 * 24 * 60 * 60  # 14 days ago

DELETE_PREFIX = "__MARKED_FOR_DELETION__"

# "{root[work]}": {
#     "{project[code]}": {
#         "production": {},
#         "config": {
#             "ocio": {}
#         },
#         "resources": {
#             "footage": {
#                 "plates": {},
#                 "offline": {}
#             },
#             "audio": {},
#             "art_dept": {}
#         },
#         "editorial": {},
#         "io": {
#             "incoming": {},
#             "outgoing": {}
#         },
#         "assets": {
#             "characters": {},
#             "prop": {},
#             "locations": {}
#         },
#         "shots": {},
#         "data": {
#             "nuke": {
#                 "untitled_autosaves": {}
#             }
#         },
#         "tools": {
#             "maya": {
#                 "all": {},
#                 "2023": {},
#                 "2024": {}
#             },
#             "houdini": {
#                 "all": {},
#                 "20.0": {},
#                 "19.5": {},
#                 "19.0": {}
#             },
#             "nuke": {
#                 "all": {},
#                 "14.0": {},
#                 "15.0": {}
#             }
#         }
#     }
# }

if const._debug:
    logger.info("<!>Running in Developer Mode<!>\n")

# ------------// Callable Functions //------------
def clean_all():
    total_size = 0
    scan_start = time.time()

    summary_dir = os.path.join("/pipe", "archive_logs")
    if not os.path.exists(summary_dir):
        os.makedirs(summary_dir)

    timestamp = time.strftime('%Y%m%d%H%M')
    summary_file = os.path.join(summary_dir, f"{timestamp}.txt")

    # Create a file handler which logs even debug messages
    file_handler = logging.FileHandler(summary_file)
    file_handler.setLevel(logging.info)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    logger.addHandler(file_handler)

    logger.info("======= CLEAN ALL PROJECTS =======")

    for proj in sorted(glob(const.PROJECTS_DIR + "/*")):
        proj = os.path.basename(proj)
        total_size += clean_project(proj, calculate_size=True, force_delete=False)

    elapsed_time = time.time() - scan_start
    logger.info("Total Clean Time %s", utils.time_elapsed(elapsed_time))


def clean_project(proj_code, calculate_size=False, force_delete=False):
    """
    Performs a routine cleaning of an active project. Removes finaled shots from the
    outgoing, review, and final folders. Removes rendered publishes older than 5
    versions.
    """
    target_root = "{0}/{1}".format(const.PROJECTS_DIR, proj_code)

    sg = credentials.get_shotgrid_session()
    sg_project = sg.find_one("Project", [["sg_code", "is", proj_code]], ["name"])
    if not sg_project:
        logger.error("SG Project with code '%s' not found, can't proceed", proj_code)
        return None

    summary_dir = os.path.join(target_root, "archive_logs")
    if not os.path.exists(summary_dir):
        os.makedirs(summary_dir)

    timestamp = time.strftime('%Y%m%d%H%M')
    summary_file = os.path.join(summary_dir, f"{timestamp}.txt")

    # Create a file handler which logs even debug messages
    file_handler = logging.FileHandler(summary_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    logger.addHandler(file_handler)

    project_name = sg_project["name"]
    logger.info("======= Cleaning project '%s' (%s) =======", project_name, proj_code)

    scan_start = time.time()

    # Prep
    # finaled_shots, sent_versions, breakdown_shots, breakdown_assets = get_shotgrid_data(proj_code)
    total_size = 0
    total_size += clean_published_files(project_name, calculate_size, force_delete)
    total_size += clean_work_files(target_root, calculate_size, force_delete)
    total_size += clean_io_files(target_root, calculate_size, force_delete)

    elapsed_time = time.time() - scan_start
    logger.info("Clean Time %s", utils.time_elapsed(elapsed_time))
    logger.info("More logging details at '%s'", summary_file)

    logger.removeHandler(file_handler)
    return total_size

def purge_project(proj_code):
    """
    Performs a deep cleaning of the project and preps if for archival by moving to
    staging area ('ol03/For_Archive/Ready_To_Send'). This should only be executed
    after a project has been finaled and no one is actively working on it.
    """
    clean_project(proj_code, force_delete=True)


# ------------// Common Functions //------------

def get_shotgrid_data(proj_code):

    logger.info(" - Getting Final list from Shotgrid")

    # Authenticate Shotgrid
    sg = credentials.get_shotgrid_session()

    # Find if project is restricted from clean up
    if sg.find(
        "Project",
        [["sg_code", "is", proj_code], ["sg_auto_cleanup", "is", False]],
    ):
        return

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

    # Find Shots that have been finaled
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
        # "entity.Shot.sg_shots_breakdown",
        "sg_status_list",
        SG_FIELD_MEDIA_GENERATED,
        SG_FIELD_MEDIA_PATH,
        SG_FIELD_OP_INSTANCE_ID
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
            finaled_shots[shot_name]["final"].append(
                version["code"]
            )
        elif version["sg_status_list"] == "snt":
            if shot_name not in sent_versions:
                sent_versions[shot_name] = []
            sent_versions[shot_name].append(
                version["code"]
            )


def delete_filepath(filepath):
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


def consider_file_for_deletion(filepath, calculate_size=False, force_delete=False):

    filepath_stat = os.stat(filepath)
    size = 0

    if force_delete:
        delete_filepath(filepath)
        if calculate_size:
            size = filepath_stat.st_size
        return True, size

    # If file is newer than warning, ignore
    if filepath_stat.st_mtime > WARNING_THRESHOLD:
        return False, size

    # Extract the directory path and the original name
    dir_path, original_name = os.path.split(filepath)

    if DELETE_PREFIX in original_name and filepath_stat.st_mtime < DELETE_THRESHOLD:
        delete_filepath(filepath)
        if calculate_size:
            size = filepath_stat.st_size
        return True, size

    # Create the new name with the prefix
    new_name = f"{DELETE_PREFIX}{original_name}"

    # Construct the full path for the new name
    new_filepath = os.path.join(dir_path, new_name)

    # Rename the file or folder
    if not const._debug:
        os.rename(filepath, new_filepath)

    logger.info(f"Marked for deletion: '{filepath}' -> '{new_filepath}'")

    return True, size


def clean_published_files(project_name, calculate_size=False, force_delete=False):

    logger.info(" - Finding already published files")
    total_size = 0

    anatomy = Anatomy(project_name)

    versions = op_cli.get_versions(project_name)
    for version in versions:
        rootless_source_path = version["data"].get("source")
        source_path = anatomy.fill_root(rootless_source_path)
        file_deleted, size = consider_file_for_deletion(source_path, force_delete)
        if file_deleted and calculate_size:
            total_size += size

    if calculate_size:
        logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))


def clean_io_files(target_root, calculate_size=False, force_delete=False):

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
                    file_deleted, size = consider_file_for_deletion(filepath, force_delete)
                    if file_deleted and calculate_size:
                        total_size += size

                # Check each subdirectory in the current directory
                for dirname in list(dirnames):  # Use a copy of the list for safe modification
                    subdirpath = os.path.join(dirpath, dirname)
                    file_deleted, size = consider_file_for_deletion(subdirpath, force_delete)
                    if file_deleted:
                        # Remove from dirnames to prevent further exploration
                        dirnames.remove(dirname)
                        if calculate_size:
                            total_size += size

    if calculate_size:
        logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size


def clean_work_files(target_root, calculate_size=False, force_delete=False):

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
    }
    file_patterns_to_remove = {
        ".*.nk~",
        ".*nk_history",
        ".*nk.autosave.*",
        "*_auto*.hip",
        "*_bak*.hip",
        "*.hrox.autosave"
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
                for filepath in os.listdir(folder_path):
                    file_deleted, size = consider_file_for_deletion(filepath, force_delete)
                    if file_deleted and calculate_size:
                        total_size += size

                    # Remove from dirnames to prevent further exploration
                    dirnames.remove(folder)

            # Delete all files that match the patterns that we have decided
            # we should delete
            for pattern in file_patterns_to_remove:
                for filename in fnmatch.filter(filenames, pattern):
                    filepath = os.path.join(dirpath, filename)
                    file_deleted, size = consider_file_for_deletion(filepath, force_delete)
                    if file_deleted and calculate_size:
                        total_size += size

        if calculate_size:
            logger.info("\n\nRemoved {0:,.3f} GB".format(utils.to_unit(total_size)))

    return total_size

# ------------// Archival Functions //------------
# def move_for_archive():
#     logger.info(" - Moving Shots to Archive Directory")
#     target = "{0}/{1}".format(const.PROJECTS_DIR, target_project)
#     shot_regex = "[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z, _]*"
#     asset_regex = "_assets/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*"

#     # Shots
#     for path in glob("{0}/{1}".format(target, shot_regex)):
#         archive_path(path)

#     # Assets
#     for path in glob("{0}/{1}".format(target, asset_regex)):
#         archive_path(path)

#     # Move remaining sequence folders
#     for path in glob("{0}/[0-9, a-z, A-Z]*/_*".format(target)):
#         archive_path(path)

#     # Move project base folders
#     for path in glob("{0}/_*".format(target)):
#         archive_path(path)

#     # Rsync the entire project to ensure everything is copied
#     archive_path(target, rsync=True)

#     # Remove empty sequence level folders
#     for path in glob("{0}/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z, _]*/".format(target)):
#         if not os.listdir(path) and not const._debug:
#             shutil.rmtree(path)
#             logger.info("Remove {0}".format(path))
#     # Remove empty sequence level folders
#     for path in glob("{0}/[0-9, a-z, A-Z]*/".format(target)):
#         if not os.listdir(path) and not const._debug:
#             shutil.rmtree(path)
#             logger.info("Remove {0}".format(path))

# def compress_workfiles(proj_code):

#     target = os.path.join(const.ARCHIVE_DIR, proj_code)

#     # Shots
#     logger.info(" - Compressing shot work files")
#     render_regex = (
#         "[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*"  # JAP
#     )
#     for path in sorted(glob("{0}/{1}/work".format(target, render_regex))):
#         if const._debug:
#             logger.info(" + Dry compress --{0}".format("/".join(path.split("/")[-5:-1])))
#         else:
#             logger.info(" + {0}".format("/".join(path.split("/")[-5:-1])))
#             os.system("cd {0} &&zip -0rmT work work".format(os.path.dirname(path)))

#     # Assets
#     logger.info(" - Compressing asset work files")
#     render_regex = "[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*"
#     for path in glob("{0}/_assets/{1}/work".format(target, render_regex)):
#         if const._debug:
#             logger.info(" + Dry compress --{0}".format("/".join(path.split("/")[-5:-1])))
#         else:
#             logger.info(" + {0}".format("/".join(path.split("/")[-3:-1])))
#             os.system("cd {0} &&zip -0rmT work work".format(os.path.dirname(path)))
