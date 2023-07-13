"""Module for handling OP delivery of Shotgrid playlists"""
import os
import copy
import collections
import click
import tqdm
import clique
import getpass
import json

import shotgun_api3

from openpype.client import (
    get_project,
    get_version_by_id,
    get_representations,
    get_representation_by_name,
)
from openpype.lib import Logger, collect_frames, get_datetime_data
from openpype.pipeline import Anatomy
from openpype.pipeline.load import get_representation_path_with_anatomy
from openpype.pipeline.delivery import (
    check_destination_path,
    deliver_single_file,
)
from openpype.modules.shotgrid.lib.settings import get_shotgrid_servers
from openpype.settings import get_project_settings

logger = Logger.get_logger(__name__)


def get_shotgrid_session():
    """Return a Shotgun API session object for the configured ShotGrid server.

    The function reads the ShotGrid server settings from the OpenPype
    configuration file and uses them to create a Shotgun API session object.

    Returns:
        A Shotgun API session object.
    """
    shotgrid_servers_settings = get_shotgrid_servers()
    logger.info(
        "shotgrid_servers_settings: {}".format(shotgrid_servers_settings)
    )

    shotgrid_server_setting = shotgrid_servers_settings.get("alkemyx", {})
    shotgrid_url = shotgrid_server_setting.get("shotgrid_url", "")

    shotgrid_script_name = shotgrid_server_setting.get(
        "shotgrid_script_name", ""
    )
    shotgrid_script_key = shotgrid_server_setting.get(
        "shotgrid_script_key", ""
    )
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
@click.option("--delivery_template_name", "-t", required=False)
@click.option("--representation_names", "-r", multiple=True, required=False)
def deliver_playlist_command(
    playlist_id, delivery_template_name=None, representation_names=None
):
    """Given a SG playlist id, deliver all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to deliver.
        delivery_template_name (str): Name of the delivery template to use.
        representation_names (list): List of representation names to deliver.
    """
    return deliver_playlist(
        playlist_id, delivery_template_name, representation_names
    )


def deliver_playlist(
    playlist_id,
    delivery_template_name=None,
    representation_names=None,
    delivery_type=None,
):
    """Given a SG playlist id, deliver all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to deliver.
        delivery_template_name (str): Name of the delivery template to use.
        representation_names (list): List of representation names to deliver.
        delivery_type (str): What type of delivery it is (i.e., final, review)
    """
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
        return {
            "success": False,
            "message": ('Didn\'t find project "{}" in avalon.').format(
                project_name
            ),
        }

    # Get whether the project entity contains any delivery overrides
    sg_project = sg.find_one(
        "Project",
        [["id", "is", sg_playlist["project"]["id"]]],
        fields=[
            "sg_delivery_name",
            "sg_delivery_template",
            "sg_final_output",
            "sg_review_output",
        ],
    )
    delivery_project_name = sg_project.get("sg_delivery_name")
    delivery_template = sg_project.get("sg_delivery_template")

    if not representation_names:
        representation_names = []

    # Generate a list of representation names from the output types set in SG
    out_data_types = sg_project.get(f"sg_{delivery_type}_output")
    for out_data_type in out_data_types:
        representation_names.append(
            "{}_{}".format(
                out_data_type["name"].replace(" ", "").lower(),
                delivery_type,
            )
        )

    # Get all the SG versions associated to the playlist
    sg_versions = sg.find(
        "Version",
        [["playlists", "in", sg_playlist]],
        ["sg_op_instance_id", "entity", "code"],
    )

    # Create dictionary of inputs required by deliver_sg_version
    delivery_data = {
        "date": get_datetime_data(),
        "delivery_template": delivery_template,
        "delivery_project_name": delivery_project_name,
    }

    # Iterate over each SG version and deliver it
    report_items = collections.defaultdict(list)
    for sg_version in tqdm.tqdm(sg_versions):
        new_report_items = deliver_sg_version(
            sg_version,
            project_name,
            delivery_data,
            representation_names,
            delivery_template_name,
        )
        if new_report_items:
            report_items.update(new_report_items)

    click.echo(report_items)
    return report_items


