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


def payload_submit(
    plugin,
    plugin_data,
    batch_name,
    task_name,
    group="",
    comment="",
    priority=DEFAULT_PRIORITY,
    chunk_size=DEFAULT_CHUNK_SIZE,
    concurrent_tasks=DEAFAULT_CONCURRENT_TASKS,
    frame_range=None,
    department="",
    extra_env=None,
    response_data=None,
):
    if not response_data:
        response_data = {}

    frames = "0" if not frame_range else f"{frame_range[0]}-{frame_range[1]}"

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
            "Frames": frames,
            "Comment": comment or "",
            # Optional, enable double-click to preview rendered
            # frames from Deadline Monitor
            # "OutputFilename0": preview_fname(render_path).replace("\\", "/"),
        },
        "PluginInfo": plugin_data,
        # Mandatory for Deadline, may be empty
        "AuxFiles": [],
    }

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
        "AVALON_ASSET",
        "AVALON_TASK",
        "AVALON_PROJECT",
        "AVALON_APP_NAME",
        "OCIO",
        "USER",
        "OPENPYPE_SG_USER",
    ]

    # Add OpenPype version if we are running from build.
    if is_running_from_build():
        keys.append("OPENPYPE_VERSION")

    environment = dict(
        {key: os.environ[key] for key in keys if key in os.environ},
        **legacy_io.Session,
    )

    if extra_env:
        environment.update(extra_env)

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
