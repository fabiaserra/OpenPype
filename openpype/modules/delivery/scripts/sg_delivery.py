"""Module for handling OP delivery of Shotgrid playlists"""
import os
import re
import copy
import collections
import click
import tqdm
import getpass
import json

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
from openpype.settings import get_system_settings
from openpype.modules.delivery.scripts import utils


logger = Logger.get_logger(__name__)

# List of SG fields from context entities (i.e., Project, Shot) that we care to
# query for delivery purposes
SG_DELIVERY_FIELDS = [
    "sg_delivery_name",
    "sg_final_output_type",
    "sg_review_output_type",
]

# List of SG entities hierarchy from more specific to more generic
SG_HIERARCHY = ["Shot", "Sequence", "Episode", "Project"]

# List of SG fields that we need to query to grab the parent entity
SG_HIERARCHY_FIELDS = ["entity", "sg_sequence", "episode", "project"]


@click.command("deliver_playlist_id")
@click.option(
    "--playlist_id",
    "-p",
    required=True,
    type=int,
    help="Shotgrid playlist id to deliver.",
)
@click.option(
    "--delivery_types",
    "-types",
    type=click.Choice(["final", "review"]),
    required=False,
    multiple=True,
    default=["final", "review"],
)
@click.option(
    "--representation_names",
    "-r",
    multiple=True,
    required=False,
    help="List of representation names that we want to deliver",
    default=None,
)
def deliver_playlist_id_command(
    playlist_id,
    delivery_types,
    representation_names=None,
):
    """Given a SG playlist id, deliver all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to deliver.
        delivery_types (list[str]): What type(s) of delivery it is
        representation_names (list): List of representation names to deliver.
            (i.e., ["final", "review"])

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the delivery was successful.
    """
    return deliver_playlist_id(playlist_id, delivery_types, representation_names)


def deliver_playlist_id(
    playlist_id,
    delivery_types,
    representation_names=None,
    delivery_templates=None,
):
    """Given a SG playlist id, deliver all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to deliver.
        representation_names (list): List of representation names to deliver.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        delivery_templates (dict[str, str]): Dictionary that maps different
            delivery types (i.e., 'single_file', 'sequence') to the corresponding
            templated string to use for delivery.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the delivery was successful.
    """
    report_items = collections.defaultdict(list)

    sg = utils.get_shotgrid_session()

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

    # Get all the SG versions associated to the playlist
    sg_versions = sg.find(
        "Version",
        [["playlists", "in", sg_playlist]],
        ["sg_op_instance_id", "entity", "code"],
    )

    # Create dictionary of inputs required by deliver_version
    delivery_data = {
        "date": get_datetime_data(),
        "delivery_project_name": delivery_project_name,
    }

    if not representation_names:
        representation_names = []

    # Generate a list of representation names from the output types set in SG
    # if delivery_types:
    #     representation_names.extend(
    #         utils.get_sg_entity_representation_names(sg_project, delivery_types)
    #     )
    # if representation_names:
    #     msg = "Delivering representation names:"
    #     logger.info("%s: %s", msg, representation_names)
    #     report_items[msg] = representation_names
    # else:
    #     msg = "No representation names specified: "
    #     sub_msg = "All representations will be delivered."
    #     logger.info(msg + sub_msg)
    #     report_items[msg] = [sub_msg]

    # Iterate over each SG version and deliver it
    success = True
    for sg_version in tqdm.tqdm(sg_versions):
        new_report_items, new_success = deliver_version(
            sg_version,
            project_name,
            delivery_data,
            delivery_types,
            representation_names,
            delivery_templates,
        )
        if new_report_items:
            report_items.update(new_report_items)

        if not new_success:
            success = False

    click.echo(report_items)
    return report_items, success


@click.command("deliver_version_id")
@click.option(
    "--version_id",
    "-v",
    required=True,
    type=int,
    help="Shotgrid version id to deliver.",
)
@click.option(
    "--delivery_types",
    "-types",
    type=click.Choice(["final", "review"]),
    required=False,
    multiple=True,
    default=["final", "review"],
)
@click.option(
    "--representation_names",
    "-r",
    multiple=True,
    required=False,
    help="List of representation names that should be delivered.",
    default=None,
)
def deliver_version_id_command(
    version_id,
    delivery_types,
    representation_names=None,
):
    """Given a SG version id, deliver it so it triggers the OP publish pipeline again.

    Args:
        version_id (int): Shotgrid version id to deliver.
        delivery_types (list[str]): What type(s) of delivery it is so we
            regenerate those representations.
        representation_names (list): List of representation names that should exist on
            the representations being published.
        force (bool): Whether to force the creation of the delivery representations or not.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the deliver was successful.
    """
    return deliver_version_id(version_id, delivery_types, representation_names)


