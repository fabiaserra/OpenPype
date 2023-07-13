import os

import pyblish.api
from openpype.lib.mongo import OpenPypeMongoConnection
### Starts Alkemy-X Override ###

# List of SG fields from context entities (i.e., Project, Shot) that we care to
# query for delivery purposes
SG_DELIVERY_FIELDS = [
    "sg_delivery_name",
    "sg_delivery_template",
    "sg_final_datatype",
    "sg_final_fps",
    "sg_final_output_type",
    "sg_final_tags",
    "sg_review_fps",
    "sg_review_lut",
    "sg_review_output_type",
    "sg_review_reformat",
    "sg_review_scale",
    "sg_review_tags",
]

# List of SG fields on the 'output_datatypes' entity that we care to query for
SG_OUTPUT_DATATYPE_FIELDS = [
    "sg_ffmpeg_input_args",
    "sg_ffmpeg_output_args",
    "sg_ffmpeg_video_filters",
    "sg_ffmpeg_audio_filters",
    "sg_extension",
]

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
        # Collect relevant data for review/delivery purposes
        delivery_overrides = _find_delivery_overrides(context, instance)
        self.log.info(
            "Collected delivery overrides : {}".format(delivery_overrides)
        )
        context.data["shotgridDeliveryOverrides"] = delivery_overrides


def _get_shotgrid_entity_overrides(sg, sg_entity):
    """Create a dictionary of relevant delivery fields for the given SG entity.

    The returned dictionary includes overrides for the delivery fields defined
    in SG_DELIVERY_FIELDS, as well as overrides for the sg_review_output and
    sg_final_output fields. The value for each of these fields is a dictionary
    of the ffmpeg arguments required to create each output type.

    Args:
        sg_entity (dict): The Shotgrid entity to get overrides for.

    Returns:
        dict: A dictionary of overrides for the given Shotgrid entity.
    """
    delivery_overrides = {}

    # Store overrides for all the SG delivery fields
    for delivery_field in SG_DELIVERY_FIELDS:
        delivery_overrides[delivery_field] = sg_entity.get(
            delivery_field
        )

    # Override the value for the sg_{delivery_type}_output key with
    # a dictionary of the ffmpeg args required to create each output
    # type
    for delivery_type in ["review", "final"]:
        delivery_overrides[f"sg_{delivery_type}_output_type"] = {}
        out_data_types = sg_entity.get(f"sg_{delivery_type}_output_type") or []
        for out_data_type in out_data_types:
            sg_out_data_type = sg.find_one(
                "CustomNonProjectEntity03",
                [["id", "is", out_data_type["id"]]],
                fields=SG_OUTPUT_DATATYPE_FIELDS,
            )
            out_name = "{}_{}".format(
                out_data_type["name"].replace(" ", "").lower(),
                delivery_type
            )
            delivery_overrides[f"sg_{delivery_type}_output_type"]\
                    [out_name] = {}
            for field in SG_OUTPUT_DATATYPE_FIELDS:
                delivery_overrides[f"sg_{delivery_type}_output_type"]\
                    [out_name][field] = sg_out_data_type.get(field)

    return delivery_overrides

def _find_delivery_overrides(context, instance):
    """Find the delivery overrides for the given SG project and Shot.

    Args:
        context (dict): The context dictionary.
        instance (pyblish.api.Instance): The instance to process.

    Returns:
        dict: A dictionary containing the delivery overrides, if found. The keys are:
            - "project": A dictionary with the keys "name" and "template", representing
                the delivery name and template for the SG project.
            - "asset": A dictionary with the keys "name" and "template", representing
                the delivery name and template for the Shot.

    """
    sg = context.data.get("shotgridSession")
    delivery_overrides = {}

    # Create a dictionary holding all the delivery overrides for the project
    sg_project = sg.find_one(
        "Project",
        [["id", "is", context.data["shotgridProject"]["id"]]],
        fields=SG_DELIVERY_FIELDS,
    )
    if sg_project:
        delivery_overrides["project"] = _get_shotgrid_entity_overrides(
            sg, sg_project
        )

    # Create a dictionary holding all the delivery overrides for the shot
    # TODO: In the future we will want to scale this to other hierarchy
    # entities (season, episode, sequence...)
    sg_shot = sg.find_one(
        "Shot",
        [
            ["project", "is", context.data["shotgridProject"]],
            ["id", "is", instance.data["shotgridEntity"]["id"]],
        ],
        fields=SG_DELIVERY_FIELDS,
    )
    if sg_shot:
        delivery_overrides["shot"] = _get_shotgrid_entity_overrides(
            sg, sg_shot
        )

    return delivery_overrides
### Ends Alkemy-X Override ###


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
