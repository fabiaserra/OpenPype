"""Module for handling OP delivery of Shotgrid playlists"""
import os
import copy
import collections
import click
import tqdm
import clique
import getpass
import json
import requests

import shotgun_api3

from openpype.client import (
    get_project,
    get_version_by_id,
    get_representations,
    get_representation_by_name,
)
from openpype.lib import Logger, collect_frames, get_datetime_data
from openpype.pipeline import Anatomy, legacy_io
from openpype.pipeline.load import get_representation_path_with_anatomy
from openpype.pipeline.delivery import (
    check_destination_path,
    deliver_single_file,
)
from openpype.pipeline.colorspace import get_imageio_config
from openpype.modules.shotgrid.lib.settings import get_shotgrid_servers
from openpype.settings import get_system_settings

logger = Logger.get_logger(__name__)

# List of SG fields from context entities (i.e., Project, Shot) that we care to
# query for delivery purposes
SG_DELIVERY_FIELDS = [
    "sg_delivery_name",
    "sg_final_output_type",
    "sg_review_output_type",
]


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


@click.command("deliver_playlist")
@click.option(
    "--playlist_id",
    "-p",
    required=True,
    type=int,
    help="Shotgrid playlist id to deliver.",
)
@click.option("--representation_names", "-r", multiple=True, required=False)
@click.option(
    "--delivery_types",
    "-types",
    type=click.Choice(["final", "review"]),
    required=False,
    multiple=True,
)
def deliver_playlist_command(
    playlist_id,
    representation_names=None,
    delivery_types=None,
):
    """Given a SG playlist id, deliver all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to deliver.
        delivery_template_name (str): Name of the delivery template to use.
        representation_names (list): List of representation names to deliver.
    """
    return deliver_playlist(playlist_id, representation_names, delivery_types)


def deliver_playlist(
    playlist_id,
    representation_names=None,
    delivery_types=None,
    delivery_templates=None,
):
    """Given a SG playlist id, deliver all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to deliver.
        representation_names (list): List of representation names to deliver.
        delivery_type (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        delivery_templates (dict[str, str]): Dictionary that maps different
            delivery types (i.e., 'single_file', 'sequence') to the corresponding
            templated string to use for delivery.
    """
    report_items = collections.defaultdict(list)

    sg = get_shotgrid_session()

    sg_playlist = sg.find_one(
        "Playlist",
        [
            ["id", "is", int(playlist_id)],
        ],
        ["project"],
    )

    # Get the project name associated with the selected entities
    project_name = sg_playlist["project"]["name"]

    project_doc = get_project(project_name, fields=["name"])
    if not project_doc:
        return report_items[f"Didn't find project '{project_name}' in avalon."], False

    # Get whether the project entity contains any delivery overrides
    sg_project = sg.find_one(
        "Project",
        [["id", "is", sg_playlist["project"]["id"]]],
        fields=SG_DELIVERY_FIELDS,
    )
    delivery_project_name = sg_project.get("sg_delivery_name")

    if not representation_names:
        representation_names = []

    # Generate a list of representation names from the output types set in SG
    for delivery_type in delivery_types:
        out_data_types = sg_project.get(f"sg_{delivery_type}_output_type")
        for out_data_type in out_data_types:
            representation_name = "{}_{}".format(
                out_data_type["name"].replace(" ", "").lower(),
                delivery_type,
            )
            representation_names.append(representation_name)

        # Add 'review' and 'final' as representation names as we want to deliver
        # those in some cases. If 'delete output' tag is added on the Extract OIIO
        # Transcode plugin, these representations won't exist but that doesn't matter
        representation_names.append(delivery_type)

    if representation_names:
        msg = "Delivering representation names:"
        logger.info("%s: %s", msg, representation_names)
        report_items[msg] = representation_names
    else:
        msg = "No representation names specified: "
        sub_msg = "All representations will be delivered."
        logger.info(msg + sub_msg)
        report_items[msg] = sub_msg

    # Get all the SG versions associated to the playlist
    sg_versions = sg.find(
        "Version",
        [["playlists", "in", sg_playlist]],
        ["sg_op_instance_id", "entity", "code"],
    )

    # Create dictionary of inputs required by deliver_sg_version
    delivery_data = {
        "date": get_datetime_data(),
        "delivery_project_name": delivery_project_name,
    }

    # Iterate over each SG version and deliver it
    success = True
    for sg_version in tqdm.tqdm(sg_versions):
        new_report_items, new_success = deliver_sg_version(
            sg_version,
            project_name,
            delivery_data,
            representation_names,
            delivery_templates,
        )
        if new_report_items:
            report_items.update(new_report_items)

        if not new_success:
            success = False

    click.echo(report_items)
    return report_items, success


