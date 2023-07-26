"""Utility module with functions related to the delivery pipeline using SG.
"""
import itertools
from collections import OrderedDict

from openpype.lib import Logger

logger = Logger.get_logger(__name__)

# List of SG fields from context entities (i.e., Project, Shot) that we care to
# query for delivery purposes
SG_DELIVERY_FIELDS = [
    "sg_delivery_name",
    "sg_delivery_template",
    "sg_slate_subtitle",
    # "sg_final_datatype",  # TODO: not used yet
    "sg_final_fps",
    "sg_final_output_type",
    "sg_final_tags",
    "sg_review_fps",
    "sg_review_lut",
    "sg_review_output_type",
    # "sg_review_reformat",  # TODO: not used yet
    # "sg_review_scale",  # TODO: not used yet
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

# Map of SG entities hierarchy from more specific to more generic with the
# field that we need to query the parent entity
SG_HIERARCHY_MAP = OrderedDict([
    ("Version", "entity"),
    ("Shot", "sg_sequence"),
    ("Sequence", "episode"),
    ("Episode", "project"),
    ("Project", None),
])

# List of delivery types that we support
DELIVERY_TYPES = ["review", "final"]


def get_sg_entity_overrides(sg, sg_entity):
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

    overrides_exist = False

    # Store overrides for all the SG delivery fields
    for delivery_field in SG_DELIVERY_FIELDS:
        override_value = sg_entity.get(delivery_field)
        if override_value:
            overrides_exist = True
            delivery_overrides[delivery_field] = override_value

    # Return early if no overrides exist on that entity
    if not overrides_exist:
        return delivery_overrides

    # Override the value for the sg_{delivery_type}_output key with
    # a dictionary of the ffmpeg args required to create each output
    # type
    for delivery_type in DELIVERY_TYPES:
        output_field = f"sg_{delivery_type}_output_type"
        # Clear existing overrides for output_types
        delivery_overrides[output_field] = {}
        out_data_types = sg_entity.get(output_field) or []
        for out_data_type in out_data_types:
            sg_out_data_type = sg.find_one(
                "CustomNonProjectEntity03",
                [["id", "is", out_data_type["id"]]],
                fields=SG_OUTPUT_DATATYPE_FIELDS,
            )
            representation_name = "{}_{}".format(
                out_data_type["name"].replace(" ", "").lower(),
                delivery_type
            )

            delivery_overrides[output_field][representation_name] = {}
            for field in SG_OUTPUT_DATATYPE_FIELDS:
                delivery_overrides[output_field]\
                    [representation_name][field] = sg_out_data_type.get(field)

    return delivery_overrides


def find_delivery_overrides(context, instance, include_current=True):
    """
    Find the delivery overrides for the given Shotgrid project and Shot.

    Args:
        context (dict): A dictionary containing the context information. It should have
            the following keys:
            - "shotgridSession": A Shotgrid session object.
            - "shotgridEntity": A dictionary containing information about the Shotgrid
                entity. It should have the following keys:
                - "id": The ID of the Shotgrid entity.
                - "type": The type of the Shotgrid entity.

    Returns:
        dict: A dictionary containing the delivery overrides, if found. The keys are:
            - "project": A dictionary with the keys "name" and "template", representing
                the delivery name and template for the Shotgrid project.
            - "asset": A dictionary with the keys "name" and "template", representing
                the delivery name and template for the Shot.

    """
    delivery_overrides = {}

    sg = context.data.get("shotgridSession")

    # Find SG entity corresponding to the current instance
    entity_id = instance.data["shotgridEntity"]["id"]
    entity_type = instance.data["shotgridEntity"]["type"]

    prior_sg_entity = None
    prior_entity_id = entity_id

    # Find the index on the hierarchy of the "prior" entity
    prior_entity_index = list(SG_HIERARCHY_MAP.keys()).index(entity_type)

    # If we also want to include the current entity on the overrides, we need to
    # shift the index one
    if include_current:
        prior_entity_index -= 1

    # Create two iterators with an offset of one so we can iterate over the hierarchy
    # of entities while also finding the query field from the "prior" entity
    # Example: In order to find "Sequence" entity, we need to query "sg_sequence" field
    # on the "Shot"
    prior_iterator = itertools.islice(SG_HIERARCHY_MAP.items(), prior_entity_index, None)
    iterator = itertools.islice(SG_HIERARCHY_MAP.items(), prior_entity_index + 1, None)

    # Create a dictionary of delivery overrides per entity
    for entity, query_field in iterator:

        query_fields = SG_DELIVERY_FIELDS.copy()
        if query_field:
            query_fields.append(query_field)

        # Find the query field for the entity above
        _, prior_query_field = next(prior_iterator, (None, None))

        if prior_sg_entity:
            prior_entity_id = prior_sg_entity[prior_query_field]["id"]

        sg_entity = sg.find_one(
            entity,
            [["id", "is", prior_entity_id]],
            query_fields,
        )
        if not sg_entity:
            logger.debug("No SG entity '%s' found" % entity)
            continue

        prior_sg_entity = sg_entity

        entity_overrides = get_sg_entity_overrides(
            sg, sg_entity
        )
        if not entity_overrides:
            continue

        delivery_overrides[entity] = entity_overrides
        logger.debug("Added delivery overrides for SG entity '%s'." % entity)

    return delivery_overrides
