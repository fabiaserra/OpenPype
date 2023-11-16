import os
import glob
import pyblish.api

from openpype.pipeline import publish
from openpype.modules.deadline.lib import submit
from openpype.modules.deadline import constants as dl_constants
from openpype.modules.delivery.scripts import utils


class ExtractReviewNuke(publish.Extractor):
    """Generate review media through a Nuke deadline task"""

    order = pyblish.api.ExtractorOrder + 0.02
    label = "Extract Review Nuke"
    families = ["review"]

    # Supported extensions
    image_exts = ["exr", "jpg", "jpeg", "png", "dpx"]
    video_exts = ["mov", "mp4", "mxf"]
    supported_exts = image_exts + video_exts

    # TODO: Replace these with published Templates workflow
    nuke_review_py = "/pipe/nuke/templates/review_template.py"
    default_nuke_review_script = "/pipe/nuke/templates/review_template.nk"

    new_rep_name = "h264"

    def process(self, instance):
        """Submit a job to the farm to generate an mp4 review media"""

        # Skip review when requested
        if not instance.data.get("review", True):
            return

        if not instance.data.get("farm"):
            self.log.warning(
                "Extract review in Nuke only works when publishing in the farm."
            )
            return

        instance.data["toBeRenderedOn"] = "deadline"

        context = instance.context

        # # Set staging directory
        staging_dir = instance.data["outputDir"]
        # instance.data["stagingDir"] = staging_dir

        # # Create staging directory if it doesn't exist
        # try:
        #     if not os.path.isdir(staging_dir):
        #         os.makedirs(staging_dir, exist_ok=True)
        # except OSError:
        #     pass

        frame_start = instance.data["frameStart"]
        frame_end = instance.data["frameEnd"]

        # Name to use for batch grouping Deadline tasks
        batch_name = os.path.splitext(
            os.path.basename(instance.context.data.get("currentFile"))
        )[0]

        nuke_review_script = self.get_show_nuke_review_script()
        if not nuke_review_script:
            nuke_review_script = self.default_nuke_review_script

        # Add environment variables required to run Nuke script
        task_env = {
            "_AX_REVIEW_NUKESCRIPT": nuke_review_script,
            "_AX_REVIEW_FRAMES": "{0}_{1}".format(
                int(frame_start), int(frame_end)
            ),
            "_AX_REVIEW_ARTIST": os.getenv("USER"),
            "_AX_REVIEW_COMMENT": instance.data.get("comment", ""),
            "_AX_REVIEW_OUTPUT_NAME": self.new_rep_name,
            "_AX_DEBUG_PATH": os.path.join(staging_dir, "nuke_review_script"),
            "AVALON_TASK": instance.data["task"],
            "AVALON_ASSET": instance.data["asset"],
            "AVALON_PROJECT": os.getenv("AVALON_PROJECT"),
            "AVALON_APP": "nuke",
            "AVALON_APP_NAME": "nuke/14-03",
            "OPENPYPE_RENDER_JOB": "1",
        }

        submission_jobs = []
        for repre in self.get_review_representations(instance):

            out_filename = f"{repre['name']}_{self.new_rep_name}.mp4"
            output_path = os.path.join(
                staging_dir,
                out_filename
            )
            self.log.debug("Output path: %s", output_path)

            source_colorspace = "scene_linear"
            colorspace_data = repre.get("colorspaceData")
            if colorspace_data:
                source_colorspace = colorspace_data["colorspace"]

            # Add environment variables specific to this output
            basename = repre["files"][0] if isinstance(repre["files"], list) else repre["files"]
            read_path = os.path.join(staging_dir, basename)
            hashes_path = utils.replace_frame_number_with_token(read_path, "####")

            output_task_env = task_env.copy()
            output_task_env["_AX_REVIEW_READPATH"] = hashes_path
            output_task_env["_AX_REVIEW_WRITEPATH"] = output_path
            output_task_env["_AX_REVIEW_READCOLORSPACE"] = source_colorspace
            output_task_env["_AX_REVIEW_FILENAME"] = out_filename

            # Create dictionary of data specific to Nuke plugin for payload submit
            plugin_data = {
                "ScriptJob": True,
                "SceneFile": self.nuke_review_py,
                "ScriptFilename": self.nuke_review_py,
                "Version": "14.0",
                "UseGpu": False,
                "OutputFilePath": staging_dir,
            }

            self.log.info("Submitting Nuke review generation")
            task_name = "Create SG Review - {} - {} - {} ({})".format(
                out_filename,
                instance.data["asset"],
                os.getenv("AVALON_PROJECT"),
                os.getenv("SHOW")
            )
            response = submit.payload_submit(
                plugin="AxNuke",
                plugin_data=plugin_data,
                batch_name=batch_name,
                task_name=task_name,
                frame_range=(frame_start, frame_end),
                department="",
                group=dl_constants.NUKE_CPU_GROUP,
                comment=context.data.get("comment", ""),
                extra_env=output_task_env,
            )

            # Adding the review file that will be generated to expected files
            if not instance.data.get("expectedFiles"):
                instance.data["expectedFiles"] = []

            instance.data["expectedFiles"].append(output_path)
            self.log.debug(
                "__ expectedFiles: `{}`".format(instance.data["expectedFiles"])
            )
            submission_jobs.append(response)

        instance.data["deadlineSubmissionJobs"] = submission_jobs
        instance.data["publishJobState"] = "Suspended"
        # Store output dir for unified publisher (filesequence)
        # instance.data["outputDir"] = staging_dir

    def get_review_representations(self, instance):
        for repre in instance.data["representations"]:
            repre_name = str(repre.get("name"))
            self.log.debug("Looking to see if we should generate review for '%s'", repre_name)

            tags = repre.get("tags") or []

            if "review" not in tags:
                self.log.debug((
                    "Repre: {} - Didn't found \"review\" in tags. Skipping"
                ).format(repre_name))
                continue

            if "thumbnail" in tags:
                self.log.debug((
                    "Repre: {} - Found \"thumbnail\" in tags. Skipping"
                ).format(repre_name))
                continue

            if "passing" in tags:
                self.log.debug((
                    "Repre: {} - Found \"passing\" in tags. Skipping"
                ).format(repre_name))
                continue

            input_ext = repre["ext"]
            if input_ext.startswith("."):
                input_ext = input_ext[1:]

            if input_ext not in self.supported_exts:
                self.log.info(
                    "Representation has unsupported extension \"{}\"".format(
                        input_ext
                    )
                )
                continue

            yield repre

    def get_show_nuke_review_script(self):
        review_template = ""
        review_template_path = os.path.join(
            os.getenv("AX_PROJ_ROOT"),
            os.getenv("SHOW"),
            "resources",
            "review"
        )
        review_templates = sorted(glob.glob(review_template_path))
        if review_templates:
            review_template = review_templates[-1]

        return review_template
