"""Utility functions for delivery module.

Most of these are just copy pastes from OpenPype plugins. The problem was
that a lot of those plugin functions can't be called directly without
quite a bit of refactoring. In the future we should abstract those functions
in the plugins so they can be reused elsewhere.
"""
import clique
import getpass
import os
import requests

import shotgun_api3

from openpype.lib import Logger
from openpype.pipeline import Anatomy
from openpype.pipeline.colorspace import get_imageio_config
from openpype.modules.shotgrid.lib.settings import get_shotgrid_servers

logger = Logger.get_logger(__name__)


def get_shotgrid_session():
    """Return a Shotgun API session object for the configured ShotGrid server.

    The function reads the ShotGrid server settings from the OpenPype
    configuration file and uses them to create a Shotgun API session object.

    Returns:
        A Shotgun API session object.
    """
    shotgrid_servers_settings = get_shotgrid_servers()
    logger.info("shotgrid_servers_settings: {}".format(shotgrid_servers_settings))

    shotgrid_server_setting = shotgrid_servers_settings.get("alkemyx", {})
    shotgrid_url = shotgrid_server_setting.get("shotgrid_url", "")

    shotgrid_script_name = shotgrid_server_setting.get("shotgrid_script_name", "")
    shotgrid_script_key = shotgrid_server_setting.get("shotgrid_script_key", "")
    if not shotgrid_script_name and not shotgrid_script_key:
        logger.error(
            "No Shotgrid API credential found, please enter "
            "script name and script key in OpenPype settings"
        )

    proxy = os.environ.get("HTTPS_PROXY", "").lstrip("https://")
    return shotgun_api3.Shotgun(
        shotgrid_url,
        script_name=shotgrid_script_name,
        api_key=shotgrid_script_key,
        http_proxy=proxy,
    )


def create_metadata_path(instance_data):
    # Ensure output dir exists
    output_dir = instance_data.get(
        "publishRenderMetadataFolder", instance_data["outputDir"]
    )

    try:
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
    except OSError:
        # directory is not available
        logger.warning("Path is unreachable: `{}`".format(output_dir))

    metadata_filename = "{}_{}_metadata.json".format(
        instance_data["asset"], instance_data["subset"]
    )

    return os.path.join(output_dir, metadata_filename)


def get_representations(instance_data, exp_files, do_not_add_review):
    """Create representations for file sequences.

    This will return representations of expected files if they are not
    in hierarchy of aovs. There should be only one sequence of files for
    most cases, but if not - we create representation from each of them.

    Arguments:
        instance_data (dict): instance["data"] for which we are
                            setting representations
        exp_files (list): list of expected files
        do_not_add_review (bool): explicitly skip review

    Returns:
        list of representations

    """
    representations = []
    collections, _ = clique.assemble(exp_files)

    anatomy = Anatomy(instance_data["project"])

    # create representation for every collected sequence
    for collection in collections:
        ext = collection.tail.lstrip(".")
        preview = True

        staging = os.path.dirname(list(collection)[0])
        success, rootless_staging_dir = anatomy.find_root_template_from_path(staging)
        if success:
            staging = rootless_staging_dir
        else:
            logger.warning(
                (
                    "Could not find root path for remapping '{}'."
                    " This may cause issues on farm."
                ).format(staging)
            )

        frame_start = int(instance_data.get("frameStartHandle"))
        if instance_data.get("slate"):
            frame_start -= 1

        preview = preview and not do_not_add_review
        rep = {
            "name": ext,
            "ext": ext,
            "files": [os.path.basename(f) for f in list(collection)],
            "frameStart": frame_start,
            "frameEnd": int(instance_data.get("frameEndHandle")),
            # If expectedFile are absolute, we need only filenames
            "stagingDir": staging,
            "fps": instance_data.get("fps"),
            "tags": ["review", "shotgridreview"] if preview else [],
        }

        if instance_data.get("multipartExr", False):
            rep["tags"].append("multipartExr")

        # support conversion from tiled to scanline
        if instance_data.get("convertToScanline"):
            logger.info("Adding scanline conversion.")
            rep["tags"].append("toScanline")

        representations.append(rep)

        _solve_families(instance_data, preview)

    return representations


def get_colorspace_settings(project_name):
    """Returns colorspace settings for project.

    Returns:
        tuple | bool: config, file rules or None
    """
    config_data = get_imageio_config(
        project_name,
        host_name=None,
    )

    # in case host color management is not enabled
    if not config_data:
        return None

    return config_data


