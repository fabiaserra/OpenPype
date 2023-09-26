import os
import requests
import re
import getpass
import json

from openpype.lib import Logger
from openpype.lib import is_running_from_build
from openpype.pipeline import legacy_io

from openpype.modules.deadline import constants


logger = Logger.get_logger(__name__)

# Default Deadline job
DEFAULT_PRIORITY = 50
DEFAULT_CHUNK_SIZE = 9999
DEAFAULT_CONCURRENT_TASKS = 1

# Dictionary that maps each Deadline plugin with the required arguments
# for the plugin that will be passed as plugin_data dictionary
PLUGINS_DATA_MAP = {
    "AxNuke": [
        "ScriptJob",
        "ScriptFilename",
        "SceneFile",
        "Version",
        "UseGpu",
    ],
    "CommandLine": [
        "Executable",
        "Arguments",
        "UseGpu",
        "WorkingDirectory",
    ]
}


def payload_submit(
    render_path,
    out_framerange,
    plugin,
    plugin_data,
    batch_name,
    task_name,
    group="",
    comment="",
    priority=DEFAULT_PRIORITY,
    chunk_size=DEFAULT_CHUNK_SIZE,
    concurrent_tasks=DEAFAULT_CONCURRENT_TASKS,
    department="",
    extra_env=None,
    response_data=None,
):
    if not response_data:
        response_data = {}

    render_dir = os.path.normpath(os.path.dirname(render_path))
    try:
        # Ensure render folder exists
        os.makedirs(render_dir)
    except OSError:
        pass

    payload = {
        "JobInfo": {
            # Top-level group name
            "BatchName": batch_name,
            # Job name, as seen in Monitor
            "Name": task_name,
            # Arbitrary username, for visualisation in Monitor
            "UserName": getpass.getuser(),
            "Priority": priority,
            "ChunkSize": chunk_size,
            "ConcurrentTasks": concurrent_tasks,
            "Department": department,
            "Pool": "",
            "SecondaryPool": "",
            "Group": group,
            "Plugin": plugin,
            "Frames": f"{out_framerange[0]}-{out_framerange[1]}",
            "Comment": comment or "",
            # Optional, enable double-click to preview rendered
            # frames from Deadline Monitor
            "OutputFilename0": preview_fname(render_path).replace("\\", "/"),
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

    # Set plugin overrides data from plugin data
    plugin_overrides = {}
    for key in PLUGINS_DATA_MAP[plugin]:
        if plugin_data.get(key):
            plugin_overrides[key] = plugin_data[key]

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

    url = "{}/api/jobs".format(constants.DEADLINE_URL)
    response = requests.post(url, json=payload, timeout=10)

    if not response.ok:
        raise Exception(response.text)

    return response.json()


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
