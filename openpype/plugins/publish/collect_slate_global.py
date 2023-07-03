import os
import json
import pyblish.api

from openpype import resources
from openpype.pipeline import Anatomy


class CollectSlateGlobal(pyblish.api.InstancePlugin):
    """Inject the data needed to generate Slates in the enabled families."""
    label = "Collect for Slate Global workflow"
    order = pyblish.api.CollectorOrder + 0.499
    families = [
        "review",
        "render"
    ]

    def process(self, instance):

        context = instance.context
        slate_settings = context.data["project_settings"]["global"]\
            ["publish"].get("ExtractSlateGlobal")

        if not slate_settings:
            self.log.warning("No slate settings found. Skipping.")
            return

        if not slate_settings["enabled"]:
            self.log.warning("ExtractSlateGlobal is not active. Skipping.")
            return

        if instance.data.get("farm"):
            self.log.warning(
                "Skipping Slate Global Collect in Nuke context, defer to "
                "Deadline."
            )
            return

        self.log.info("ExtractSlateGlobal is active.")

        # Create dictionary of common data across all slates
        project_name = instance.data["anatomyData"]["project"]["name"]
        asset_name = instance.data["anatomyData"]["asset"]
        anatomy = Anatomy(project_name)
        frame_padding = anatomy.templates["work"].get("frame_padding")
        version_padding = anatomy.templates["work"].get("version_padding")

        slate_common_data = {
            "version": instance.data["version"],
            "@version": str(instance.data["version"]).zfill(version_padding),
            "frame_padding": frame_padding,
            "slate_title": project_name,
            "intent": {"label": "", "value": ""},
            "comment": "",
            "scope": "",
            "fps": context.data["projectEntity"]["data"].get("fps"),
        }
        slate_common_data.update(instance.data["anatomyData"])
        if "customData" in instance.data:
            slate_common_data.update(instance.data["customData"])

        # Collect possible delivery overrides
        delivery_template = "{asset}_{task[short]}_v{@version}"
        delivery_overrides_dict = context.data["shotgridDeliveryOverrides"]

        project_overrides = delivery_overrides_dict.get("project")
        if project_overrides:
            project_name = project_overrides.get("name")
            delivery_template = project_overrides.get("template") or delivery_template
            if project_name:
                slate_common_data["project"]["name"] = project_name
                slate_common_data["slate_title"] = project_name

        asset_overrides = delivery_overrides_dict.get("asset")
        if asset_overrides:
            asset_name = asset_overrides.get("name")
            delivery_template = asset_overrides.get("template") or delivery_template
            if asset_name:
                slate_common_data["asset"] = asset_name

        # Fill up slate subtitle field with all the data collected thus far
        slate_common_data["slate_subtitle"] = delivery_template.format(
            **slate_common_data
        )

        template_path = slate_settings["slate_template_path"].format(
            **os.environ
        )
        if not template_path:
            template_path = resources.get_resource(
                "slate_template", "generic_slate.html"
            )
            self.log.info(
                "No 'slate_template_path' found in project settings. "
                "Using default '%s'", template_path
            )

        resources_path = slate_settings["slate_resources_path"].format(
            **os.environ
        )
        if not resources_path:
            resources_path = resources.get_resource(
                "slate_template", "resources"
            )
            self.log.info(
                "No 'slate_resources_path' found in project settings. "
                "Using default '%s'", resources_path
            )

        slate_global = {
            "slate_template_path": template_path,
            "slate_resources_path": resources_path,
            "slate_profiles": slate_settings["profiles"],
            "slate_common_data": slate_common_data,
            "slate_thumbnail": "",
            "slate_repre_data": {},
            "slate_task_types": slate_settings["integrate_task_types"]
        }
        instance.data["slateGlobal"] = slate_global

        if "families" not in instance.data:
            instance.data["families"] = list()

        if "versionData" not in instance.data:
            instance.data["versionData"] = dict()

        if "families" not in instance.data["versionData"]:
            instance.data["versionData"]["families"] = list()

        task_type = instance.data["anatomyData"]["task"]["type"]
        if task_type in slate_settings["integrate_task_types"]:

            self.log.debug(
                "Task: %s is enabled for Extract Slate Global workflow, "
                "tagging for slate extraction on review families", task_type
            )

            instance.data["slate"] = True
            instance.data["families"].append("slate")
            instance.data["versionData"]["families"].append("slate")

            self.log.debug(
                "SlateGlobal Data: %s", json.dumps(
                    instance.data["slateGlobal"],
                    indent=4,
                    default=str
                )
            )
        else:
            self.log.debug(
                "Task: %s is disabled for Extract Slate Global workflow, "
                "skipping slate extraction on review families...", task_type
            )
