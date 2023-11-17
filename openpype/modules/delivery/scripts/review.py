import os

from openpype.lib import Logger
from openpype.modules.deadline import constants as dl_constants
from openpype.modules.deadline.lib import submit


# TODO: Replace these with published Templates workflow
NUKE_REVIEW_PY = "/pipe/nuke/templates/review_template.py"
DEFAULT_NUKE_REVIEW_SCRIPT = "/pipe/nuke/templates/review_template.nk"
PROJ_NUKE_REVIEW_SCRIPT = "/proj/{proj_code}/resources/review/review_template.nk"

REVIEW_REPRESENTATION_NAME = "h264"

GENERATE_REVIEW_EXTENSIONS = {"exr", "jpg", "jpeg", "png", "dpx", "tif", "tiff"}

logger = Logger.get_logger(__name__)


def generate_review(
    project_name,
    proj_code,
    asset_name,
    task_name,
    read_path,
    output_path,
    frame_start,
    frame_end,
    review_data
):
    output_dir, output_filename = os.path.split(output_path)

    # Get the Nuke script to use to generate the review
    # First try to see if there's one set on the show, otherwise
    # we just use the default global one
    nuke_review_script = DEFAULT_NUKE_REVIEW_SCRIPT
    proj_review_script = PROJ_NUKE_REVIEW_SCRIPT.format(
        proj_code=proj_code
    )
    if os.path.exists(proj_review_script):
        nuke_review_script = proj_review_script
    else:
        logger.warning(
            "Project Nuke template for reviews not found at '%s'",
            nuke_review_script
        )

    # Add environment variables required to run Nuke script
    task_env = {
        "_AX_REVIEW_NUKESCRIPT": nuke_review_script,
        "_AX_REVIEW_FRAMES": "{0}_{1}".format(
            int(frame_start), int(frame_end)
        ),
        "_AX_REVIEW_READPATH": read_path,
        "_AX_REVIEW_WRITEPATH": output_path,
        "_AX_REVIEW_FILENAME": os.path.splitext(output_filename)[0],
        "_AX_REVIEW_OUTPUT_NAME": REVIEW_REPRESENTATION_NAME,
        "_AX_REVIEW_ARTIST": os.getenv("USER"),
        "_AX_REVIEW_READCOLORSPACE": review_data.get("colorspace") or "scene_linear",
        "_AX_REVIEW_COMMENT": review_data.get("comment", ""),
        "_AX_DEBUG_PATH": os.path.join(output_dir, "nuke_review_script"),
        "AVALON_TASK": task_name,
        "AVALON_ASSET": asset_name,
        "AVALON_PROJECT": project_name,
        "AVALON_APP": "nuke",
        "AVALON_APP_NAME": "nuke/14-03",
        "OPENPYPE_RENDER_JOB": "1",
    }

    # Create dictionary of data specific to Nuke plugin for payload submit
    plugin_data = {
        "ScriptJob": True,
        "SceneFile": NUKE_REVIEW_PY,
        "ScriptFilename": NUKE_REVIEW_PY,
        "Version": "14.0",
        "UseGpu": False,
        "OutputFilePath": output_dir,
    }

    logger.info("Submitting Nuke review generation")
    task_name = "Create SG Review - {} - {} - {} ({})".format(
        output_filename,
        asset_name,
        project_name,
        proj_code
    )

    response = submit.payload_submit(
        plugin="AxNuke",
        plugin_data=plugin_data,
        batch_name=review_data.get("batch_name", task_name),
        task_name=task_name,
        frame_range=(frame_start, frame_end),
        department="",
        group=dl_constants.NUKE_CPU_GROUP,
        comment=review_data.get("comment", ""),
        extra_env=task_env,
    )

    return response
