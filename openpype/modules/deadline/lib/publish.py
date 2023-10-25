import os
import getpass
import json

from openpype.lib import Logger
from openpype.pipeline import legacy_io
from openpype.client import get_asset_by_name, get_subset_by_name, get_version_by_name

from openpype.modules.deadline import constants as dl_constants
from openpype.modules.deadline.lib import submit
from openpype.modules.shotgrid.lib import credentials
from openpype.modules.shotgrid.scripts import populate_tasks
from openpype.modules.delivery.scripts import utils


logger = Logger.get_logger(__name__)


REVIEW_FAMILIES = {
    "render",
    "reference",
}

PUBLISH_TO_SG_FAMILIES = {
    "render",
    "review",
    "reference",
}


def check_version_exists(project_name, asset_doc, subset_name, version):
    """Check whether version document exists in database."""

    subset_doc = get_subset_by_name(
        project_name, subset_name, asset_doc["_id"]
    )
    if not subset_doc:
        return False

    existing_version_doc = get_version_by_name(
        project_name, version, subset_doc["_id"]
    )

    # Check if version already exists
    if existing_version_doc:
        return True

    return False


def check_task_exists(project_name, asset_doc, task_name, force_creation=False):
    """Check whether version document exists in database."""

    if force_creation:
        logger.debug("Creating task '%s' in asset '%s'", task_name, asset_doc["name"])
        sg = credentials.get_shotgrid_session()
        sg_project = sg.find_one("Project", [["name", "is", project_name]], ["code"])
        sg_shot = sg.find_one("Shot", [["code", "is", asset_doc["name"]]], ["code"])
        populate_tasks.add_tasks_to_sg_entities(
            sg_project,
            [sg_shot],
            "Shot",
            tasks={task_name: task_name}
        )
    elif task_name not in asset_doc.get("data", {}).get("tasks", {}):
        return False

    return True


def validate_version(
    project_name,
    asset_name,
    task_name,
    family_name,
    subset_name,
    expected_representations,
    publish_data,
    overwrite_version=False,
    force_task_creation=False,
):
    # String representation of product being published
    item_str = f"Asset: {asset_name} - Task: {task_name} - Family: {family_name} - Subset: {subset_name}"

    # Validate that all required fields exist
    if not all(
        [
            project_name,
            asset_name,
            task_name,
            family_name,
            subset_name,
            expected_representations
        ]
    ):
        msg = (
            f"{item_str} -> Can't publish version without all arguments."
        )
        logger.error(msg)
        return msg, False

    asset_doc = get_asset_by_name(project_name, asset_name, fields=["_id", "data", "name"])
    context_data = asset_doc["data"]

    # Validate that the version doesn't exist if we choose to not overwrite
    if not overwrite_version and publish_data.get("version"):
        if check_version_exists(
            project_name, asset_doc, subset_name, publish_data.get("version")
        ):
            msg = (
                f"{item_str} -> Version already exists."
            )
            logger.error(msg)
            return msg, False

    # Validate that the task exists
    if not check_task_exists(project_name, asset_doc, task_name, force_task_creation):
        msg = (
            f"{item_str} -> Task '{task_name}' doesn't exist."
        )
        logger.error(msg)
        return msg, False

    # TODO: write some logic that finds the main path from the list of
    # representations
    source_path = list(expected_representations.values())[0]

    instance_data = {
        "project": project_name,
        "family": family_name,
        "subset": subset_name,
        "families": publish_data.get("families", []),
        "asset": asset_name,
        "task": task_name,
        "fps": publish_data.get("fps", context_data.get("fps")),
        "comment": publish_data.get("comment", ""),
        "source": source_path,
        "overrideExistingFrame": False,
        "useSequenceForReview": True,
        "colorspace": publish_data.get("colorspace"),
        "version": publish_data.get("version"),
        "outputDir": os.path.dirname(source_path),
    }

    logger.debug("Getting representations...")

    representations = utils.get_representations(
        instance_data,
        expected_representations,
        add_review=family_name in REVIEW_FAMILIES,
        publish_to_sg=family_name in PUBLISH_TO_SG_FAMILIES,
    )
    if not representations:
        msg = f"{item_str} -> No representations could be found on expected dictionary: {expected_representations}"
        logger.error(msg)
        return msg, False

    msg = f"{item_str} -> Valid"

    return msg, True


