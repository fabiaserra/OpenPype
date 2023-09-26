import os
import re

import shotgun_api3
import pyblish.api

from openpype.pipeline.publish import get_publish_repre_path


class IntegrateShotgridPublish(pyblish.api.InstancePlugin):
    """
    Create published Files from representations and add it to version. If
    representation is tagged as shotgrid review, it will add it in
    path to movie for a movie file or path to frame for an image sequence.
    """

    order = pyblish.api.IntegratorOrder + 0.499
    label = "Shotgrid Published Files"

    def process(self, instance):

        ### Starts Alkemy-X Override ###
        # Skip execution if instance is marked to be processed in the farm
        if instance.data.get("farm"):
            self.log.info(
                "Instance is marked to be processed on farm. Skipping")
            return
        ### Ends Alkemy-X Override ###

        context = instance.context

        self.sg = context.data.get("shotgridSession")

        shotgrid_version = instance.data.get("shotgridVersion")

        self.log.info("Integrating Shotgrid Published files...")

        for representation in instance.data.get("representations", []):

            local_path = get_publish_repre_path(
                instance, representation, False
            )

            ### Starts Alkemy-X Override ###
            if "shotgridreview" not in representation.get("tags", []):
                self.log.debug(
                    "No 'shotgridreview' tag on representation '%s', skipping.",
                    representation.get("name")
                )
                continue

            code = os.path.basename(local_path)
            # Extract and remove version number from code so Publishedfile versions are
            # grouped together. More info about this on:
            # https://developer.shotgridsoftware.com/tk-core/_modules/tank/util/shotgun/publish_creation.html
            version_number = 0
            match = re.search("_v(\d+)", code)
            if match:
                version_number = int(match.group(1))
                # Remove version from name
                code = re.sub("_v\d+", "", code)
                # Remove frames from name (i.e., filename.1001.exr -> filename.exr)
                code = re.sub("\.\d+", "", code)
            ### Ends Alkemy-X Override ###

            ### Starts Alkemy-X Override ###
            published_file = self._find_existing_publish(
                code, context, instance, shotgrid_version
            )
            ### Ends Alkemy-X Override ###

            published_file_data = {
                "project": context.data.get("shotgridProject"),
                "code": code,
                ### Starts Alkemy-X Override ###
                "entity": instance.data.get("shotgridEntity"),
                ### Ends Alkemy-X Override ###
                "task": context.data.get("shotgridTask"),
                "version": shotgrid_version,
                "path": {"local_path": local_path},
                ### Starts Alkemy-X Override ###
                # Add file type and version number fields
                "published_file_type": self._find_published_file_type(
                    local_path, representation
                ),
                "version_number": version_number,
                ### Ends Alkemy-X Override ###
            }
            ### Starts Alkemy-X Override ###
            # Catch exception so a SG error doesn't stop the publish process
            try:
                if not published_file:
                    published_file = self._create_published(published_file_data)
                    self.log.info(
                        "Create Shotgrid PublishedFile: {}".format(published_file)
                    )
                else:
                    self.sg.update(
                        published_file["type"],
                        published_file["id"],
                        published_file_data,
                    )
                    self.log.info(
                        "Update Shotgrid PublishedFile: {}".format(published_file)
                    )

                if instance.data["family"] == "image":
                    self.sg.upload_thumbnail(
                        published_file["type"], published_file["id"], local_path
                    )
                instance.data["shotgridPublishedFile"] = published_file
            except shotgun_api3.shotgun.Fault:
                self.log.warning(
                    "Couldn't upload published file with path '%s' to Shotgrid.",
                    local_path
                )
            ### Ends Alkemy-X Override ###

    ### Starts Alkemy-X Override ###
    def _find_published_file_type(self, filepath, representation):
        """Given a filepath infer what type of published file type it is."""

        _, ext = os.path.splitext(filepath)
        published_file_type = "Unknown"

        if ext in [".exr", ".jpg", ".jpeg", ".png", ".dpx", ".tif", ".tiff"]:
            is_sequence = len(representation["files"]) > 1
            if is_sequence:
                published_file_type = "Rendered Image"
            else:
                published_file_type = "Image"
        elif ext in [".mov", ".mp4"]:
            published_file_type = "Movie"
        elif ext == ".abc":
            published_file_type = "Alembic Cache"
        elif ext in [".bgeo", ".sc", ".gz"]:
            published_file_type = "Bgeo Geo"
        elif ext in [".ma", ".mb"]:
            published_file_type = "Maya Scene"
        elif ext == ".nk":
            published_file_type = "Nuke Script"
        elif ext == ".hip":
            published_file_type = "Houdini Scene"
        elif ext in [".hda"]:
            published_file_type = "HDA"
        elif ext in [".fbx"]:
            published_file_type = "FBX Geo"

        filters = [["code", "is", published_file_type]]
        sg_published_file_type = self.sg.find_one(
            "PublishedFileType", filters=filters
        )
        if not sg_published_file_type:
            # create a published file type on the fly
            sg_published_file_type = self.sg.create(
                "PublishedFileType", {"code": published_file_type}
            )
        return sg_published_file_type
    ### Ends Alkemy-X Override ###

    ### Starts Alkemy-X Override ###
    def _find_existing_publish(self, code, context, instance, shotgrid_version):
    ### Ends Alkemy-X Override ###

        filters = [
            ["project", "is", context.data.get("shotgridProject")],
            ["task", "is", context.data.get("shotgridTask")],
            ### Startss Alkemy-X Override ###
            ["entity", "is", instance.data.get("shotgridEntity")],
            ### Ends Alkemy-X Override ###
            ["version", "is", shotgrid_version],
            ["code", "is", code],
        ]
        return self.sg.find_one("PublishedFile", filters, [])

    def _create_published(self, published_file_data):

        return self.sg.create("PublishedFile", published_file_data)
