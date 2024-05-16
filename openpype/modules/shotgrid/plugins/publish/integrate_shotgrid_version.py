"""This plugin has been modified quite enough by Alkemy-X to fit our needs that we
no longer add sandwiches on the overrides.

We have changed the logic so if there's no "data_to_update" at all we don't integrate
the version to SG at all.
"""
import re
import pyblish.api

from openpype.pipeline.publish import get_publish_repre_path
from openpype.lib.transcoding import (
    VIDEO_EXTENSIONS,
    IMAGE_EXTENSIONS
)
VIDEO_EXTENSIONS = set(ext.lstrip(".") for ext in VIDEO_EXTENSIONS)
IMAGE_EXTENSIONS = set(ext.lstrip(".") for ext in IMAGE_EXTENSIONS)


class IntegrateShotgridVersion(pyblish.api.InstancePlugin):
    """Integrate Shotgrid Version"""

    order = pyblish.api.IntegratorOrder + 0.497
    label = "Shotgrid Version"
    ### Starts Alkemy-X Override ###
    fields_to_add = {
        "comment": (str, "description"),
        "family": (str, "sg_version_type"),
    }
    ### Ends Alkemy-X Override ###

    sg = None

    def process(self, instance):
        # Skip execution if instance is marked to be processed in the farm
        if instance.data.get("farm"):
            self.log.info(
                "Instance is marked to be processed on farm. Skipping")
            return
        context = instance.context

        data_to_update = {}
        for representation in instance.data.get("representations", []):
            local_path = get_publish_repre_path(
                instance, representation, False
            )
            self.log.debug(
                "Checking whether to integrate representation '%s'.", representation
            )

            self.log.debug("Integrating representation")
            if representation["ext"] in VIDEO_EXTENSIONS:
                data_to_update["sg_path_to_movie"] = local_path
                ### Starts Alkemy-X Override ###
                if (
                    "slate" in instance.data["families"]
                    and "slate-frame" in representation["tags"]
                ):
                    data_to_update["sg_movie_has_slate"] = True
                ### Ends Alkemy-X Override ###

            elif representation["ext"] in IMAGE_EXTENSIONS:
                # Define the pattern to match the frame number
                padding_pattern = r"\.\d+\."
                # Replace the frame number with '%04d'
                path_to_frame = re.sub(padding_pattern, ".%04d.", local_path)

                data_to_update["sg_path_to_frames"] = path_to_frame
                ### Starts Alkemy-X Override ###
                if "slate" in instance.data["families"]:
                    data_to_update["sg_frames_have_slate"] = True
                ### Ends Alkemy-X Override ###

        if not data_to_update:
            self.log.info("No data to integrate to SG, skipping version creation.")
            return

        self.sg = context.data.get("shotgridSession")

        # TODO: Use path template solver to build version code from settings
        anatomy = instance.data.get("anatomyData", {})
        code = "{}_{}_{}".format(
            anatomy["asset"],
            instance.data["subset"],
            "v{:03}".format(int(anatomy["version"]))
        )
        self.log.info("Integrating Shotgrid version with code: {}".format(code))

        version = self._find_existing_version(code, context, instance)

        if not version:
            version = self._create_version(code, context, instance)
            self.log.info("Created Shotgrid version: {}".format(version))
        else:
            self.log.info("Using existing Shotgrid version: {}".format(version))

        # Upload movie to version
        path_to_movie = data_to_update.get("sg_path_to_movie")
        if path_to_movie:
            self.log.info(
                "Upload review: {} for version shotgrid {}".format(
                    path_to_movie, version.get("id")
                )
            )
            self.sg.upload(
                "Version",
                version.get("id"),
                path_to_movie,
                field_name="sg_uploaded_movie",
            )

        intent = context.data.get("intent")
        if intent:
            data_to_update["sg_status_list"] = intent["value"]

        ### Starts Alkemy-X Override ###
        frame_start = instance.data.get("frameStart", context.data.get("frameStart"))
        frame_end = instance.data.get("frameEnd", context.data.get("frameEnd"))
        handle_start = instance.data.get("handleStart", 0)
        handle_end = instance.data.get("handleEnd", 0)
        if frame_start != None and handle_start != None:
            frame_start = int(frame_start)
            handle_start = int(handle_start)
            data_to_update["sg_first_frame"] = frame_start - handle_start
            self.log.info("Adding field '{}' to SG as '{}':'{}'".format(
                    "frameStart", "sg_first_frame", frame_start - handle_start)
                )
        if frame_end != None and handle_end != None:
            frame_end = int(frame_end)
            handle_end = int(handle_end)
            data_to_update["sg_last_frame"] = frame_end + handle_end
            self.log.info("Adding field '{}' to SG as '{}':'{}'".format(
                    "frameEnd", "sg_last_frame", frame_end + handle_end)
                )
        # Add a few extra fields from OP to SG version
        for op_field, sg_field in self.fields_to_add.items():
            field_value = instance.data.get(op_field) or context.data.get(op_field)
            if field_value:
                # Break sg_field tuple into whatever type of data it is and its name
                type_, field_name = sg_field
                self.log.info("Adding field '{}' to SG as '{}':'{}'".format(
                    op_field, field_name, field_value)
                )
                data_to_update[field_name] = type_(field_value)

        # Add version objectId to "sg_op_instance_id" so we can keep a link
        # between both
        version_entity = instance.data.get("versionEntity", {}).get("_id")
        if not version_entity:
            self.log.warning(
                "Instance doesn't have a 'versionEntity' to extract the id."
            )
            version_entity = "-"
        data_to_update["sg_op_instance_id"] = str(version_entity)
        ### Ends Alkemy-X Override ###

        self.log.info("Updating Shotgrid version with {}".format(data_to_update))
        self.sg.update("Version", version["id"], data_to_update)

        instance.data["shotgridVersion"] = version

    ### Starts Alkemy-X Override ###
    def _find_existing_version(self, code, context, instance):
    ### Ends Alkemy-X Override ###
        filters = [
            ["project", "is", context.data.get("shotgridProject")],
            ["sg_task", "is", context.data.get("shotgridTask")],
            ### Starts Alkemy-X Override ###
            ["entity", "is", instance.data.get("shotgridEntity")],
            ### Ends Alkemy-X Override ###
            ["code", "is", code],
        ]
        return self.sg.find_one("Version", filters, ["entity"])

    ### Starts Alkemy-X Override ###
    def _create_version(self, code, context, instance):
    ### Ends Alkemy-X Override ###
        version_data = {
            "project": context.data.get("shotgridProject"),
            "sg_task": context.data.get("shotgridTask"),
            ### Starts Alkemy-X Override ###
            "entity": instance.data.get("shotgridEntity"),
            ### Ends Alkemy-X Override ###
            "code": code,
        }
        return self.sg.create("Version", version_data)
