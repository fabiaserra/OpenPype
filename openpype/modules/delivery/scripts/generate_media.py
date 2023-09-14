
"""Module for handling generation of delivery media of SG playlists and versions"""
import os
import re
import collections
import click
import getpass
import json

from openpype.client import (
    get_project,
    get_version_by_id,
    get_representations,
    get_representation_by_name,
    get_subset_by_id,
    get_last_version_by_subset_name,
)
from openpype.lib import Logger
from openpype.pipeline import legacy_io, context_tools
from openpype.settings import get_system_settings
from openpype.modules.shotgrid.lib import credentials, delivery
from openpype.modules.delivery.scripts import utils


logger = Logger.get_logger(__name__)


NUKE_DELIVERY_PY_DEFAULT = "/pipe/hiero/templates/nuke_delivery.py"
NUKE_DELIVERY_PY_SCRIPT = "/pipe/hiero/templates/nuke_delivery.nk"


def generate_delivery_media_playlist_id(
    playlist_id,
    delivery_types,
    representation_names=None,
    force=False,
    description=None,
    override_version=None,
):
    """Given a SG playlist id, generate all the delivery media for all the versions associated to it.

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

    sg = credentials.get_shotgrid_session()

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

    # Get all the SG versions associated to the playlist
    sg_versions = sg.find(
        "Version",
        [["playlists", "in", sg_playlist]],
        ["project", "code", "entity", "sg_op_instance_id"],
    )

    success = True
    for sg_version in sg_versions:
        new_report_items, new_success = generate_delivery_media_version(
            sg_version,
            project_name,
            delivery_types,
            representation_names,
            force,
            description,
            override_version,
        )
        if new_report_items:
            report_items.update(new_report_items)

        if not new_success:
            success = False

    click.echo(report_items)
    return report_items, success


def generate_delivery_media_version_id(
    version_id,
    delivery_types,
    representation_names=None,
    force=False,
    description=None,
    override_version=None,
):
    """Given a SG version id, generate its corresponding delivery so it
        triggers the OP publish pipeline again.

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
    sg = credentials.get_shotgrid_session()

    sg_version = sg.find_one(
        "Version",
        [
            ["id", "is", int(version_id)],
        ],
        ["project", "code", "entity", "sg_op_instance_id"],
    )
    return generate_delivery_media_version(
        sg_version,
        sg_version["project"]["name"],
        delivery_types,
        representation_names,
        force,
        description,
        override_version,
    )