def deliver_sg_version(
    sg_version,
    project_name,
    delivery_data,
    representation_names=None,
    delivery_templates=None,
):
    """Deliver a single SG version.

    Args:
        sg_version (): Shotgrid Version object to deliver.
        project_name (str): Name of the project corresponding to the version being
            delivered.
        delivery_data (dict[str, str]): Dictionary of relevant data for delivery.
        representation_names (list): List of representation names to deliver.
        delivery_type (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        delivery_templates (dict[str, str]): Dictionary that maps different
            delivery types (i.e., 'single_file', 'sequence') to the corresponding
            templated string to use for delivery.
    """
    report_items = collections.defaultdict(list)

    # Grab the OP's id corresponding to the SG version
    op_version_id = sg_version["sg_op_instance_id"]
    if not op_version_id or op_version_id == "-":
        sub_msg = f"{sg_version['code']}<br>"
        report_items["Missing 'sg_op_instance_id' field on SG Versions"].append(sub_msg)
        return report_items, False

    anatomy = Anatomy(project_name)

    # Get the corresponding shot and whether it contains any overrides
    sg = get_shotgrid_session()
    sg_shot = sg.find_one(
        "Shot",
        [["id", "is", sg_version["entity"]["id"]]],
        fields=SG_DELIVERY_FIELDS,
    )
    delivery_shot_name = sg_shot.get("sg_delivery_name")

    # Find the OP representations we want to deliver
    repres_to_deliver = list(
        get_representations(
            project_name,
            representation_names=representation_names,
            version_ids=[op_version_id],
        )
    )
    for repre in repres_to_deliver:
        source_path = repre.get("data", {}).get("path")
        debug_msg = "Processing representation {}".format(repre["_id"])
        if source_path:
            debug_msg += " with published path {}.".format(source_path)
        click.echo(debug_msg)

        # Get source repre path
        frame = repre["context"].get("frame")

        if frame:
            repre["context"]["frame"] = len(str(frame)) * "#"

        # If delivery templates dictionary is passed as an argument, use that to set the
        # template token for the representation.
        delivery_template_name = None
        if delivery_templates:
            if frame:
                template_name = "{}Sequence".format(
                    "V0 " if repre["context"]["version"] == 0 else ""
                )
            else:
                template_name = "{}Single File".format(
                    "V0 " if repre["context"]["version"] == 0 else ""
                )

            logger.info(
                "Using template name '%s' for representation '%s'",
                template_name,
                repre["data"]["path"],
            )
            delivery_template = delivery_templates[template_name]

            # Make sure we prefix the template with the io folder for the project
            if delivery_template:
                delivery_template = (
                    f"/proj/{anatomy.project_code}/io/out/{delivery_template}"
                )

        else:  # Otherwise, set it based on whether it's a sequence or a single file
            if frame:
                delivery_template_name = "sequence"
            else:
                delivery_template_name = "single_file"

        anatomy_data = copy.deepcopy(repre["context"])

        # Set overrides if passed
        delivery_project_name = delivery_data.get("delivery_project_name")
        if delivery_project_name:
            logger.info(
                "Project name '%s' overridden by '%s'.",
                anatomy_data["project"]["name"],
                delivery_project_name,
            )
            anatomy_data["project"]["name"] = delivery_project_name

        if delivery_shot_name:
            logger.info(
                "Shot '%s' name overridden by '%s'.",
                anatomy_data["asset"],
                delivery_shot_name,
            )
            anatomy_data["asset"] = delivery_shot_name

        logger.debug(anatomy_data)

        repre_report_items, dest_path = check_destination_path(
            repre["_id"],
            anatomy,
            anatomy_data,
            delivery_data.get("date"),
            delivery_template_name,
            delivery_template,
            return_dest_path=True,
        )

        if repre_report_items:
            return repre_report_items, False

        repre_path = get_representation_path_with_anatomy(repre, anatomy)

        args = [
            repre_path,
            repre,
            anatomy,
            delivery_template_name,
            anatomy_data,
            None,
            report_items,
            logger,
            delivery_template,
        ]
        src_paths = []
        for repre_file in repre["files"]:
            src_path = anatomy.fill_root(repre_file["path"])
            src_paths.append(src_path)
        sources_and_frames = collect_frames(src_paths)

        for src_path, frame in sources_and_frames.items():
            args[0] = src_path
            if frame:
                anatomy_data["frame"] = frame
            new_report_items, _ = deliver_single_file(*args)
            # If not new report items it means the delivery was successful
            # so we append it to the list of successful delivers
            if not new_report_items:
                report_items["Successful delivered representations"].append(
                    f"{repre_path} -> {dest_path}<br>"
                )
            report_items.update(new_report_items)

    return report_items, True


