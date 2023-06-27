import os

import pyblish.api
from openpype.lib.mongo import OpenPypeMongoConnection


### Starts Alkemy-X Override ###
class CollectShotgridEntities(pyblish.api.InstancePlugin):
### Ends Alkemy-X Override ###
    """Collect shotgrid entities according to the current context"""

    order = pyblish.api.CollectorOrder + 0.498
    label = "Collect Shotgrid entities"

    ### Starts Alkemy-X Override ###
    def process(self, instance):
        context = instance.context
    ### Ends Alkemy-X Override ###

        avalon_project = context.data.get("projectEntity")
        avalon_asset = context.data.get("assetEntity") or instance.data.get(
            "assetEntity"
        )
        avalon_task_name = os.getenv("AVALON_TASK")

        self.log.info(avalon_project)
        self.log.info(avalon_asset)

        sg_project = _get_shotgrid_project(context)
        sg_task = _get_shotgrid_task(
            avalon_project,
            avalon_asset,
            avalon_task_name
        )
        sg_entity = _get_shotgrid_entity(avalon_project, avalon_asset)

        if sg_project:
            context.data["shotgridProject"] = sg_project
            self.log.info(
                "Collected corresponding shotgrid project : {}".format(
                    sg_project
                )
            )

        if sg_task:
            context.data["shotgridTask"] = sg_task
            self.log.info(
                "Collected corresponding shotgrid task : {}".format(sg_task)
            )

        if sg_entity:
            ### Starts Alkemy-X Override ###
            instance.data["shotgridEntity"] = sg_entity
            ### Ends Alkemy-X Override ###
            self.log.info(
                "Collected corresponding shotgrid entity : {}".format(sg_entity)
            )

        ### Starts Alkemy-X Override ###
        # Collect whether project and current asset have any delivery
        # name overrides
        delivery_names = _find_delivery_names(context, instance)
        context.data["shotgridDeliveryNames"] = delivery_names
        ### Ends Alkemy-X Override ###


def _find_delivery_names(context, instance):
    """Finds the delivery names for the given ShotGrid project and Shot.

    Args:
        context (dict): The context dictionary.
        instance (pyblish.api.Instance): The instance to process.

    Returns:
        dict: A dictionary containing the delivery names, if found. The keys are:
            - "delivery_project": The delivery name for the ShotGrid project.
            - "delivery_asset": The delivery name for the Shot.

    """
    delivery_names = {}
    sg = context.data.get("shotgridSession")
    sg_project = sg.find_one(
        "Project",
        [["id", "is", context.data["shotgridProject"]["id"]]],
        fields=["sg_delivery_name"]
    )
    if sg_project and sg_project.get("sg_delivery_name"):
        delivery_names["delivery_project"] = sg_project.get("sg_delivery_name")

    sg_shot = sg.find_one(
        "Shot",
        [
            ["project", "is", context.data["shotgridProject"]],
            ["id", "is", instance.data["shotgridEntity"]["id"]],
        ],
        fields=["sg_delivery_name"]
    )
    if sg_shot and sg_shot.get("sg_delivery_name"):
        delivery_names["delivery_asset"] = sg_shot.get("sg_delivery_name")

    return delivery_names


def _get_shotgrid_collection(project):
    client = OpenPypeMongoConnection.get_mongo_client()
    return client.get_database("shotgrid_openpype").get_collection(project)


def _get_shotgrid_project(context):
    shotgrid_project_id = context.data["project_settings"].get(
        "shotgrid_project_id"
    )
    ### Starts Alkemy-X Override ###
    if not shotgrid_project_id:
        shotgrid_data = context.data["project_settings"].get("shotgrid")
        if shotgrid_data:
            shotgrid_project_id = shotgrid_data.get("shotgrid_project_id")
    ### Ends Alkemy-X Override ###
    if shotgrid_project_id:
        return {"type": "Project", "id": shotgrid_project_id}
    return {}


def _get_shotgrid_task(avalon_project, avalon_asset, avalon_task):
    sg_col = _get_shotgrid_collection(avalon_project["name"])
    shotgrid_task_hierarchy_row = sg_col.find_one(
        {
            "type": "Task",
            "_id": {"$regex": "^" + avalon_task + "_[0-9]*"},
            "parent": {"$regex": ".*," + avalon_asset["name"] + ","},
        }
    )
    if shotgrid_task_hierarchy_row:
        return {"type": "Task", "id": shotgrid_task_hierarchy_row["src_id"]}
    return {}


def _get_shotgrid_entity(avalon_project, avalon_asset):
    sg_col = _get_shotgrid_collection(avalon_project["name"])
    shotgrid_entity_hierarchy_row = sg_col.find_one(
        {"_id": avalon_asset["name"]}
    )
    if shotgrid_entity_hierarchy_row:
        return {
            "type": shotgrid_entity_hierarchy_row["type"],
            "id": shotgrid_entity_hierarchy_row["src_id"],
        }
    return {}