def deliver_version_id(
    version_id,
    delivery_types,
    representation_names=None,
    delivery_templates=None,
):
    """Util function to deliver a single SG version given its id.

    Args:
        version_id (str): Shotgrid Version id to deliver.
        project_name (str): Name of the project corresponding to the version being
            delivered.
        delivery_data (dict[str, str]): Dictionary of relevant data for delivery.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        representation_names (list): List of representation names to deliver.
        delivery_templates (dict[str, str]): Dictionary that maps different
            delivery types (i.e., 'single_file', 'sequence') to the corresponding
            templated string to use for delivery.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the delivery was successful.
    """
    report_items = collections.defaultdict(list)

    sg = utils.get_shotgrid_session()

    # Get all the SG versions associated to the playlist
    sg_version = sg.find_one(
        "Version",
        [["id", "is", int(version_id)]],
        ["sg_op_instance_id", "entity", "code", "project"],
    )

    if not sg_version:
        report_items["SG Version not found"].append(version_id)
        return report_items, False

    # Get whether the project entity contains any delivery overrides
    sg_project = sg.find_one(
        "Project",
        [["id", "is", sg_version["project"]["id"]]],
        fields=SG_DELIVERY_FIELDS,
    )
    delivery_project_name = sg_project.get("sg_delivery_name")

    # Create dictionary of inputs required by deliver_version
    delivery_data = {
        "date": get_datetime_data(),
        "delivery_project_name": delivery_project_name,
    }

    return deliver_version(
        sg_version,
        sg_version["project"]["name"],
        delivery_data,
        delivery_types,
        representation_names,
        delivery_templates,
    )


def deliver_version(
    sg_version,
    project_name,
    delivery_data,
    delivery_types,
    representation_names=None,
    delivery_templates=None,
):
    """Deliver a single SG version.

    Args:
        sg_version (): Shotgrid Version object to deliver.
        project_name (str): Name of the project corresponding to the version being
            delivered.
        delivery_data (dict[str, str]): Dictionary of relevant data for delivery.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        representation_names (list): List of representation names to deliver.
        delivery_templates (dict[str, str]): Dictionary that maps different
            delivery types (i.e., 'single_file', 'sequence') to the corresponding
            templated string to use for delivery.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the delivery was successful.
    """
    report_items = collections.defaultdict(list)

    # Grab the OP's id corresponding to the SG version
    op_version_id = sg_version["sg_op_instance_id"]
    if not op_version_id or op_version_id == "-":
        sub_msg = f"{sg_version['code']} - {sg_version['id']}<br>"
        msg = "Missing 'sg_op_instance_id' field on SG Versions"
        report_items[msg].append(sub_msg)
        logger.error("%s: %s", msg, sub_msg)
        return report_items, False

    anatomy = Anatomy(project_name)

    # Get the corresponding shot and whether it contains any overrides
    sg = utils.get_shotgrid_session()
    sg_shot = sg.find_one(
        "Shot",
        [["id", "is", sg_version["entity"]["id"]]],
        fields=SG_DELIVERY_FIELDS,
    )
    delivery_shot_name = sg_shot.get("sg_delivery_name")

    entity = None
    if not representation_names:
        # Add representation names for the current SG Version
        representation_names, entity = utils.get_sg_version_representation_names(
            sg_version, delivery_types
        )

    if representation_names:
        if entity != "Project":
            msg = f"Override of outputs for '{sg_version['code']}' " \
                f"({sg_version['id']}) at the {entity} level"
            logger.info("%s: %s", msg, representation_names)
            report_items[msg] = representation_names
        else:
            msg = "Project delivery representation names"
            logger.info("%s: %s", msg, representation_names)
            report_items[msg] = representation_names
    else:
        msg = "No representation names specified"
        sub_msg = "All representations will be delivered."
        logger.info("%s: %s", msg, sub_msg)
        report_items[msg] = [sub_msg]

    # Find the OP representations we want to deliver
    repres_to_deliver = list(
        get_representations(
            project_name,
            representation_names=representation_names,
            version_ids=[op_version_id],
        )
    )
    if not repres_to_deliver:
        sub_msg = f"{sg_version['code']} - {sg_version['id']}<br>"
        msg = "None of the representations requested found on SG Versions"
        report_items[msg].append(sub_msg)
        logger.error("%s: %s", msg, sub_msg)
        return report_items, False

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
        delivery_template = None
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
            msg = "Project name overridden"
            sub_msg = "{} -> {}".format(
                anatomy_data["project"]["name"],
                delivery_project_name,
            )
            logger.info("%s: %s", msg, sub_msg)
            report_items[msg].append(sub_msg)
            anatomy_data["project"]["name"] = delivery_project_name

        if delivery_shot_name:
            msg = "Shot name overridden"
            sub_msg = "{} -> {}".format(
                anatomy_data["asset"],
                delivery_shot_name,
            )
            logger.info("%s: %s", msg, sub_msg)
            report_items[msg].append(sub_msg)
            anatomy_data["asset"] = delivery_shot_name

        logger.debug("Anatomy data: %s" % anatomy_data)

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
                msg = "Successful delivered representations"
                sub_msg = f"{repre_path} -> {dest_path}<br>"
                report_items[msg].append(sub_msg)
                logger.info("%s: %s", msg, sub_msg)
            report_items.update(new_report_items)

    return report_items, True


