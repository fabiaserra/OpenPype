import os
import getpass
import json

from openpype import AYON_SERVER_ENABLED
from openpype.lib import Logger
from openpype.pipeline import legacy_io, Anatomy
from openpype.pipeline.template_data import get_template_data
from openpype.client import (
    get_project,
    get_asset_by_name,
    get_subset_by_name,
    get_version_by_name
)

from openpype.tools.utils import paths as path_utils
from openpype.modules.deadline import constants as dl_constants
from openpype.modules.deadline.lib import submit
from openpype.modules.delivery.scripts import utils, review
if AYON_SERVER_ENABLED:
    from ayon_shotgrid.lib import credentials
    from ayon_shotgrid.scripts import populate_tasks
else:
    from openpype.modules.shotgrid.lib import credentials
    from openpype.modules.shotgrid.scripts import populate_tasks


logger = Logger.get_logger(__name__)


REVIEW_FAMILIES = {
    "render",
    "reference",
    "plate",
    "review"
}

PUBLISH_TO_SG_FAMILIES = {
    "render",
    "review",
    "reference",
}

IGNORE_LUT_FAMILIES = {
    "reference",
    "review",
}

TASKS_TO_IGNORE_REVIEW = {
    "3dtrack"
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
        sg_entity_type = asset_doc["data"].get("sgEntityType") or "Shot"
        sg_entity = sg.find_one(sg_entity_type, [["code", "is", asset_doc["name"]]], ["code"])
        populate_tasks.add_tasks_to_sg_entities(
            sg_project,
            [sg_entity],
            sg_entity_type,
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
        "colorspace": publish_data.get("src_colorspace", "scene_linear"),
        "version": publish_data.get("version"),
        "outputDir": os.path.dirname(source_path),
    }

    logger.debug("Getting representations...")
    representations = utils.get_representations(
        instance_data,
        expected_representations,
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

    asset_doc = get_asset_by_name(
        project_name, asset_name, fields=["_id", "data", "name"]
    )
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
        "colorspace": publish_data.get("src_colorspace", "scene_linear"),
        "version": publish_data.get("version"),
        "outputDir": os.path.dirname(source_path),
        "convertToScanline": publish_data.get("convertToScanline", False),
    }

    logger.debug("Getting representations...")

    add_review = family_name in REVIEW_FAMILIES

    # Quick dirty solution to avoid generating reviews for certain
    # tasks
    if task_name in TASKS_TO_IGNORE_REVIEW:
        add_review = False

    representations = utils.get_representations(
        instance_data,
        expected_representations,
        add_review=add_review,
        publish_to_sg=family_name in PUBLISH_TO_SG_FAMILIES,
    )
    if not representations:
        msg = f"{item_str} -> No representations could be found on expected dictionary: {expected_representations}"
        logger.error(msg)
        return msg, False

    # Get project code to grab the project code and add it to the task name
    project_doc = get_project(
        project_name, fields=["data.code", "config.tasks"]
    )
    project_code = project_doc["data"]["code"]

    deadline_task_name = "Publish {} - {}{} - {} - {} - {} ({})".format(
        family_name,
        subset_name,
        " v{0:03d}".format(int(instance_data.get("version"))) if instance_data.get("version") else "",
        task_name,
        asset_name,
        project_name,
        project_code,
    )

    # Fill instance data with anatomyData
    anatomy_data = get_template_data(
        project_doc, asset_doc, task_name
    )
    instance_data["anatomyData"] = anatomy_data

    # If we are generating a review, create a Deadline Nuke task for
    # the representation that is an image extension
    job_submissions = []
    if add_review:
        anatomy = Anatomy(project_name)

        for repre in representations:
            if repre["ext"] not in review.GENERATE_REVIEW_EXTENSIONS:
                continue

            staging_dir = anatomy.fill_root(
                repre["stagingDir"]
            )

            # Set output colorspace default to 'shot_lut' unless it's a review/reference family
            out_colorspace = "shot_lut"
            if family_name in IGNORE_LUT_FAMILIES:
                out_colorspace = ""

            # Create dictionary with some useful data required to submit
            # Nuke review job to the farm
            review_data = {
                "comment": publish_data.get("comment", ""),
                "batch_name": publish_data.get("jobBatchName") or deadline_task_name,
                "src_colorspace": publish_data.get("src_colorspace", "scene_linear"),
                # We default the output colorspace to out_colorspace if it's not
                # explicitly set on the publish_data dictionary
                "out_colorspace": publish_data.get("out_colorspace", out_colorspace)
            }

            # Create read path to pass to Nuke task
            basename = repre["files"][0] if isinstance(repre["files"], list) else repre["files"]
            read_path = os.path.join(staging_dir, basename)
            read_path = path_utils.replace_frame_number_with_token(read_path, "#", padding=True)
            logger.debug("Review read path: %s", read_path)

            # Create review output path
            file_name = f"{repre['name']}_h264.mov"
            output_path = os.path.join(
                staging_dir,
                file_name
            )
            logger.debug("Review output path: %s", output_path)

            response = review.generate_review(
                project_name,
                project_code,
                asset_name,
                task_name,
                read_path,
                output_path,
                repre["frameStart"],
                repre["frameEnd"],
                review_data
            )
            job_submissions.append(response)

            # Add review as a new representation to publish
            representations.append(
                {
                    "name": "h264",
                    "ext": "mov",
                    "files": file_name,
                    "frameStart": repre["frameStart"],
                    "frameEnd": repre["frameEnd"],
                    "stagingDir": staging_dir,
                    "fps": instance_data.get("fps"),
                    "tags": ["shotgridreview"],
                }
            )

            # We force it to only generate a review for the first representation
            # that supports it
            # TODO: in the future we might want to improve this if it's common
            # that we ingest multiple image representations
            break

    instance_data["frameStart"] = int(representations[0]["frameStart"])
    instance_data["frameEnd"] = int(representations[0]["frameEnd"])
    instance_data["frameStartHandle"] = int(representations[0]["frameStart"])
    instance_data["frameEndHandle"] = int(representations[0]["frameEnd"])

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
        "AVALON_WORKDIR": os.path.dirname(source_path),
        "AYON_SG_USER" if AYON_SERVER_ENABLED else "OPENPYPE_SG_USER": username,
        "AYON_PUBLISH_JOB" if AYON_SERVER_ENABLED else "OPENPYPE_PUBLISH_JOB": "1",
        "AYON_RENDER_JOB" if AYON_SERVER_ENABLED else "OPENPYPE_RENDER_JOB":  "0",
        "AYON_REMOTE_JOB" if AYON_SERVER_ENABLED else "OPENPYPE_REMOTE_JOB":  "0",
        "OPENPYPE_LOG_NO_COLORS": "1",
    }
    # Also add bundle name to submission
    if AYON_SERVER_ENABLED:
        extra_env["AYON_BUNDLE_NAME"] = os.getenv("AYON_BUNDLE_NAME")

    logger.debug("Submitting payload...")
    response = submit.payload_submit(
        plugin="Ayon" if AYON_SERVER_ENABLED else "OpenPype",
        plugin_data=plugin_data,
        batch_name=publish_data.get("jobBatchName") or deadline_task_name,
        task_name=deadline_task_name,
        group=dl_constants.AYON_GROUP if AYON_SERVER_ENABLED else dl_constants.OP_GROUP,
        extra_env=extra_env,
        job_dependencies=job_submissions
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
