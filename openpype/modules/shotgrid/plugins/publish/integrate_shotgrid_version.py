import re
import pyblish.api

from openpype.pipeline.publish import get_publish_repre_path


class IntegrateShotgridVersion(pyblish.api.InstancePlugin):
    """Integrate Shotgrid Version"""

    order = pyblish.api.IntegratorOrder + 0.497
    label = "Shotgrid Version"
    ### Starts Alkemy-X Override ###
    fields_to_add = {
        "frameStart": (int, "sg_first_frame"),
        "frameEnd": (int, "sg_last_frame"),
        "comment": (str, "description"),
        "family": (str, "sg_version_type"),
    }
    ### Ends Alkemy-X Override ###

    sg = None

    def process(self, instance):
        # Instance should be integrated on a farm
        if instance.data.get("farm"):
            self.log.info(
                "Instance is marked to be processed on farm. Skipping")
            return

        context = instance.context
        self.sg = context.data.get("shotgridSession")

        # TODO: Use path template solver to build version code from settings
        anatomy = instance.data.get("anatomyData", {})
        ### Starts Alkemy-X Override ###
        code = "{}_{}_{}".format(
            anatomy["asset"],
            instance.data["subset"],
            "v{:03}".format(int(anatomy["version"]))
        )
        self.log.info("Integrating Shotgrid version with code: {}".format(code))
        ### Ends Alkemy-X Override ###

        ### Starts Alkemy-X Override ###
        version = self._find_existing_version(code, context, instance)
        ### Ends Alkemy-X Override ###

        if not version:
            ### Starts Alkemy-X Override ###
            version = self._create_version(code, context, instance)
            ### Ends Alkemy-X Override ###
            self.log.info("Create Shotgrid version: {}".format(version))
        else:
            self.log.info("Use existing Shotgrid version: {}".format(version))

        data_to_update = {}
        intent = context.data.get("intent")
        if intent:
            data_to_update["sg_status_list"] = intent["value"]

        ### Starts Alkemy-X Override ###
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

        for representation in instance.data.get("representations", []):
            local_path = get_publish_repre_path(
                instance, representation, False
            )
            self.log.debug(
                "Checking whether to integrate representation '%s'.", representation
            )
            if "shotgridreview" in representation.get("tags", []):
                self.log.debug("Integrating representation")
                if representation["ext"] in ["mov", "avi", "mp4"]:
                    self.log.info(
                        "Upload review: {} for version shotgrid {}".format(
                            local_path, version.get("id")
                        )
                    )
                    self.sg.upload(
                        "Version",
                        version.get("id"),
                        local_path,
                        field_name="sg_uploaded_movie",
                    )

                    data_to_update["sg_path_to_movie"] = local_path

                elif representation["ext"] in ["jpg", "png", "exr", "tga"]:
                    # Define the pattern to match the frame number
                    padding_pattern = r"\.\d+\."
                    # Replace the frame number with '%04d'
                    path_to_frame = re.sub(padding_pattern, ".%04d.", local_path)
                    data_to_update["sg_path_to_frames"] = path_to_frame

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
        return self.sg.find_one("Version", filters, [])

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