def set_representation_colorspace(
    representation,
    project_name,
    colorspace=None,
):
    """Sets colorspace data to representation.

    Args:
        representation (dict): publishing representation
        project_name (str): Name of project
        config_data (dict): host resolved config data
        file_rules (dict): host resolved file rules data
        colorspace (str, optional): colorspace name. Defaults to None.

    Example:
        ```
        {
            # for other publish plugins and loaders
            "colorspace": "linear",
            "config": {
                # for future references in case need
                "path": "/abs/path/to/config.ocio",
                # for other plugins within remote publish cases
                "template": "{project[root]}/path/to/config.ocio"
            }
        }
        ```

    """
    ext = representation["ext"]
    # check extension
    logger.debug("__ ext: `{}`".format(ext))

    config_data = _get_colorspace_settings(project_name)

    if not config_data:
        # warn in case no colorspace path was defined
        logger.warning("No colorspace management was defined")
        return

    logger.debug("Config data is: `{}`".format(config_data))

    # infuse data to representation
    if colorspace:
        colorspace_data = {"colorspace": colorspace, "config": config_data}

        # update data key
        representation["colorspaceData"] = colorspace_data


def solve_families(instance, preview=False):
    families = instance.get("families")

    # if we have one representation with preview tag
    # flag whole instance for review and for ftrack
    if preview:
        if "review" not in families:
            logger.debug('Adding "review" to families because of preview tag.')
            families.append("review")
        if "client_review" not in families:
            logger.debug('Adding "client_review" to families because of preview tag.')
            families.append("client_review")
        instance["families"] = families


def expected_files(instance_data, path, out_frame_start, out_frame_end):
    """Create expected files in instance data"""
    if not instance_data.get("expectedFiles"):
        instance_data["expectedFiles"] = []

    dirname = os.path.dirname(path)
    filename = os.path.basename(path)

    if "#" in filename:
        pparts = filename.split("#")
        padding = "%0{}d".format(len(pparts) - 1)
        filename = pparts[0] + padding + pparts[-1]

    if "%" not in filename:
        instance_data["expectedFiles"].append(path)
        return

    for i in range(out_frame_start, (out_frame_end + 1)):
        instance_data["expectedFiles"].append(
            os.path.join(dirname, (filename % i)).replace("\\", "/")
        )


def submit_deadline_post_job(
    instance_data, job, instances, output_dir, deadline_url, metadata_path
):
    """Submit publish job to Deadline.

    Deadline specific code separated from :meth:`process` for sake of
    more universal code. Muster post job is sent directly by Muster
    submitter, so this type of code isn't necessary for it.

    Returns:
        (str): deadline_publish_job_id
    """
    subset = instance_data["subset"]
    job_name = "Publish - {subset}".format(subset=subset)

    # instance_data.get("subset") != instances[0]["subset"]
    # 'Main' vs 'renderMain'
    override_version = None
    instance_version = instance_data.get("version")  # take this if exists
    if instance_version != 1:
        override_version = instance_version

    # Transfer the environment from the original job to this dependent
    # job so they use the same environment
    # metadata_path = create_metadata_path(instance_data)
    # logger.info("Metadata path: %s", metadata_path)
    username = getpass.getuser()

    environment = {
        "AVALON_PROJECT": instance_data.get("project"),
        "AVALON_ASSET": instance_data.get("asset"),
        "AVALON_TASK": instance_data.get("task"),
        "OPENPYPE_USERNAME": username,
        "OPENPYPE_PUBLISH_JOB": "1",
        "OPENPYPE_RENDER_JOB": "0",
        "OPENPYPE_REMOTE_JOB": "0",
        "OPENPYPE_LOG_NO_COLORS": "1",
        "OPENPYPE_SG_USER": username,
    }

    args = [
        "--headless",
        "publish",
        '"{}"'.format(metadata_path),
        "--targets",
        "deadline",
        "--targets",
        "farm",
    ]

    # Generate the payload for Deadline submission
    payload = {
        "JobInfo": {
            "Plugin": "OpenPype",
            "BatchName": job["Props"]["Batch"],
            "Name": job_name,
            "UserName": job["Props"]["User"],
            "Comment": instance_data.get("comment", ""),
            "Department": "",
            "ChunkSize": 1,
            "Priority": 50,
            "Group": "nuke-cpu-epyc",
            "Pool": "",
            "SecondaryPool": "",
            # ensure the outputdirectory with correct slashes
            "OutputDirectory0": output_dir.replace("\\", "/"),
        },
        "PluginInfo": {
            "Version": os.getenv("OPENPYPE_VERSION"),
            "Arguments": " ".join(args),
            "SingleFrameOnly": "True",
        },
        # Mandatory for Deadline, may be empty
        "AuxFiles": [],
    }

    if instance_data.get("suspend_publish"):
        payload["JobInfo"]["InitialStatus"] = "Suspended"

    for index, (key_, value_) in enumerate(environment.items()):
        payload["JobInfo"].update(
            {
                "EnvironmentKeyValue%d"
                % index: "{key}={value}".format(key=key_, value=value_)
            }
        )
    # remove secondary pool
    payload["JobInfo"].pop("SecondaryPool", None)

    logger.info("Submitting Deadline job ...")
    logger.debug("Payload: %s", payload)

    url = "{}/api/jobs".format(deadline_url)
    response = requests.post(url, json=payload, timeout=10)
    if not response.ok:
        raise Exception(response.text)

    deadline_publish_job_id = response.json()["_id"]
    logger.info(deadline_publish_job_id)

    return deadline_publish_job_id
