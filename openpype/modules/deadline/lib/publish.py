import os
import re
import getpass
import json

from openpype.lib import Logger
from openpype.pipeline import legacy_io
from openpype.client import get_asset_by_name

from openpype.modules.deadline import constants as dl_constants
from openpype.modules.deadline.lib import submit
from openpype.modules.delivery.scripts import utils


logger = Logger.get_logger(__name__)


def publish_version(
    project_name,
    asset_name,
    task_name,
    family_name,
    subset_name,
    expected_representations,
    publish_data,
):
    # asset_entity = get_asset_by_name(project_name, asset_name)
    # context_data = asset_entity["data"]
    context_data = {}

    # # Make sure input path frames are replaced with hashes
    # source_path = re.sub(
    #     r"\d+(?=\.\w+$)", lambda m: "#" * len(m.group()) if m.group() else "#",
    #     source_path
    # )

    # out_frame_start = int(
    #     publish_data["frameStart"] - publish_data.get("handleStart", 0)
    # )
    # out_frame_end = int(
    #     publish_data["frameEnd"] + publish_data.get("handleEnd", 0)
    # )

    # TODO: write some logic that finds the main path from the list of
    # representations
    source_path = expected_representations.values()[0]

    instance_data = {
        "project": project_name,
        "family": family_name,
        "subset": subset_name,
        "families": publish_data.get("families", []),
        "asset": asset_name,
        "task": task_name,
        # "frameStart": publish_data.get(
            # "frameStart", context_data.get("frameStart")
        # ),
        # "frameEnd": publish_data.get("frameEnd", context_data.get("frameEnd")),
        # "handleStart": publish_data.get("handleEnd", 0),
        # "handleEnd": publish_data.get("handleEnd", 0),
        # "frameStartHandle": out_frame_start,
        # "frameEndHandle": out_frame_end,
        "comment": publish_data.get("comment", ""),
        "source": source_path,
        "overrideExistingFrame": False,
        # "jobBatchName": "Publish - {} - {} - {}".format(
        #     subset_name,
        #     asset_name,
        #     project_name
        # ),
        "useSequenceForReview": True,
        "colorspace": publish_data.get("colorspace"),
        "version": publish_data.get("version"),
        # "outputDir": render_path,
    }

    representations = utils.get_representations(
        instance_data,
        expected_representations,
        add_review=publish_data.get("add_review", True),
        publish_to_sg=publish_data.get("publish_to_sg", True),
    )

    # inject colorspace data
    for rep in representations:
        source_colorspace = publish_data.get("colorspace") or "scene_linear"
        logger.debug(
            "Setting colorspace '%s' to representation", source_colorspace
        )
        utils.set_representation_colorspace(
            rep, project_name, colorspace=source_colorspace
        )

    # add representation
    instance_data["representations"] = representations
    instances = [instance_data]

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
    }

    # Submit job to Deadline
    task_name = "Publish {} - {} - {} - {}".format(
        family_name,
        subset_name,
        asset_name,
        project_name
    )

    extra_env = {
        "AVALON_ASSET": asset_name,
        "AVALON_TASK": task_name,
        "AVALON_WORKDIR": os.path.dirname(source_path),
        "AVALON_PROJECT": project_name,
        "OPENPYPE_PUBLISH_JOB": "1",
    }

    response = submit.payload_submit(
        plugin="OpenPype",
        plugin_data=plugin_data,
        batch_name=publish_data.get("jobBatchName") or task_name,
        task_name=task_name,
        group=dl_constants.OP_GROUP,
        extra_env=extra_env,
    )

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
        "job": None,
        "session": legacy_io.Session.copy(),
        "instances": instances,
        "deadline_publish_job_id": response.get("_id")
    }

    logger.info("Writing json file: {}".format(metadata_path))
    with open(metadata_path, "w") as f:
        json.dump(publish_job, f, indent=4, sort_keys=True)
