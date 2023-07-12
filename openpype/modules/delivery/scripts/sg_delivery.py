"""Module for handling OP delivery of Shotgrid playlists"""
import os
import copy
import collections
import click
import tqdm

import shotgun_api3

from openpype.client import get_project, get_representations
from openpype.lib import Logger, collect_frames, get_datetime_data
from openpype.pipeline import Anatomy
from openpype.pipeline.load import get_representation_path_with_anatomy
from openpype.pipeline.delivery import (
    check_destination_path,
    deliver_single_file,
)
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

    out_data_types = sg_project.get(f"sg_{delivery_type}_output")
    for out_data_type in out_data_types:
        sg_out_data_type = sg.find_one(
            "output_data_type",
            [["id", "is", out_data_type]],
            fields=["sg_op_representation_names"]
        )
        representation_names.extend(
            sg_out_data_type.get("sg_op_representation_names") or []
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


if __name__ == "__main__":
    deliver_playlist()
