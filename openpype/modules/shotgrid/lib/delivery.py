"""Utility module with functions related to the delivery pipeline using SG.
"""
import itertools
from collections import OrderedDict

from openpype.lib import Logger

logger = Logger.get_logger(__name__)

# List of SG fields from context entities (i.e., Project, Shot) that we care to
# query for delivery purposes
SG_EXTRA_DELIVERY_FIELDS = [
    "sg_delivery_template",
    "sg_slate_subtitle",
    # "sg_final_datatype",  # TODO: not used yet
    "sg_final_fps",
    "sg_final_tags",
    "sg_review_fps",
    "sg_review_lut",
    "sg_review_tags",
    # "sg_review_reformat",  # TODO: not used yet
    # "sg_review_scale",  # TODO: not used yet
]

SG_DELIVERY_NAME_FIELD = "sg_delivery_name"

SG_DELIVERY_OUTPUT_FIELDS = [
    "sg_final_output_type",
    "sg_review_output_type",
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
SG_HIERARCHY_MAP = OrderedDict(
    [
        ("Version", "entity"),
        ("Shot", "sg_sequence"),
        ("Sequence", "episode"),
        ("Episode", "project"),
        ("Project", None),
    ]
)


def get_representation_names_from_overrides(
    delivery_overrides, delivery_types
):
    representation_names = []
    for entity in SG_HIERARCHY_MAP.keys():
        entity_overrides = delivery_overrides.get(entity)
        if not entity_overrides:
            continue
        for delivery_type in delivery_types:
            delivery_rep_names = entity_overrides[f"sg_{delivery_type}_output_type"]
            representation_names.extend(delivery_rep_names)

        return representation_names, entity

    return [], None


def get_representation_names(
    sg,
    entity_id,
    entity_type,
    delivery_types,
):
    delivery_overrides = get_entity_hierarchy_overrides(
        sg,
        entity_id,
        entity_type,
        delivery_types,
        query_representation_names=True,
        stop_when_found=True
    )
    return get_representation_names_from_overrides(delivery_overrides, delivery_types)


def get_entity_overrides(
    sg, sg_entity, delivery_types, query_fields, query_ffmpeg_args=False
):
    """Create a dictionary of relevant delivery fields for the given SG entity.

    The returned dictionary includes overrides for the delivery fields defined
    in SG_EXTRA_DELIVERY_FIELDS, as well as overrides for the sg_review_output and
    sg_final_output fields. The value for each of these fields is a dictionary
    of the ffmpeg arguments required to create each output type.

    Args:
        sg_entity (dict): The Shotgrid entity to get overrides for.

    Returns:
        dict: A dictionary of overrides for the given Shotgrid entity.
    """
    overrides_exist = False

    # Store overrides for all the SG delivery fields
    delivery_overrides = {}
    for delivery_field in query_fields:
        override_value = sg_entity.get(delivery_field)
        if override_value:
            overrides_exist = True
            # For the output_type values, we just set the names
            if delivery_field.endswith("output_type"):
                delivery_type = "review" if "review" in delivery_field else "final"
                override_value = [
                    f"{v['name'].replace(' ', '').lower()}_{delivery_type}"
                    for v in override_value
                ]
            delivery_overrides[delivery_field] = override_value

    # Return early if no overrides exist on that entity
    if not overrides_exist:
        return {}

    # If we are not querying the output type arguments we can return already
    if not query_ffmpeg_args:
        return delivery_overrides

    # Otherwise we query the arguments of the output data types and update
    # the delivery overrides dict with it
    output_ffmpeg_args = get_output_type_ffmpeg_args(sg, sg_entity, delivery_types)
    delivery_overrides.update(output_ffmpeg_args)
    return delivery_overrides


def get_output_type_ffmpeg_args(sg, sg_entity, delivery_types):
    # Create a dictionary with sg_{delivery_type}_output keys and values
    # a dictionary of the ffmpeg args required to create each output
    # type
    output_ffmpeg_args = {}
    for delivery_type in delivery_types:
        output_field = f"sg_{delivery_type}_output_type"
        output_ffmpeg_args[output_field] = {}
        out_data_types = sg_entity.get(output_field) or []
        for out_data_type in out_data_types:
            representation_name = "{}_{}".format(
                out_data_type["name"].replace(" ", "").lower(), delivery_type
            )
            sg_out_data_type = sg.find_one(
                "CustomNonProjectEntity03",
                [["id", "is", out_data_type["id"]]],
                fields=SG_OUTPUT_DATATYPE_FIELDS,
            )
            output_ffmpeg_args[output_field][representation_name] = {}
            for field in SG_OUTPUT_DATATYPE_FIELDS:
                output_ffmpeg_args[output_field][representation_name][
                    field
                ] = sg_out_data_type.get(field)

    return output_ffmpeg_args


def get_entity_hierarchy_overrides(
    sg,
    entity_id,
    entity_type,
    delivery_types,
    query_delivery_names=False,
    query_representation_names=False,
    query_extra_delivery_fields=False,
    query_ffmpeg_args=False,
    stop_when_found=False,
):
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

    # Find the index on the hierarchy of the current entity
    entity_index = list(SG_HIERARCHY_MAP.keys()).index(entity_type)

    # Create an iterator object starting at the current entity index
    # We are also creating an iterator object so we can manually control
    # its iterations within the for loop
    iterator = itertools.islice(SG_HIERARCHY_MAP.items(), entity_index, None)

    base_query_fields = []

    if query_representation_names:
        base_query_fields.extend(SG_DELIVERY_OUTPUT_FIELDS)

    if query_delivery_names:
        base_query_fields.append(SG_DELIVERY_NAME_FIELD)

    if query_extra_delivery_fields:
        base_query_fields.extend(SG_EXTRA_DELIVERY_FIELDS)

    # If we are not requesting all delivery types we only keep the fields
    # that are specific to the delivery type being requested
    if len(delivery_types) == 1:
        base_query_fields = [f for f in base_query_fields if delivery_types[0] in f]

    # Create a dictionary of delivery overrides per entity
    for entity, query_field in iterator:
        query_fields = base_query_fields.copy()
        if query_field:
            query_fields.append(query_field)

        # Keep querying the hierarchy of entities until we find one
        available_parents = True
        while available_parents:
            logger.debug(
                "Querying entity '%s' with id '%s' and query field '%s'",
                entity,
                entity_id,
                query_field,
            )
            sg_entity = sg.find_one(
                entity,
                [["id", "is", entity_id]],
                query_fields,
            )
            logger.debug("SG Entity: %s", sg_entity)

            # If we are querying the highest entity on the hierarchy
            # No need to check for its parent
            if entity == "Project":
                available_parents = False
                break

            # If parent entity is found, we break the while loop
            # otherwise we query the next one
            sg_parent_entity = sg_entity[query_field]
            if sg_parent_entity:
                entity_id = sg_parent_entity["id"]
                break

            logger.debug(
                "SG entity '%s' doesn't have a '%s' linked, querying the next parent",
                entity,
                query_field,
            )

            # Skip an iteration
            _, query_field = next(iterator, (None, None))
            if not query_field:
                # This shouldn't happen but we have it in case we run out of
                # parent entities to query to avoid an endless loop
                available_parents = False
                break

            query_fields.append(query_field)

        entity_overrides = get_entity_overrides(
            sg, sg_entity, delivery_types, base_query_fields, query_ffmpeg_args
        )
        if not entity_overrides:
            continue

        delivery_overrides[entity] = entity_overrides
        logger.debug("Added delivery overrides for SG entity '%s'." % entity)
        if stop_when_found:
            return delivery_overrides

    return delivery_overrides