def publish_version(
    project_name,
    asset_name,
    task_name,
    family_name,
    subset_name,
    expected_representations,
    publish_data,
    overwrite_version=False,
    force_task_creation=False,
):
    # String representation of product being published
    item_str = f"Asset: {asset_name} - Task: {task_name} - Family: {family_name} - Subset: {subset_name}"

    # Validate that all required fields exist
    if not all(
        [
            project_name,
            asset_name,
            task_name,
            family_name,
            subset_name,
            expected_representations
        ]
    ):
        msg = (
            f"{item_str} -> Can't publish version without all arguments."
        )
        logger.error(msg)
        return msg, False

    asset_doc = get_asset_by_name(project_name, asset_name, fields=["_id", "data", "name"])
    context_data = asset_doc["data"]

    # Validate that the version doesn't exist if we choose to not overwrite
    if not overwrite_version and publish_data.get("version"):
        if check_version_exists(
            project_name, asset_doc, subset_name, publish_data.get("version")
        ):
            msg = (
                f"{item_str} -> Version already exists."
            )
            logger.error(msg)
            return msg, False

    # Validate that the task exists
    if not check_task_exists(project_name, asset_doc, task_name, force_task_creation):
        msg = (
            f"{item_str} -> Task '{task_name}' doesn't exist."
        )
        logger.error(msg)
        return msg, False

    # TODO: write some logic that finds the main path from the list of
    # representations
    source_path = list(expected_representations.values())[0]

    instance_data = {
        "project": project_name,
        "family": family_name,
        "subset": subset_name,
        "families": publish_data.get("families", []),
        "asset": asset_name,
        "task": task_name,
        "fps": publish_data.get("fps", context_data.get("fps")),
        "comment": publish_data.get("comment", ""),
        "source": source_path,
        "overrideExistingFrame": False,
        "useSequenceForReview": True,
        "colorspace": publish_data.get("colorspace"),
        "version": publish_data.get("version"),
        "outputDir": os.path.dirname(source_path),
    }

    logger.debug("Getting representations...")

    representations = utils.get_representations(
        instance_data,
        expected_representations,
        add_review=family_name in REVIEW_FAMILIES,
        publish_to_sg=family_name in PUBLISH_TO_SG_FAMILIES,
    )
    if not representations:
        msg = f"{item_str} -> No representations could be found on expected dictionary: {expected_representations}"
        logger.error(msg)
        return msg, False

    if family_name in REVIEW_FAMILIES:
        # inject colorspace data if we are generating a review
        for rep in representations:
            source_colorspace = publish_data.get("colorspace") or "scene_linear"
            logger.debug(
                "Setting colorspace '%s' to representation", source_colorspace
            )
            utils.set_representation_colorspace(
                rep, project_name, colorspace=source_colorspace
            )

    instance_data["frameStart"] = representations[0]["frameStart"]
    instance_data["frameEnd"] = representations[0]["frameEnd"]
    instance_data["frameStartHandle"] = representations[0]["frameStart"]
    instance_data["frameEndHandle"] = representations[0]["frameEnd"]

    # add representation
    instance_data["representations"] = representations
    instances = [instance_data]

    # Create farm job to run OP publish
    metadata_path = utils.create_metadata_path(instance_data)
    logger.info("Metadata path: %s", metadata_path)

    publish_args = [
        "--headless",
        "publish",
        '"{}"'.format(metadata_path),
        "--targets",
        "deadline",
        "--targets",
        "farm",
    ]

    # Create dictionary of data specific to OP plugin for payload submit
    plugin_data = {
        "Arguments": " ".join(publish_args),
        "Version": os.getenv("OPENPYPE_VERSION"),
        "SingleFrameOnly": "True",
    }

    username = getpass.getuser()

    # Submit job to Deadline
    extra_env = {
        "AVALON_PROJECT": project_name,
        "AVALON_ASSET": asset_name,
        "AVALON_TASK": task_name,
        "OPENPYPE_USERNAME": username,
        "AVALON_WORKDIR": os.path.dirname(source_path),
        "OPENPYPE_PUBLISH_JOB": "1",
        "OPENPYPE_RENDER_JOB": "0",
        "OPENPYPE_REMOTE_JOB": "0",
        "OPENPYPE_LOG_NO_COLORS": "1",
        "OPENPYPE_SG_USER": username,
    }

    deadline_task_name = "Publish {} - {} - {} - {} - {}".format(
        family_name,
        subset_name,
        task_name,
        asset_name,
        project_name
    )
    logger.debug("Submitting payload...")

    response = submit.payload_submit(
        plugin="OpenPype",
        plugin_data=plugin_data,
        batch_name=publish_data.get("jobBatchName") or deadline_task_name,
        task_name=deadline_task_name,
        group=dl_constants.OP_GROUP,
        extra_env=extra_env,
    )

    # Set session environment variables as a few OP plugins
    # rely on these
    legacy_io.Session["AVALON_PROJECT"] = project_name
    legacy_io.Session["AVALON_ASSET"] = asset_name
    legacy_io.Session["AVALON_TASK"] = task_name
    legacy_io.Session["AVALON_WORKDIR"] = extra_env["AVALON_WORKDIR"]

    # publish job file
    publish_job = {
        "asset": instance_data["asset"],
        "frameStart": instance_data["frameStartHandle"],
        "frameEnd": instance_data["frameEndHandle"],
        "fps": instance_data["fps"],
        "source": instance_data["source"],
        "user": getpass.getuser(),
        "version": None,  # this is workfile version
        "comment": instance_data["comment"],
        "job": {},
        "session": legacy_io.Session.copy(),
        "instances": instances,
        "deadline_publish_job_id": response.get("_id")
    }

    logger.info("Writing json file: {}".format(metadata_path))
    with open(metadata_path, "w") as f:
        json.dump(publish_job, f, indent=4, sort_keys=True)

    msg = f"{item_str} -> Deadline Job {response.get('_id')}"

    return msg, True
