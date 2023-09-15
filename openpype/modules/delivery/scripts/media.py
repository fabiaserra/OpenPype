
"""Module for handling generation of delivery media of SG playlists and versions"""
import os
import collections
import copy
import re
import click

from openpype import client as op_cli
from openpype.lib import Logger, StringTemplate, get_datetime_data
from openpype.pipeline import delivery
from openpype.modules.deadline.lib import submit
from openpype.modules.shotgrid.lib import credentials


logger = Logger.get_logger(__name__)


NUKE_DELIVERY_PY_DEFAULT = "/pipe/hiero/templates/nuke_delivery.py"
NUKE_DELIVERY_SCRIPT_DEFAULT = "/pipe/hiero/templates/nuke_delivery.nk"

DELIVERY_STAGING_DIR = "/proj/{proj[code]}/io/delivery/ready_to_deliver/{yyyy}{mm}{dd}/{package_name}"


SG_FIELD_MEDIA_GENERATED = "sg_op_delivery_media_generated"
SG_FIELD_MEDIA_PATH = "sg_op_delivery_media_path"


def generate_delivery_media_playlist_id(
    playlist_id,
    delivery_types,
    override_version=None,
):
    """Given a SG playlist id, generate all the delivery media for all the versions associated to it.

    Args:
        playlist_id (int): Shotgrid playlist id to republish.
        delivery_types (list[str]): What type(s) of delivery it is
            (i.e., ["final", "review"])

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

    project_doc = op_cli.get_project(project_name, fields=["name"])
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
            delivery_types
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
    override_version=None,
):
    """Given a SG version id, generate its corresponding delivery so it
        triggers the OP publish pipeline again.

    Args:
        version_id (int): Shotgrid version id to republish.
        delivery_types (list[str]): What type(s) of delivery it is so we
            regenerate those representations.

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
    )


def generate_delivery_media_version(
    sg_version,
    project_name,
    delivery_types,
    delivery_args=None,
    override_version=None,
    out_filename_template=None,
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
    version_doc = op_cli.get_version_by_id(project_name, op_version_id)
    if not version_doc:
        msg = "No OP version found for SG versions"
        sub_msg = f"{sg_version['code']} - id: {sg_version['id']}<br>"
        logger.error("%s: %s", msg, sub_msg)
        report_items[msg].append(sub_msg)
        return report_items, False

    # Find the OP representations we want to deliver
    exr_repre_doc = op_cli.get_representation_by_name(
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

    # TODO: move this on the dialog
    sg = credentials.get_shotgrid_session()
    representation_names, entity = delivery.get_representation_names(
        sg, sg_version["id"], "Version", delivery_types
    )
    logger.debug(
        "%s representation names found at '%s': %s",
        sg_version['code'],
        entity,
        representation_names
    )

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
    thumbnail_repre_doc = op_cli.get_representation_by_name(
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

    input_path = exr_repre_doc["data"]["path"]
    # Replace frame number with #'s for expected_files function
    hashes_path = re.sub(
        r"\d+(?=\.\w+$)", lambda m: "#" * len(m.group()) if m.group() else "#",
        input_path
    )

    anatomy_data = copy.deepcopy(exr_repre_doc["context"])
    anatomy_data.update(get_datetime_data())
    for representation_name in representation_names:
        # repre_report_items, dest_path = delivery.check_destination_path(
        #     delivery_name,
        #     anatomy=None,
        #     anatomy_data,
        #     get_datetime_data(),
        #     delivery_template_name,
        #     delivery_template,
        #     return_dest_path=True,
        # )
        output_path_template = os.path.join(
            DELIVERY_STAGING_DIR, out_filename_template
        )
        output_delivery_path = StringTemplate.format_template(
            output_path_template, anatomy_data
        )

        # No need to raise error as Nuke raises an error exit value if
        # something went wrong
        logger.info("Submitting Nuke transcode")

        # Add environment variables required to run Nuke script
        extra_env = {}
        extra_env["_AX_DELIVERY_NUKESCRIPT"] = NUKE_DELIVERY_SCRIPT_DEFAULT
        extra_env["_AX_DELIVERY_FRAMES"] = "{0}_{1}_{2}".format(
            int(out_frame_start), int(out_frame_end), int(out_frame_start)
        )
        extra_env["_AX_DELIVERY_READPATH"] = input_path
        extra_env["_AX_DELIVERY_WRITEPATH"] = output_delivery_path

        plugin_data = {
            "ScriptJob": True,
            "ScriptFilename": NUKE_DELIVERY_PY_DEFAULT,
            # the Version entry is kind of irrelevant as our Deadline workers only
            # contain a single DCC version at the time of writing this
            "Version": "14.0",
            "UseGpu": False,
        }

        # TODO: Change the AxNuke plugin to improve monitored process when
        # submitting "scriptJob" type Nuke jobs to not error out when
        # exiting the script
        response = submit.payload_submit(
            output_delivery_path,
            (out_frame_start, out_frame_end),
            plugin="AxNuke",
            plugin_data=plugin_data,
            extra_env=extra_env,
        )

    click.echo(report_items)
    return report_items, True