@click.command("republish_playlist_id")
@click.option(
    "--playlist_id",
    "-p",
    required=True,
    type=int,
    help="Shotgrid playlist id to republish.",
)
@click.option(
    "--representation_names",
    "-r",
    multiple=True,
    required=False,
    help="List of representation names that should exist on the republished version",
    default=None,
)
@click.option(
    "--delivery_types",
    "-types",
    type=click.Choice(["final", "review"]),
    required=False,
    multiple=True,
    default=["final", "review"],
)
@click.option("--override/--no-override", default=False)
def republish_playlist_id_command(
    playlist_id,
    delivery_types,
    representation_names=None,
    override=False,
):
    """Given a SG playlist id, republish all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to republish.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        representation_names (list): List of representation names that should exist on
            the representations being published.
        force (bool): Whether to force the creation of the delivery representations or not.


    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the republish was successful.
    """
    return republish_playlist_id(
        playlist_id, delivery_types, representation_names, override
    )


def republish_playlist_id(
    playlist_id, delivery_types, representation_names=None, force=False
):
    """Given a SG playlist id, deliver all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to republish.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        representation_names (list): List of representation names that should exist on
            the representations being published.
        force (bool): Whether to force the creation of the delivery representations or not.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the republish was successful.
    """
    report_items = collections.defaultdict(list)

    sg = utils.get_shotgrid_session()

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

    if not representation_names:
        representation_names = []

    # Get all the SG versions associated to the playlist
    sg_versions = sg.find(
        "Version",
        [["playlists", "in", sg_playlist]],
        ["sg_op_instance_id", "entity", "code"],
    )

    success = True
    for sg_version in sg_versions:
        new_report_items, new_success = republish_version(
            sg_version, delivery_types, representation_names, force
        )
        if new_report_items:
            report_items.update(new_report_items)

        if not new_success:
            success = False

    click.echo(report_items)
    return report_items, success


@click.command("republish_version_id")
@click.option(
    "--version_id",
    "-v",
    required=True,
    type=int,
    help="Shotgrid version id to republish.",
)
@click.option(
    "--delivery_types",
    "-types",
    type=click.Choice(["final", "review"]),
    required=False,
    multiple=True,
    default=["final", "review"],
)
@click.option(
    "--representation_names",
    "-r",
    multiple=True,
    required=False,
    help="List of representation names that should exist on the republished version",
    default=None,
)
@click.option("--force/--no-force", default=False)
def republish_version_id_command(
    version_id,
    delivery_types,
    representation_names=None,
    force=False,
):
    """Given a SG version id, republish it so it triggers the OP publish pipeline again.

    Args:
        version_id (int): Shotgrid version id to republish.
        delivery_types (list[str]): What type(s) of delivery it is so we
            regenerate those representations.
        representation_names (list): List of representation names that should exist on
            the representations being published.
        force (bool): Whether to force the creation of the delivery representations or not.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the republish was successful.
    """
    return republish_version_id(version_id, delivery_types, representation_names, force)


def republish_version_id(
    version_id,
    delivery_types,
    representation_names=None,
    force=False,
):
    """Given a SG version id, republish it so it triggers the OP publish pipeline again.

    Args:
        version_id (int): Shotgrid version id to republish.
        delivery_types (list[str]): What type(s) of delivery it is so we
            regenerate those representations.
        representation_names (list): List of representation names that should exist on
            the representations being published.
        force (bool): Whether to force the creation of the delivery representations or not.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the republish was successful.
    """
    sg = utils.get_shotgrid_session()

    sg_version = sg.find_one(
        "Version",
        [
            ["id", "is", int(version_id)],
        ],
        ["project", "sg_op_instance_id", "code", "entity", "project"],
    )
    return republish_version(
        sg_version,
        sg_version["project"]["name"],
        delivery_types,
        representation_names,
        force,
    )


