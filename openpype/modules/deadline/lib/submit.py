import os
import requests
import re
import getpass
import json

from openpype.lib import Logger
from openpype.lib import is_running_from_build
from openpype.pipeline import legacy_io


logger = Logger.get_logger(__name__)


PRIORITY = 50
CHUNK_SIZE = 9999
CONCURRENT_TASKS = 1
GROUP = "nuke-cpu-epyc"
DEPARTMENT = "Editorial"

DEADLINE_URL = ""

PLUGINS_DATA_MAP = {
    "Nuke":

}


def payload_submit(
    instance,
    render_path,
    out_framerange,
    plugin,
    executable=None,
    args=None,
    comment=None,
    extra_env=None,
    response_data=None,
):
    render_dir = os.path.normpath(os.path.dirname(render_path))
    jobname = "%s - %s" % (render_dir, os.path.basename(render_path))

    output_filename_0 = preview_fname(render_path)

    if not response_data:
        response_data = {}

    try:
        # Ensure render folder exists
        os.makedirs(render_dir)
    except OSError:
        pass

    payload = {
        "JobInfo": {
            # Top-level group name
            "BatchName": render_dir,
            # Job name, as seen in Monitor
            "Name": jobname,
            # Arbitrary username, for visualisation in Monitor
            "UserName": getpass.getuser(),
            "Priority": PRIORITY,
            "ChunkSize": CHUNK_SIZE,
            "ConcurrentTasks": CONCURRENT_TASKS,
            "Department": DEPARTMENT,
            "Pool": None,
            "SecondaryPool": None,
            "Group": GROUP,
            "Plugin": plugin,
            "Frames": f"{out_framerange[0]}-{out_framerange[1]}",
            "Comment": comment,
            # Optional, enable double-click to preview rendered
            # frames from Deadline Monitor
            "OutputFilename0": output_filename_0.replace("\\", "/"),
        },
        "PluginInfo": {
            # Output directory and filename
            "OutputFilePath": render_dir.replace("\\", "/"),
            # Resolve relative references
            "AWSAssetFile0": render_path,
        },
        # Mandatory for Deadline, may be empty
        "AuxFiles": [],
    }

    plugin_overrides = {}
    if plugin == "Nuke":
        plugin_overrides = {
            "ScriptJob": True,
            "ScriptFilename": self.nuke_transcode_py,
            "SceneFile": self.nuke_transcode_py,
            "Version": self._ver,
            "UseGpu": False,
        }

    elif plugin == "CommandLine":
        plugin_overrides = {
            "Executable": executable,
            "Arguments": args,
            "UseGpu": False,
            "WorkingDirectory": render_dir,
        }

    # Update plugin info with overrides
    payload["PluginInfo"].update(plugin_overrides)

    if response_data.get("_id"):
        payload["JobInfo"].update(
            {
                "JobType": "Normal",
                "BatchName": response_data["Props"]["Batch"],
                "JobDependency0": response_data["_id"],
                "ChunkSize": 99999999,
            }
        )

    # Include critical environment variables with submission
    keys = [
        "AVALON_APP_NAME",
        "AVALON_ASSET",
        "AVALON_PROJECT",
        "AVALON_TASK",
        "FOUNDRY_LICENSE",
        "FTRACK_API_KEY",
        "FTRACK_API_USER",
        "FTRACK_SERVER",
        "NUKE_PATH",
        "OPENPYPE_SG_USER",
        "PATH",
        "PYBLISHPLUGINPATH",
        "PYTHONPATH",
        "TOOL_ENV",
        "OCIO",
    ]

    # Add OpenPype version if we are running from build.
    if is_running_from_build():
        keys.append("OPENPYPE_VERSION")

    # Add mongo url if it's enabled
    if instance.context.data.get("deadlinePassMongoUrl"):
        keys.append("OPENPYPE_MONGO")

    environment = dict(
        {key: os.environ[key] for key in keys if key in os.environ},
        **legacy_io.Session,
    )

    for _path in os.environ:
        if _path.lower().startswith("openpype_"):
            environment[_path] = os.environ[_path]

    if extra_env:
        environment.update(extra_env)

    # to recognize job from PYPE for turning Event On/Off
    environment["OPENPYPE_RENDER_JOB"] = "1"

    payload["JobInfo"].update(
        {
            "EnvironmentKeyValue%d"
            % index: "{key}={value}".format(
                key=key, value=environment[key]
            )
            for index, key in enumerate(environment)
        }
    )

    plugin = payload["JobInfo"]["Plugin"]
    logger.info("using render plugin : {}".format(plugin))

    logger.info("Submitting..")
    logger.info(json.dumps(payload, indent=4, sort_keys=True))

    # # adding expected files to instance.data
    # self.expected_files(
    #     instance,
    #     render_path,
    #     out_framerange[0],
    #     out_framerange[1]
    )

    logger.debug(
        "__ expectedFiles: `{}`".format(instance.data["expectedFiles"])
    )
    response = requests.post(DEADLINE_URL, json=payload, timeout=10)

    if not response.ok:
        raise Exception(response.text)

    return response


def preview_fname(path):
    """Return output file path with #### for padding.

    Deadline requires the path to be formatted with # in place of numbers.
    For example `/path/to/render.####.png`

    Args:
        path (str): path to rendered images

    Returns:
        str

    """
    logger.debug("_ path: `{}`".format(path))
    if "%" in path:
        hashes_path = re.sub(
            r"%(\d*)d", lambda m: "#" * int(m.group(1)) if m.group(1) else "#", path
        )
        return hashes_path

    if "#" in path:
        logger.debug("_ path: `{}`".format(path))

    return path