def deliver_sg_version(
    sg_version,
    project_name,
    delivery_data,
    delivery_template_name=None,
    representation_names=None,
):
    report_items = collections.defaultdict(list)

    # Grab the OP's id corresponding to the SG version
    op_version_id = sg_version["sg_op_instance_id"]
    if not op_version_id or op_version_id == "-":
        sub_msg = f"{sg_version['code']}<br>"
        report_items[
            "Missing 'sg_op_instance_id' field on SG Versions"
        ].append(sub_msg)
        return report_items

    # Get the corresponding shot and whether it contains any overrides
    sg = get_shotgrid_session()
    sg_shot = sg.find_one(
        "Shot",
        [["id", "is", sg_version["entity"]["id"]]],
        fields=["sg_delivery_name", "sg_delivery_template"],
    )

    delivery_shot_name = sg_shot.get("sg_delivery_name")
    # Override delivery_template only if the value is not None, otherwise fallback
    # to whatever existing value delivery_template had (could be None as well)
    delivery_template = sg_shot.get(
        "sg_delivery_template"
    ) or delivery_data.get("delivery_template")

    anatomy = Anatomy(project_name)

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

        # If delivery template name is passed as an argument, use that
        # Otherwise, set it based on whether it's a sequence or a single file
        if delivery_template_name:
            _delivery_template_name = delivery_template_name
        elif frame:
            _delivery_template_name = "sequence"
        else:
            _delivery_template_name = "single_file"

        anatomy_data = copy.deepcopy(repre["context"])
        repre_report_items = check_destination_path(
            repre["_id"],
            anatomy,
            anatomy_data,
            delivery_data.get("date"),
            _delivery_template_name,
        )

        if repre_report_items:
            return repre_report_items

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

        repre_path = get_representation_path_with_anatomy(repre, anatomy)

        args = [
            repre_path,
            repre,
            anatomy,
            _delivery_template_name,
            anatomy_data,
            None,
            report_items,
            logger,
            # delivery_template,
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
            report_items.update(new_report_items)

    return report_items


def republish_version(sg_version, project_name, review=True, final=True):

    report_items = collections.defaultdict(list)

    # Grab the OP's id corresponding to the SG version
    op_version_id = sg_version["sg_op_instance_id"]
    if not op_version_id or op_version_id == "-":
        sub_msg = f"{sg_version['code']}<br>"
        report_items[
            "Missing 'sg_op_instance_id' field on SG Versions"
        ].append(sub_msg)
        return report_items

    # Get OP version corresponding to the SG version
    version_doc = get_version_by_id(project_name, op_version_id)
    if not version_doc:
        sub_msg = f"{sg_version['code']}<br>"
        report_items[
            "No OP version found for SG versions"
        ].append(sub_msg)
        return report_items

    # Find the OP representations we want to deliver
    exr_repre_doc = get_representation_by_name(
        project_name,
        "exr",
        version_id=op_version_id,
    )

    if not exr_repre_doc:
        sub_msg = f"{sg_version['code']}<br>"
        report_items[
            "No 'exr' representation found on SG versions"
        ].append(sub_msg)
        return report_items

    exr_path = exr_repre_doc["data"]["path"]
    render_path = os.path.dirname(exr_path)

    families = version_doc["families"] + "review"

    if review:
        families.append("client_review")

    if final:
        families.append("client_final")

    instance_data = {
        "family": exr_repre_doc.context["family"],
        "subset": exr_repre_doc.context["subset"],
        "families": families,
        "asset": exr_repre_doc.context["asset"],
        "frameStart": version_doc.data["frameStart"],
        "frameEnd": version_doc.data["frameEnd"],
        "handleStart": version_doc.data["handleStart"],
        "handleEnd": version_doc.data["handleEnd"],
        "frameStartHandle": version_doc.data["frameStart"] - version_doc.data["handleStart"],
        "frameEndHandle": version_doc.data["frameEnd"] + version_doc.data["handleEnd"],
        "comment": version_doc.data["comment"],
        "fps": version_doc.data["fps"],
        "source": version_doc.data["source"],
        "overrideExistingFrame": False,
        "jobBatchName": "Republish - {}_{}".format(
            sg_version["code"], version_doc["name"]
        ),
        "useSequenceForReview": True,
        "colorspace": version_doc.data.get("colorspace"),
        "version": version_doc["name"],
    }

    # TODO: account for slate
    expected_files(
        instance_data,
        render_path,
        version_doc.data["frameStart"],
        version_doc.data["frameEnd"]
    )
    logger.debug(
        "__ expectedFiles: `{}`".format(instance_data["expectedFiles"])
    )

    representations = _get_representations(
        instance_data,
        instance_data.get("expectedFiles"),
        False,
        exr_repre_doc.context
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
    deadline_url = get_project_settings(project_name)["deadline"]["deadline_urls"]["default"]

    deadline_publish_job_id = _submit_deadline_post_job(
        instance_data, render_job, instances, render_path
    )

    # Inject deadline url to instances.
    for inst in instances:
        inst["deadlineUrl"] = deadline_url

    # publish job file
    publish_job = {
        "asset": instance_data["asset"],
        "frameStart": instance_data["frameEnd"],
        "frameEnd": instance_data["frameStart"],
        "fps": instance_data["fps"],
        "source": instance_data["source"],
        "user": getpass.getuser(),
        "version": None,  # this is workfile version
        "intent": None,
        "comment": instance_data["comment"],
        "job": render_job or None,
        # "session": legacy_io.Session.copy(),
        "instances": instances
    }

    if deadline_publish_job_id:
        publish_job["deadline_publish_job_id"] = deadline_publish_job_id

    metadata_path = _create_metadata_path(instance_data)

    logger.info("Writing json file: {}".format(metadata_path))
    with open(metadata_path, "w") as f:
        json.dump(publish_job, f, indent=4, sort_keys=True)


def _create_metadata_path(instance_data):
    # Ensure output dir exists
    output_dir = instance_data.get(
        "publishRenderMetadataFolder", instance_data["outputDir"])

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


def _get_representations(instance_data, exp_files, do_not_add_review, context):
    """Create representations for file sequences.

    This will return representations of expected files if they are not
    in hierarchy of aovs. There should be only one sequence of files for
    most cases, but if not - we create representation from each of them.

    Arguments:
        instance_data (dict): instance.data for which we are
                            setting representations
        exp_files (list): list of expected files
        do_not_add_review (bool): explicitly skip review

    Returns:
        list of representations

    """
    representations = []
    collections, _ = clique.assemble(exp_files)

    anatomy = context.data["anatomy"]

    # create representation for every collected sequence
    for collection in collections:
        ext = collection.tail.lstrip(".")
        preview = True

        staging = os.path.dirname(list(collection)[0])
        success, rootless_staging_dir = (
            anatomy.find_root_template_from_path(staging)
        )
        if success:
            staging = rootless_staging_dir
        else:
            logger.warning((
                "Could not find root path for remapping \"{}\"."
                " This may cause issues on farm."
            ).format(staging))

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

    # for rep in representations:
    #     # inject colorspace data
    #     set_representation_colorspace(
    #         rep, context,
    #         colorspace=instance_data["colorspace"]
    #     )

    return representations


def _solve_families(instance, preview=False):
    families = instance.get("families")

    # if we have one representation with preview tag
    # flag whole instance for review and for ftrack
    if preview:
        if "review" not in families:
            logger.debug(
                "Adding \"review\" to families because of preview tag."
            )
            families.append("review")
        if "client_review" not in families:
            logger.debug(
                "Adding \"client_review\" to families because of preview tag."
            )
            families.append("client_review")
        instance["families"] = families


def expected_files(
    instance_data,
    path,
    out_frame_start,
    out_frame_end
):
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
            os.path.join(dirname, (filename % i)).replace("\\", "/"))



def _submit_deadline_post_job(instance_data, job, instances, output_dir):
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
    metadata_path, rootless_metadata_path = \
        _create_metadata_path(instance)

    environment = {
        "AVALON_PROJECT": legacy_io.Session["AVALON_PROJECT"],
        "AVALON_ASSET": instance_data.get("asset"),
        "AVALON_TASK": legacy_io.Session["AVALON_TASK"],
        "OPENPYPE_USERNAME": instance.context.data["user"],
        "OPENPYPE_PUBLISH_JOB": "1",
        "OPENPYPE_RENDER_JOB": "0",
        "OPENPYPE_REMOTE_JOB": "0",
        "OPENPYPE_LOG_NO_COLORS": "1",
        "IS_TEST": str(int(is_in_tests()))
    }

    # add environments from self.environ_keys
    for env_key in self.environ_keys:
        if os.getenv(env_key):
            environment[env_key] = os.environ[env_key]

    # pass environment keys from self.environ_job_filter
    job_environ = job["Props"].get("Env", {})
    for env_j_key in self.environ_job_filter:
        if job_environ.get(env_j_key):
            environment[env_j_key] = job_environ[env_j_key]

    priority = self.deadline_priority or instance_data.get("priority", 50)

    args = [
        "--headless",
        'publish',
        '"{}"'.format(rootless_metadata_path),
        "--targets", "deadline",
        "--targets", "farm"
    ]

    # Generate the payload for Deadline submission
    secondary_pool = (
        self.deadline_pool_secondary or instance_data.get("secondaryPool")
    )
    payload = {
        "JobInfo": {
            "Plugin": self.deadline_plugin,
            "BatchName": job["Props"]["Batch"],
            "Name": job_name,
            "UserName": job["Props"]["User"],
            "Comment": instance.context.data.get("comment", ""),

            "Department": self.deadline_department,
            "ChunkSize": self.deadline_chunk_size,
            "Priority": priority,

            "Group": self.deadline_group,
            "Pool": self.deadline_pool or instance_data.get("primaryPool"),
            "SecondaryPool": secondary_pool,
            # ensure the outputdirectory with correct slashes
            "OutputDirectory0": output_dir.replace("\\", "/")
        },
        "PluginInfo": {
            "Version": self.plugin_pype_version,
            "Arguments": " ".join(args),
            "SingleFrameOnly": "True",
        },
        # Mandatory for Deadline, may be empty
        "AuxFiles": [],
    }

    # add assembly jobs as dependencies
    if instance_data.get("tileRendering"):
        logger.info("Adding tile assembly jobs as dependencies...")
        job_index = 0
        for assembly_id in instance_data.get("assemblySubmissionJobs"):
            payload["JobInfo"]["JobDependency{}".format(job_index)] = assembly_id  # noqa: E501
            job_index += 1
    elif instance_data.get("bakingSubmissionJobs"):
        logger.info("Adding baking submission jobs as dependencies...")
        job_index = 0
        for assembly_id in instance_data["bakingSubmissionJobs"]:
            payload["JobInfo"]["JobDependency{}".format(job_index)] = assembly_id  # noqa: E501
            job_index += 1
    elif job.get("_id"):
        payload["JobInfo"]["JobDependency0"] = job["_id"]

    if instance_data.get("suspend_publish"):
        payload["JobInfo"]["InitialStatus"] = "Suspended"

    for index, (key_, value_) in enumerate(environment.items()):
        payload["JobInfo"].update(
            {
                "EnvironmentKeyValue%d"
                % index: "{key}={value}".format(
                    key=key_, value=value_
                )
            }
        )
    # remove secondary pool
    payload["JobInfo"].pop("SecondaryPool", None)

    logger.info("Submitting Deadline job ...")

    url = "{}/api/jobs".format(self.deadline_url)
    response = requests.post(url, json=payload, timeout=10)
    if not response.ok:
        raise Exception(response.text)

    deadline_publish_job_id = response.json()["_id"]

    return deadline_publish_job_id


if __name__ == "__main__":
    deliver_playlist()
