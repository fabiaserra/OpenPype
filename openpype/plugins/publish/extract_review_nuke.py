import os
import pyblish.api

from openpype.pipeline import publish
from openpype.modules.delivery.scripts import utils, review


class ExtractReviewNuke(publish.Extractor):
    """Generate review media through a Nuke deadline task"""

    order = pyblish.api.ExtractorOrder + 0.02
    label = "Extract Review Nuke"
    families = ["review"]

    def process(self, instance):
        """Submit a job to the farm to generate a mov review media to upload to SG"""

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

        anatomy = context.data["anatomy"]
        staging_dir = anatomy.fill_root_with_path(
            instance.data["outputDir"]
        )

        base_path = None
        output_path = None
        src_colorspace = None

        # If there's job dependencies it means there's a prior Deadline task that might be
        # generating files that will be used to generate the review (i.e., transcode frames)
        job_dependencies = instance.data.get("deadlineSubmissionJobs")
        if job_dependencies:
            base_path = instance.data["expectedFiles"][0]
            output_path = "{}_h264.mov".format(base_path.split(".", 1)[0])
            src_colorspace = instance.data["colorspace"]
        # Otherwise we just iterate from the created representations to generate the review from
        else:
            self.log.info("No Deadline job dependencies, checking instance representations.")
            for repre in self.get_review_representations(instance):
                # Create read path
                basename = repre["files"][0] if isinstance(repre["files"], list) else repre["files"]
                base_path = os.path.join(staging_dir, basename)

                # Create review output path
                output_path = os.path.join(
                    staging_dir,
                    f"{repre['name']}_h264.mov"
                )

                # Get source colorspace from representation
                colorspace_data = repre.get("colorspaceData")
                if colorspace_data:
                    src_colorspace = colorspace_data["colorspace"]

                break

        if not (base_path and output_path):
            self.log.info(
                "Skipping review generation as it couldn't find any representations to generate from."
            )
            return

        read_path = utils.replace_frame_number_with_token(base_path, "####")

        # Name to use for batch grouping Deadline tasks
        batch_name = instance.data.get("deadlineBatchName") or os.path.splitext(
            os.path.basename(context.data.get("currentFile")))[0]

        # Grab frame start/end
        frame_start = instance.data["frameStart"]
        frame_end = instance.data["frameEnd"]

        self.log.debug("Output path: %s", output_path)

        # Create dictionary with other useful data required to submit
        # Nuke review job to the farm
        review_data = {
            "comment": instance.data.get("comment", ""),
            "batch_name": batch_name
        }

        # Add source colorspace if it's set on the representation
        if src_colorspace:
            review_data["src_colorspace"] = src_colorspace

        # Submit job to the farm
        response = review.generate_review(
            os.getenv("AVALON_PROJECT"),
            os.getenv("SHOW"),
            instance.data["asset"],
            instance.data.get("task", os.getenv("AVALON_TASK")),
            read_path,
            output_path,
            frame_start,
            frame_end,
            review_data
        )

        # Adding the review file that will be generated to expected files
        if not instance.data.get("expectedFiles"):
            instance.data["expectedFiles"] = []

        instance.data["expectedFiles"].append(output_path)
        self.log.debug(
            "__ expectedFiles: `{}`".format(instance.data["expectedFiles"])
        )

        if job_dependencies:
            instance.data["deadlineSubmissionJobs"].append(response)
        else:
            instance.data["deadlineSubmissionJobs"] = [response]

    def get_review_representations(self, instance):
        for repre in instance.data["representations"]:
            repre_name = str(repre.get("name"))
            self.log.debug("Looking to see if we should generate review for '%s'", repre_name)

            tags = repre.get("tags") or []

            if repre_name == "exr_fr":
                self.log.debug("Full resolution representation, skipping.")
                continue

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

            if input_ext not in review.GENERATE_REVIEW_EXTENSIONS:
                self.log.info(
                    "Representation is not an image extension and doesn't need a revieww generated \"{}\"".format(
                        input_ext
                    )
                )
                continue

            yield repre