@click.command("republish_version")
@click.option(
    "--version_id",
    "-v",
    required=True,
    type=int,
    help="Shotgrid version id to republish.",
)
def republish_version_command(
    version_id,
):
    """Given a SG version id, republish it so it triggers the OP publish pipeline again.

    Args:
        version (int): Shotgrid version id to republish.
    """
    sg = get_shotgrid_session()

    sg_version = sg.find_one(
        "Version",
        [
            ["id", "is", int(version_id)],
        ],
        ["project", "sg_op_instance_id", "code"],
    )
    return republish_version(sg_version, sg_version["project"]["name"])


def republish_version(sg_version, project_name, review=True, final=True):
    report_items = collections.defaultdict(list)

    # Grab the OP's id corresponding to the SG version
    op_version_id = sg_version["sg_op_instance_id"]
    if not op_version_id or op_version_id == "-":
        sub_msg = f"{sg_version['code']}<br>"
        report_items["Missing 'sg_op_instance_id' field on SG Versions"].append(sub_msg)
        return report_items

    # Get OP version corresponding to the SG version
    version_doc = get_version_by_id(project_name, op_version_id)
    if not version_doc:
        sub_msg = f"{sg_version['code']}<br>"
        report_items["No OP version found for SG versions"].append(sub_msg)
        return report_items

    # Find the OP representations we want to deliver
    exr_repre_doc = get_representation_by_name(
        project_name,
        "exr",
        version_id=op_version_id,
    )

    if not exr_repre_doc:
        sub_msg = f"{sg_version['code']}<br>"
        report_items["No 'exr' representation found on SG versions"].append(sub_msg)
        return report_items

    exr_path = exr_repre_doc["data"]["path"]
    render_path = os.path.dirname(exr_path)

    families = version_doc["data"]["families"]
    families.append("review")

    if review:
        families.append("client_review")

    if final:
        families.append("client_final")

    instance_data = {
        "project": project_name,
        "family": exr_repre_doc["context"]["family"],
        "subset": exr_repre_doc["context"]["subset"],
        "families": families,
        "asset": exr_repre_doc["context"]["asset"],
        "task": exr_repre_doc["context"]["task"]["name"],
        "frameStart": version_doc["data"]["frameStart"],
        "frameEnd": version_doc["data"]["frameEnd"],
        "handleStart": version_doc["data"]["handleStart"],
        "handleEnd": version_doc["data"]["handleEnd"],
        "frameStartHandle": int(
            version_doc["data"]["frameStart"] - version_doc["data"]["handleStart"]
        ),
        "frameEndHandle": int(
            version_doc["data"]["frameEnd"] + version_doc["data"]["handleEnd"]
        ),
        "comment": version_doc["data"]["comment"],
        "fps": version_doc["data"]["fps"],
        "source": version_doc["data"]["source"],
        "overrideExistingFrame": False,
        "jobBatchName": "Republish - {}_{}".format(
            sg_version["code"], version_doc["name"]
        ),
        "useSequenceForReview": True,
        "colorspace": version_doc["data"].get("colorspace"),
        "version": version_doc["name"],
        "outputDir": render_path,
    }

    # Inject variables into session
    legacy_io.Session["AVALON_ASSET"] = instance_data["asset"]
    legacy_io.Session["AVALON_TASK"] = instance_data.get("task")
    legacy_io.Session["AVALON_WORKDIR"] = render_path
    legacy_io.Session["AVALON_PROJECT"] = project_name
    legacy_io.Session["AVALON_APP"] = "traypublisher"

    # TODO: account for slate
    expected_files(
        instance_data,
        version_doc["data"]["source"],
        instance_data["frameStartHandle"],
        instance_data["frameEndHandle"],
    )
    logger.debug("__ expectedFiles: `{}`".format(instance_data["expectedFiles"]))

    representations = _get_representations(
        instance_data,
        instance_data.get("expectedFiles"),
        False,
    )

    # inject colorspace data
    for rep in representations:
        set_representation_colorspace(
            rep, project_name, colorspace=instance_data["colorspace"]
        )

    if "representations" not in instance_data.keys():
        instance_data["representations"] = []

    # add representation
    instance_data["representations"] += representations
    instances = [instance_data]

    render_job = {}
    render_job["Props"] = {}
    # Render job doesn't exist because we do not have prior submission.
    # We still use data from it so lets fake it.
    #
    # Batch name reflect original scene name

    render_job["Props"]["Batch"] = instance_data.get("jobBatchName")

    # User is deadline user
    render_job["Props"]["User"] = getpass.getuser()

    # get default deadline webservice url from deadline module
    deadline_url = get_system_settings()["modules"]["deadline"]["deadline_urls"][
        "default"
    ]

    metadata_path = _create_metadata_path(instance_data)
    logger.info("Metadata path: %s", metadata_path)

    deadline_publish_job_id = _submit_deadline_post_job(
        instance_data, render_job, instances, render_path, deadline_url, metadata_path
    )

    # Inject deadline url to instances.
    for inst in instances:
        inst["deadlineUrl"] = deadline_url

    # publish job file
    publish_job = {
        "asset": instance_data["asset"],
        "frameStart": instance_data["frameStartHandle"],
        "frameEnd": instance_data["frameEndHandle"],
        "fps": instance_data["fps"],
        "source": instance_data["source"],
        "user": getpass.getuser(),
        "version": None,  # this is workfile version
        "intent": None,
        "comment": instance_data["comment"],
        "job": render_job or None,
        "session": legacy_io.Session.copy(),
        "instances": instances,
    }

    if deadline_publish_job_id:
        publish_job["deadline_publish_job_id"] = deadline_publish_job_id

    logger.info("Writing json file: {}".format(metadata_path))
    with open(metadata_path, "w") as f:
        json.dump(publish_job, f, indent=4, sort_keys=True)


def _create_metadata_path(instance_data):
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


def _get_representations(instance_data, exp_files, do_not_add_review):
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

    config_data = get_colorspace_settings(project_name)

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


def _solve_families(instance, preview=False):
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


def _submit_deadline_post_job(
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
    # metadata_path = _create_metadata_path(instance_data)
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


if __name__ == "__main__":
    republish_version_command()
