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
    return deliver_playlist(
        playlist_id, representation_names, delivery_types
    )


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

    if representation_names:
        logger.info("Delivering representation names: %s", representation_names)
    else:
        logger.info(
            "No representation names so we will deliver all existing representations."
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
                repre["data"]["path"]
            )
            delivery_template = delivery_templates[template_name]

            # Make sure we prefix the template with the io folder for the project
            if delivery_template:
                delivery_template = f"/proj/{anatomy.project_code}/io/out/{delivery_template}"

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


if __name__ == "__main__":
    deliver_playlist()
