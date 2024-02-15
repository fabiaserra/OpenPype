"""
Main cleaner module. Includes functions for active project cleaning and archiving.
Only three functions should be called externally.

    - clean_all()
        Performs a routine cleaning of all active projects

    - clean_project(project_code)
        Routine cleaning of a specified project

    - purge_project(project_code)
        Performs a deep cleaning of the project and preps if for archival
"""
import os
import re
import datetime
import shutil
import time
import fnmatch
from glob import glob

from . import utils
from . import const

from openpype.modules.shotgrid.lib import credentials
from openpype.lib import Logger


logger = Logger.get_logger(__name__)

TODAY = datetime.datetime.today()

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

class Expunge:
    """
    Main cleaner Class. Includes functions for active project cleaning and archiving.
    Only three functions should be called externally.

        - clean_all()
            Performs a routine cleaning of all active projects

        - clean_project(project_code)
            Routine cleaning of a specified project

        - purge_project(project_code)
            Performs a deep cleaning of the project and preps if for archival
    """

    if const._debug:
        logger.info("<!>Running in Developer Mode<!>\n")

    # ------------// Callable Functions //------------
    def clean_all(self):
        total_size = 0
        scan_start = time.time()

        for proj in sorted(glob(const.PROJECTS_DIR + "/*")):
            proj = os.path.basename(proj)
            clean_size = self.clean_project(proj, silent=False)
            total_size += clean_size

        logger.info("\n\n{0:,.3f} TB cleared Total".format(total_size / 1000))
        elapsed_time = time.time() - scan_start
        logger.info("Total Clean Time {0}".format(utils.TimeEstimate.elapsed(elapsed_time)))

    def clean_project(self, project_code, silent=False):
        """
        Performs a routine cleaning of an active project. Removes finaled shots from the
        outgoing, review, and final folders. Removes rendered publishes older than 5
        versions.
        """
        self.target_project = project_code

        if const._debug:
            dev_message = " - [DRY RUN]"
        else:
            dev_message = ""

        utils.log_header(
            "Active Cleaning for {0}{1}".format(self.target_project, dev_message)
        )
        self.start_time = time.time()
        self.finaled_shots = {}
        self.sent_versions = {}
        self.breakdown_shots = []
        self.breakdown_assets = []
        self.marked_to_remove = {"paths": [], "size": 0}

        # Prep
        self.check_shotgrid()
        # self.io_folder()
        # self.clean_old_renders()
        self.clean_old_files()

        # Remove files
        # self.remove_files(silent=silent)

        return self.marked_to_remove["size"]

    def purge_project(self, project_code):
        """
        Performs a deep cleaning of the project and preps if for archival by moving to
        staging area ('ol03/For_Archive/Ready_To_Send'). This should only be executed
        after a project has been finaled and no one is actively working on it.
        """

        self.target_project = project_code

        if const._debug:
            dev_message = "-- DRY RUN"
        else:
            dev_message = ""

        utils.log_header(
            "Cleaning for Archive {0}{1}".format(self.target_project, dev_message)
        )
        self.start_time = time.time()
        self.finaled_shots = {}
        self.sent_versions = {}
        self.breakdown_shots = []
        self.breakdown_assets = []
        self.marked_to_remove = {"paths": [], "size": 0}

        # Prep
        self.check_shotgrid()
        # self.io_folder(delete=True)
        # self.clean_shots()
        # self.clean_assets()

        # Removal
        # self.remove_files()

        # Move to Archive folder
        # self.move_for_archive()

        # Compress workfiles while in Archive folder
        self.compress_workfiles()

    # ------------// Core Utilities //------------
    def remove_files(self, paths=[], silent=False):
        summary = False
        if not paths:
            summary = True
            paths = self.marked_to_remove["paths"]
        else:
            if isinstance(paths, str):
                paths = [paths]

        total_len = len(paths)
        total_size = 0

        if total_len:
            logger.info("\n // Removing files //")
            for path in sorted(paths):
                try:
                    total_size += utils.check_size(path)
                    if not const._debug:
                        if summary and not silent:
                            logger.info("- Deleting -- {0}".format(path))
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                        elif os.path.isfile(path):
                            os.remove(path)
                    else:
                        if not silent:
                            logger.info("- Dry Run Delete -- {0}".format(path))
                except Exception as e:
                    logger.info("\n!!Could not Delete -- {0} \n{1}\n".format(path, e))

            if summary:
                logger.info("\n{0} Files removed".format(total_len))
                logger.info("{0:,.0f} GB cleared".format(total_size))
                elapsed_time = time.time() - self.start_time
                logger.info("Duration {0}".format(utils.TimeEstimate.elapsed(elapsed_time)))

            self.marked_to_remove["size"] = total_size

    # def archive_path(self, path, rsync=False):
    #     destination = os.path.dirname(
    #         "{0}/{1}".format(const.ARCHIVE_DIR, path.split("/Projects/")[-1])
    #     )
    #     folder_name = os.path.basename(path)

    #     if not os.path.exists(destination) and not const._debug:
    #         os.makedirs(destination)

    #     if rsync:
    #         rsync_cmd = "rsync -r -t --ignore-existing --progress {0} {1}".format(
    #             path, destination
    #         )
    #         if const._debug:
    #             logger.info(" - Dry Sync -- {0}".format(rsync_cmd))
    #         else:
    #             logger.info("Executing RSYNC on entire project")
    #             os.system(rsync_cmd)
    #     else:
    #         copy_files = False
    #         if path.split("/")[5] == "_assets":
    #             if "/".join(path.split("/")[7:9]) in self.breakdown_assets:
    #                 copy_files = True
    #         else:
    #             if "_".join(path.split("/")[5:8]) in self.breakdown_shots:
    #                 copy_files = True

    #         if copy_files:
    #             if const._debug:
    #                 logger.info("Dry Copy {0} --> {1}".format(path, destination))
    #             else:
    #                 os.system("cp -r {0} {1}".format(path, destination))
    #         else:
    #             if const._debug:
    #                 logger.info("Dry Move {0} --> {1}".format(path, destination))
    #             else:
    #                 try:
    #                     shutil.move(path, destination)
    #                 except Exception as e:
    #                     logger.info(e)
    #                     pass

    # ------------// Common Functions //------------
    def check_shotgrid(self):
        logger.info(" - Getting Final list from Shotgrid")
        # Authenticate Shotgrid
        sg = credentials.get_credentials()

        # Find if project is restricted from clean up
        if sg.find(
            "Project",
            [["sg_code", "is", self.target_project], ["sg_auto_cleanup", "is", False]],
        ):
            return

        # Find Shots marked for Breakdown
        for shot in sg.find(
            "Shot",
            [
                ["project.Project.sg_code", "is", self.target_project],
                ["sg_shots_breakdown", "is", True],
            ],
            ["code"],
        ):
            self.breakdown_shots.append(shot["code"])

        # Find Shots that have been finaled
        filter = [
            ["project.Project.sg_code", "is", self.target_project],
            ["sg_status_list", "in", ["snt", "fin"]],
        ]
        fields = [
            "code",
            "sg_delivery_name",
            "sg_final_version",
            "entity",
            # "entity.Shot.sg_delivery_name",  # TODO
            "entity.Shot.sg_shots_breakdown",
            "sg_status_list",
        ]
        finished_shots = sg.find("Version", filter, fields)

        for version in finished_shots:
            shot_name = version["entity"]["name"]
            delivery_name = version["entity.Shot.sg_delivery_name"] or None

            if version["sg_status_list"] == "fin":
                if shot_name not in self.finaled_shots.keys():
                    self.finaled_shots[shot_name] = {
                        "final": [],
                        "delivery_name": delivery_name,
                    }
                self.finaled_shots[shot_name]["final"].append(
                    "/".join(version["code"])
                )
            elif version["sg_status_list"] == "snt":
                if shot_name not in self.sent_versions.keys():
                    self.sent_versions[shot_name] = []
                self.sent_versions[shot_name].append(
                    "/".join(version["code"])
                )

            """
            # Add to main dictionary
            if not version['code'].split('_')[-2] == 'matte':
                self.finaled_shots[shot_name]['final'].append('/'.join(version['code'].split('_')[-2:]))
            """

    # def io_folder(self, delete=False):
    #     deliveries = []
    #     for shot in self.finaled_shots:
    #         if self.finaled_shots[shot]["delivery_name"]:
    #             deliveries.append(self.finaled_shots[shot]["delivery_name"])
    #         else:
    #             deliveries.append(shot)

    #     for folder in ["incoming", "outgoing", "delivery", "outsource"]:
    #         target = "{0}/{1}/io/{2}".format(
    #             const.PROJECTS_DIR, self.target_project, folder
    #         )

    #         if os.path.exists(target):
    #             logger.info(" - Scanning {0} folder".format(folder[1:].capitalize()))
    #         else:
    #             logger.info(" - {0} folder does not exist".format(folder[1:].capitalize()))
    #             continue

    #         total_size = 0
    #         if delete:
    #             # Add entire folder
    #             self.marked_to_remove["paths"].append(target)
    #         else:
    #             shot_regex = ".*[a-zA-Z0-9]*._[a-zA-Z0-9]*._[a-zA-Z0-9]*"
    #             for root, dirs, files in os.walk(target):
    #                 # Skip folders newer than 5 days
    #                 try:
    #                     string_date = root.split(target)[-1].split("/")[1]
    #                     publish_date = datetime.datetime.strptime(string_date, "%Y%m%d")
    #                     if publish_date > (TODAY - datetime.timedelta(days=7)):
    #                         continue
    #                 except:
    #                     # Skip folder if the date can't be determined
    #                     continue

    #                 parent = os.path.basename(root)
    #                 if not dirs and not files:
    #                     # If root folder is empty, delete it.
    #                     if root not in self.marked_to_remove["paths"]:
    #                         self.marked_to_remove["paths"].append(root)

    #                 # Check if path contains shot naming convention
    #                 if re.search(shot_regex, root.split(target)[-1]):
    #                     # Find shots within the folder names
    #                     if "_".join(parent.split("_")[:3]) in deliveries:
    #                         if root not in self.marked_to_remove["paths"]:
    #                             self.marked_to_remove["paths"].append(root)
    #                     elif "_".join(parent.split("_")[:4]) in deliveries:
    #                         if root not in self.marked_to_remove["paths"]:
    #                             self.marked_to_remove["paths"].append(root)
    #                     else:
    #                         # Parse through filepath to find the shot name
    #                         sub_path = root.split(target)[-1]
    #                         for segment in sub_path.split("/"):
    #                             if re.search(shot_regex, segment):
    #                                 if "_".join(segment.split("_")[:3]) in deliveries:
    #                                     if root not in self.marked_to_remove["paths"]:
    #                                         self.marked_to_remove["paths"].append(root)
    #                                 elif "_".join(segment.split("_")[:4]) in deliveries:
    #                                     if root not in self.marked_to_remove["paths"]:
    #                                         self.marked_to_remove["paths"].append(root)
    #                 else:
    #                     # Find shots within filenames
    #                     for filename in files:
    #                         if re.search(shot_regex, filename):
    #                             fn = os.path.join(root, filename)
    #                             if "_".join(filename.split("_")[:3]) in deliveries:
    #                                 self.marked_to_remove["paths"].append(fn)
    #                             elif "_".join(filename.split("_")[:4]) in deliveries:
    #                                 self.marked_to_remove["paths"].append(fn)

    # ------------// Active Projects Cleaning //------------
    def clean_old_publishes(self, path_regex):
        logger.info(" - Scanning old comps")
        # TODO: use OP
        # target = "{0}/{1}".format(const.PROJECTS_DIR, self.target_project)

        # for dirpath, dirnames, _ in os.walk(target):
        #     # Skip all folders that aren't within a 'work' directory
        #     if "/publish" not in dirpath:
        #         continue
        #     # versions = sorted(glob(path + "/*"))
        #     # shot = "_".join(path.split("/")[5:8])
        #     if shot in self.finaled_shots.keys() and len(versions) > 1:
        #         for version in versions[:-3]:
        #             ver = "/".join(version.split("/")[-2:])
        #             if ver not in self.finaled_shots[shot]["final"]:
        #                 self.marked_to_remove["paths"].append(version)

        #             temp_files = glob("{0}/*/*".format(ver))
        #             for temp_file in temp_files:
        #                 if temp_file.endswith(".mov"):
        #                     base_dir = os.path.dirname(temp_file)
        #                     self.marked_to_remove["paths"].append(base_dir)
        #                 elif temp_file.endswith(".exr"):
        #                     self.marked_to_remove["paths"].append(temp_file)

    def clean_old_files(self):
        logger.info(" - Scanning 7 days old work files")
        target = "{0}/{1}".format(const.PROJECTS_DIR, self.target_project)

        now = time.time()
        folders_to_remove = {
            "ass",
            "backup",
            "cache",
            "ifd",
            "ifds",
            "img",
            "render",
        }
        files_to_remove = {
            ".*.nk~",
            ".*nk_history",
            ".*nk.autosave.*",
        }

        for dirpath, dirnames, filenames in os.walk(target):
            # Skip all folders that aren't within a 'work' directory
            if "/work" not in dirpath:
                continue

            for folder in folders_to_remove:
                if folder in dirnames:
                    folder_path = os.path.join(dirpath, folder)
                    versions = sorted(glob(folder_path + "/*"))
                    for ver in versions:
                        if os.stat(ver).st_mtime < (now - 7 * 86400):
                            self.marked_to_remove["paths"].append(ver)

            for pattern in files_to_remove:
                for filename in fnmatch.filter(filenames, pattern):
                    filepath = os.path.join(dirpath, filename)
                    if os.stat(filepath).st_mtime < (now - 7 * 86400):
                        self.marked_to_remove["paths"].append(
                            filepath
                        )

    # ------------// Archival Functions //------------
    def match_list(self, path, pattern_list):
        for pattern in pattern_list:
            if re.search(pattern, path):
                return True
        return False

    # def clean_shots(self):

        # Publishes
        # logger.info(" - Cleaning published renders")
        # for path in glob("{0}/{1}/publish/render/*".format(target, render_regex)):
        #     versions = sorted(glob("{0}/v[0-9][0-9]*".format(path)))
        #     if len(versions) > 2:
        #         self.marked_to_remove["paths"] += versions[:-2]
        #     for version in versions[-2:]:
        #         for dir in ["2K_Proxy", "ReviewQT"]:
        #             if dir in os.listdir(version):
        #                 self.marked_to_remove["paths"].append("/".join([version, dir]))

        # # Plate Proxies
        # logger.info(" - Cleaning plate proxies")
        # for path in glob(
        #     "{0}/{1}/_plates/*/v[0-9][0-9]*/[a-z, A-Z]*".format(target, shot_regex)
        # ):
        #     if os.path.basename(path) in ["2K_Proxy", "JPEG_Proxy", "ReviewQT"]:
        #         self.marked_to_remove["paths"].append(path)

    # def clean_assets(self):
    #     logger.info(" // Assets //")
    #     target = "{0}/{1}".format(const.PROJECTS_DIR, self.target_project)
    #     render_regex = "[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*"

    #     nuke_trash = ["nuke/.*.nk~", "nuke/.*nk_history", "nuke/.*nk.autosave.*"]
    #     houdini_trash = [
    #         "houdini/img",
    #         "houdini/scenes/backup",
    #         "houdini/cache",
    #         "houdini/ifd",
    #         "houdini/scenes/geo",
    #         "houdini/scenes/ifds",
    #         "houdini/vrscene",
    #     ]
    #     maya_trash = ["nCache", "maya/images", "playblasts", "maya/movies"]

    #     # Work Files
    #     logger.info(" - Cleaning work files")
    #     for path in sorted(glob("{0}/{1}/work/*".format(target, render_regex))):
    #         for app_dir in glob(path + "/*"):
    #             """
    #             App Level. Each result here is an app folder.
    #             """
    #             app = path.split("/work/")[-1].split("/")[0]

    #             if app == "nuke":
    #                 if self.match_list(app_dir, nuke_trash):
    #                     self.marked_to_remove["paths"].append(app_dir)

    #             if app == "houdini":
    #                 for sub_app in glob(app_dir + "/*"):
    #                     if self.match_list(sub_app, houdini_trash):
    #                         self.marked_to_remove["paths"].append(sub_app)

    #             if app == "maya":
    #                 for sub_app in glob(app_dir + "/*"):
    #                     if self.match_list(sub_app, maya_trash):
    #                         self.marked_to_remove["paths"].append(sub_app)

    #     # Publishes
    #     logger.info(" - Cleaning published renders")
    #     for path in glob("{0}/{1}/publish/render/*".format(target, render_regex)):
    #         versions = sorted(glob("{0}/v[0-9][0-9]*".format(path)))
    #         if len(versions) > 2:
    #             self.marked_to_remove["paths"] += versions[:-2]

    #         for version in versions[-2:]:
    #             for dir in ["2K_Proxy", "ReviewQT"]:
    #                 if dir in os.listdir(version):
    #                     self.marked_to_remove["paths"].append("/".join([version, dir]))

    # def move_for_archive(self):
    #     logger.info(" - Moving Shots to Archive Directory")
    #     target = "{0}/{1}".format(const.PROJECTS_DIR, self.target_project)
    #     shot_regex = "[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z, _]*"
    #     asset_regex = "_assets/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*"

    #     # Shots
    #     for path in glob("{0}/{1}".format(target, shot_regex)):
    #         self.archive_path(path)

    #     # Assets
    #     for path in glob("{0}/{1}".format(target, asset_regex)):
    #         self.archive_path(path)

    #     # Move remaining sequence folders
    #     for path in glob("{0}/[0-9, a-z, A-Z]*/_*".format(target)):
    #         self.archive_path(path)

    #     # Move project base folders
    #     for path in glob("{0}/_*".format(target)):
    #         self.archive_path(path)

    #     # Rsync the entire project to ensure everything is copied
    #     self.archive_path(target, rsync=True)

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

    def compress_workfiles(self):
        target = "{0}/{1}".format(const.ARCHIVE_DIR, self.target_project)

        # Shots
        logger.info(" - Compressing shot work files")
        render_regex = (
            "[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*"  # JAP
        )
        for path in sorted(glob("{0}/{1}/work".format(target, render_regex))):
            if const._debug:
                logger.info(" + Dry compress --{0}".format("/".join(path.split("/")[-5:-1])))
            else:
                logger.info(" + {0}".format("/".join(path.split("/")[-5:-1])))
                os.system("cd {0} &&zip -0rmT work work".format(os.path.dirname(path)))

        # Assets
        logger.info(" - Compressing asset work files")
        render_regex = "[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*/[0-9, a-z, A-Z]*"
        for path in glob("{0}/_assets/{1}/work".format(target, render_regex)):
            if const._debug:
                logger.info(" + Dry compress --{0}".format("/".join(path.split("/")[-5:-1])))
            else:
                logger.info(" + {0}".format("/".join(path.split("/")[-3:-1])))
                os.system("cd {0} &&zip -0rmT work work".format(os.path.dirname(path)))