def generate_delivery_media_version(
    sg_version,
    project_name,
    delivery_types,
    representation_names=None,
    force=False,
    description=None,
    override_version=None,
):
    """
    Generate the corresponding delivery version given SG version by creating a new
        subset with review and/or final outputs.

    Args:
        sg_version (dict): The Shotgrid version to republish.
        project_name (str): The name of the Shotgrid project.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])
        representation_names (list): List of representation names that should exist on
            the representations being published.
        force (bool): Whether to force the creation of the delivery representations or
            not.

    Returns:
        tuple: A tuple containing a dictionary of report items and a boolean indicating
            whether the republish was successful.
    """
    report_items = collections.defaultdict(list)

    # Grab the OP's id corresponding to the SG version
    op_version_id = sg_version["sg_op_instance_id"]
    if not op_version_id or op_version_id == "-":
        msg = "Missing 'sg_op_instance_id' field on SG Versions"
        sub_msg = f"{project_name} - {sg_version['code']} - id: {sg_version['id']}<br>"
        logger.error("%s: %s", msg, sub_msg)
        report_items[msg].append(sub_msg)
        return report_items, False

    # Get OP version corresponding to the SG version
    version_doc = get_version_by_id(project_name, op_version_id)
    if not version_doc:
        msg = "No OP version found for SG versions"
        sub_msg = f"{sg_version['code']} - id: {sg_version['id']}<br>"
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
        sub_msg = f"{sg_version['code']} - id: {sg_version['id']}<br>"
        logger.error("%s: %s", msg, sub_msg)
        report_items[msg].append(sub_msg)
        return report_items, False

    # Add family for each delivery type to control which publish plugins
    # get executed
    families = []
    for delivery_type in delivery_types:
        families.append(f"client_{delivery_type}")

    frame_start_handle = int(
        version_doc["data"]["frameStart"] - version_doc["data"]["handleStart"]
    )
    frame_end_handle = int(
        version_doc["data"]["frameEnd"] + version_doc["data"]["handleEnd"]
    )
    logger.debug("Frame start handle: %s", frame_start_handle)
    logger.debug("Frame end handle: %s", frame_end_handle)

    out_frame_start = frame_start_handle
    out_frame_end = frame_end_handle

    # Find the OP representations we want to deliver
    thumbnail_repre_doc = get_representation_by_name(
        project_name,
        "thumbnail",
        version_id=op_version_id,
    )
    if not thumbnail_repre_doc:
        msg = "No 'thumbnail' representation found on SG versions"
        sub_msg = f"{sg_version['code']} - id: {sg_version['id']}<br>"
        logger.error("%s: %s", msg, sub_msg)
        report_items[msg].append(sub_msg)
        return report_items, False


    exr_path = exr_repre_doc["data"]["path"]
    # Replace frame number with #'s for expected_files function
    hashes_path = re.sub(
        r"\d+(?=\.\w+$)", lambda m: "#" * len(m.group()) if m.group() else "#", exr_path
    )

    # No need to raise error as Nuke raises an error exit value if
    # something went wrong
    logger.info("Submitting Nuke transcode")

    # Add environment variables required to run Nuke script
    extra_env = {}
    extra_env["_AX_DELIVERY_NUKESCRIPT"] = NUKE_DELIVERY_PY_SCRIPT
    extra_env["_AX_DELIVERY_FRAMES"] = "{0}_{1}_{2}".format(
        int(out_frame_start), int(out_frame_end), int(out_frame_start)
    )
    extra_env["_AX_DELIVERY_READTYPE"] = self.output_ext.lower()
    extra_env["_AX_DELIVERY_READPATH"] = exr_path
    extra_env["_AX_DELIVERY_WRITEPATH"] = output_path

    # TODO: Change the AxNuke plugin to improve monitored process when
    # submitting "scriptJob" type Nuke jobs to not error out when
    # exiting the script
    response = deadline.payload_submit(
        instance,
        output_path,
        (out_frame_start, out_frame_end),
        plugin="AxNuke",
        extra_env=extra_env,
    )
    # expected_files = utils.expected_files(
    #     hashes_path,
    #     frame_start_handle,
    #     frame_end_handle,
    # )
    # # logger.debug("__ Source expectedFiles: `{}`".format(expected_files))

    # # Inject variables into session
    # legacy_io.Session["AVALON_ASSET"] = instance_data["asset"]
    # legacy_io.Session["AVALON_TASK"] = instance_data.get("task")
    # legacy_io.Session["AVALON_PROJECT"] = project_name
    # legacy_io.Session["AVALON_APP"] = "traypublisher"

    # legacy_io.Session["AVALON_WORKDIR"] = temp_delivery_dir
    # # Set outputDir on instance data as that's used to define where
    # # to save the metadata path
    # instance_data["outputDir"] = temp_delivery_dir

    # render_job = {}
    # render_job["Props"] = {}
    # # Render job doesn't exist because we do not have prior submission.
    # # We still use data from it so lets fake it.
    # #
    # # Batch name reflect original scene name

    # render_job["Props"]["Batch"] = instance_data.get("jobBatchName")

    # # User is deadline user
    # render_job["Props"]["User"] = getpass.getuser()

    # # get default deadline webservice url from deadline module
    # deadline_url = get_system_settings()["modules"]["deadline"]["deadline_urls"][
    #     "default"
    # ]

    # metadata_path = utils.create_metadata_path(instance_data)
    # logger.info("Metadata path: %s", metadata_path)

    # deadline_publish_job_id = utils.submit_deadline_post_job(
    #     instance_data, render_job, temp_delivery_dir, deadline_url, metadata_path
    # )

    # report_items["Submitted generate delivery media job to Deadline"].append(
    #     deadline_publish_job_id
    # )

    # # Inject deadline url to instances.
    # for inst in instances:
    #     inst["deadlineUrl"] = deadline_url

    # # publish job file
    # publish_job = {
    #     "asset": instance_data["asset"],
    #     "frameStart": instance_data["frameStartHandle"],
    #     "frameEnd": instance_data["frameEndHandle"],
    #     "fps": instance_data["fps"],
    #     "source": instance_data["source"],
    #     "user": getpass.getuser(),
    #     "version": None,  # this is workfile version
    #     "intent": None,
    #     "comment": instance_data["comment"],
    #     "job": render_job or None,
    #     "session": legacy_io.Session.copy(),
    #     "instances": instances,
    # }

    # if deadline_publish_job_id:
    #     publish_job["deadline_publish_job_id"] = deadline_publish_job_id

    logger.info("Writing json file: {}".format(metadata_path))
    with open(metadata_path, "w") as f:
        json.dump(publish_job, f, indent=4, sort_keys=True)

    click.echo(report_items)
    return report_items, True