def republish_version(
    sg_version, project_name, delivery_types, representation_names=None, force=False
):
    """
    Republishes the given SG version by creating new review and/or final outputs.

    Args:
        sg_version (dict): The Shotgrid version to republish.
        project_name (str): The name of the Shotgrid project.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        representation_names (list): List of representation names that should exist on
            the representations being published.
        force (bool): Whether to force the creation of the delivery representations or not.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the republish was successful.
    """
    report_items = collections.defaultdict(list)

    # Grab the OP's id corresponding to the SG version
    op_version_id = sg_version["sg_op_instance_id"]
    if not op_version_id or op_version_id == "-":
        msg = "Missing 'sg_op_instance_id' field on SG Versions"
        sub_msg = f"{sg_version['code']} - {sg_version['id']}<br>"
        logger.error("%s: %s", msg, sub_msg)
        report_items[msg].append(sub_msg)
        return report_items, False

    # Get OP version corresponding to the SG version
    version_doc = get_version_by_id(project_name, op_version_id)
    if not version_doc:
        msg = "No OP version found for SG versions"
        sub_msg = f"{sg_version['code']} - {sg_version['id']}<br>"
        logger.error("%s: %s", msg, sub_msg)
        report_items[msg].append(sub_msg)
        return report_items, False

    # Find the OP representations we want to deliver
    exr_repre_doc = get_representation_by_name(
        project_name,
        "exr",
        version_id=op_version_id,
    )
    if not exr_repre_doc:
        msg = "No 'exr' representation found on SG versions"
        sub_msg = f"{sg_version['code']} - {sg_version['id']}<br>"
        logger.error("%s: %s", msg, sub_msg)
        report_items[msg].append(sub_msg)
        return report_items, False

    # If we are not forcing the creation of representations we validate whether the
    # representations requested already exist
    if not force:
        if not representation_names:
            representation_names, _ = utils.get_sg_version_representation_names(
                sg_version, delivery_types
            )
        representations = get_representations(
            project_name,
            version_ids=[op_version_id],
        )
        existing_rep_names = {rep["name"] for rep in representations}
        missing_rep_names = set(representation_names) - existing_rep_names
        if not missing_rep_names:
            msg = f"Requested '{delivery_types}' representations already exist"
            sub_msg = f"{sg_version['code']} - {sg_version['id']}<br>"
            report_items[msg].append(sub_msg)
            logger.info("%s: %s", msg, sub_msg)
            return report_items, True

    exr_path = exr_repre_doc["data"]["path"]
    render_path = os.path.dirname(exr_path)

    families = version_doc["data"]["families"]
    families.append("review")

    # Add family for each delivery type to control which publish plugins
    # get executed
    for delivery_type in delivery_types:
        families.append(f"client_{delivery_type}")

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

    # Replace frame number with #'s for expected_files function
    hashes_path = re.sub(
        r"\d+(?=\.\w+$)",
        lambda m: "#" * len(m.group()) if m.group() else "#", exr_path
    )

    expected_files = utils.expected_files(
        hashes_path,
        instance_data["frameStartHandle"],
        instance_data["frameEndHandle"],
    )
    logger.debug("__ expectedFiles: `{}`".format(expected_files))

    representations = utils.get_representations(
        instance_data,
        expected_files,
        False,
    )

    # inject colorspace data
    for rep in representations:
        source_colorspace = instance_data["colorspace"] or "scene_linear"
        logger.debug("Setting colorspace '%s' to representation", source_colorspace)
        utils.set_representation_colorspace(
            rep, project_name, colorspace=source_colorspace
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

    metadata_path = utils.create_metadata_path(instance_data)
    logger.info("Metadata path: %s", metadata_path)

    deadline_publish_job_id = utils.submit_deadline_post_job(
        instance_data, render_job, render_path, deadline_url, metadata_path
    )

    report_items["Submitted republish job to Deadline"].append(deadline_publish_job_id)

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

    click.echo(report_items)
    return report_items, True


@click.group()
def cli():
    pass

cli.add_command(deliver_playlist_id_command)
cli.add_command(deliver_version_id_command)
cli.add_command(republish_version_id_command)
cli.add_command(republish_playlist_id_command)


if __name__ == "__main__":
    cli()
