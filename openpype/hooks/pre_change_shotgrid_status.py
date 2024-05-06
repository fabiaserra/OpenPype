from collections import OrderedDict

from openpype.lib.applications import PreLaunchHook, LaunchTypes

from openpype.modules.shotgrid.lib import credentials


class ShotgridStatusHook(PreLaunchHook):
    """Change Shotgrid task status when starting scene."""

    order = 50
    launch_types = {LaunchTypes.local}

    sg_entity_to_env_map = OrderedDict([
        ("Asset", "ASSET_TYPE"),
        ("Shot", "SHOT"),
        ("Sequence", "SEQ"),
        ("Episode", "EPISODE"),
        ("Season", "SEASON"),
    ])

    def execute(self):
        """Hook entry method."""
        project = self.data["env"].get("SHOW")

        sg = credentials.get_shotgrid_session()
        sg_project = sg.find_one(
            "Project",
            [["sg_code", "is", project]]
        )

        current_entity_name = None
        current_entity_type = None

        for sg_ent_type, env_value in self.sg_entity_to_env_map.items():
            entity_name = self.data["env"].get(env_value)
            if entity_name:
                current_entity_name = entity_name
                current_entity_type = sg_ent_type
                break

        if not current_entity_name:
            return

        current_sg_entity = sg.find_one(
            current_entity_type,
            [
                ["project", "is", sg_project],
                ["code", "is", current_entity_name]
            ]
        )
        if not current_sg_entity:
            self.log.warning(
                "Couldn't find SG entity type '%s' with name '%s'",
                current_entity_type,
                current_entity_name
            )
            return

        # Add SG type, name and id to the env so it's easier to find it later
        self.data["env"]["SG_ENTITY_TYPE"] = current_entity_type
        self.data["env"]["SG_ENTITY_NAME"] = current_entity_name
        self.data["env"]["SG_ENTITY_ID"] = current_sg_entity["id"]

        task_name = self.data["task_name"]
        sg_task = sg.find_one(
            "Task",
            [
                ["project", "is", sg_project],
                ["entity", "is", current_sg_entity],
                ["content", "is", task_name]
            ],
            ["code", "sg_status_list", "id"]
        )

        if sg_task.get("sg_status_list") in ["wtg", "rdy"]:
            self.log.info("Updating task status to 'In Progress'")
            try:
                sg.update("Task", sg_task["id"], {"sg_status_list": "ip"})
            except Exception as e:
                self.log.warning("Couldn't update the Task status: %s", e)
