"""Utility module with functions related to the delivery pipeline using SG.
"""
import itertools
from collections import OrderedDict

from openpype.lib import Logger

logger = Logger.get_logger(__name__)

# List of SG fields from context entities (i.e., Project, Shot) that we care to
# query for delivery purposes
SG_EXTRA_DELIVERY_FIELDS = [
    # "sg_final_datatype",  # TODO: not used yet
    "sg_final_fps",
    "sg_final_tags",
    "sg_review_fps",
    "sg_review_lut",
    "sg_review_tags",
    "sg_review_resolution",
    # "sg_review_reformat",  # TODO: not used yet
    # "sg_review_scale",  # TODO: not used yet
]

SG_DELIVERY_NAME_FIELD = "sg_delivery_name"

SG_DELIVERY_OUTPUT_FIELDS = [
    "sg_final_output_type",
    "sg_review_output_type",
]

SG_SLATE_FIELDS = [
    "sg_slate_subtitle",
]

# List of SG fields on the 'output_datatypes' entity that we care to query for
SG_OUTPUT_DATATYPE_FIELDS = [
    "sg_ffmpeg_input",
    "sg_ffmpeg_output",
    "sg_ffmpeg_video_filters",
    "sg_ffmpeg_audio_filters",
    "sg_extension",
]

# Shot hierarchy of SG entities from more specific to more generic with the
# corresponding field that we need to query its parent entity
SG_SHOT_HIERARCHY_MAP = OrderedDict(
    [
        ("Version", "entity"),
        ("Shot", "sg_sequence"),
        ("Sequence", "episode"),
        ("Episode", "project"),
        ("Project", None),
    ]
)

# Asset hierarchy of SG entities from more specific to more generic with the
# corresponding field that we need to query its parent entity
SG_ASSET_HIERARCHY_MAP = OrderedDict(
    [
        ("Version", "entity"),
        ("Asset", "project"),
        ("Project", None),
    ]
)


def get_representation_names_from_overrides(
    delivery_overrides, delivery_types
):
    """
    Returns a list of representation names based on a dictionary of delivery overrides.

    Args:
        delivery_overrides (dict): A dictionary of delivery overrides.
        delivery_types (list): A list of delivery types to search for.

    Returns:
        tuple: A tuple containing a list of representation names and the name of the
            entity where the override was found.
    """
    representation_names = []
    for entity in SG_SHOT_HIERARCHY_MAP.keys():
        entity_overrides = delivery_overrides.get(entity)
        if not entity_overrides:
            continue
        for delivery_type in delivery_types:
            output_names = entity_overrides.get(f"sg_{delivery_type}_output_type", [])
            # Convert list from output names to representation names
            delivery_rep_names = [
                f"{name.lower().replace(' ', '')}_{delivery_type}"
                for name in output_names
            ]
            representation_names.extend(delivery_rep_names)

        return representation_names, entity

    return [], None


def get_representation_names(
    sg,
    entity_id,
    entity_type,
    delivery_types,
):
    """
    Returns a list of representation names for a given SG entity and delivery types.

    Args:
        sg (shotgun_api3.Shotgun): A SG API instance.
        entity_id (int): The ID of the SG entity to get representation names for.
        entity_type (str): The type of the SG entity to get representation names for.
        delivery_types (list): A list of delivery types to search for.

    Returns:
        list: A list of representation names for the given SG entity and delivery types.
    """
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
            # For the values that are list of dictionaries, we only keep the
            # name (i.e., tags and output_type)
            if isinstance(override_value, list) and \
                    all(isinstance(item, dict) for item in override_value):
                override_value = [v["name"] for v in override_value]
            # For the sg_review_lut field, we ignore it if it's set to the default
            # of True as otherwise we will be always saving overrides for all
            # entities
            elif delivery_field == "sg_review_lut" and override_value:
                continue
            overrides_exist = True
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
            sg_out_data_type = sg.find_one(
                "CustomNonProjectEntity03",
                [["id", "is", out_data_type["id"]]],
                fields=SG_OUTPUT_DATATYPE_FIELDS,
            )
            output_ffmpeg_args[output_field][out_data_type["name"]] = {}
            for field in SG_OUTPUT_DATATYPE_FIELDS:
                output_ffmpeg_args[output_field][out_data_type["name"]][
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
    query_slate_fields=False,
    query_ffmpeg_args=False,
    stop_when_found=False,
):
    """
    Find the whole hierarchy of delivery overrides for the given SG entity and delivery
    types.

    Args:
        sg (shotgun_api3.Shotgun): A SG API instance.
        entity_id (int): The ID of the ShotGrid entity to start the search from.
        entity_type (str): The type of the ShotGrid entity to start the search from.
        delivery_types (list): A list of delivery types to search for.
        query_delivery_names (bool): Whether to query delivery names.
        query_representation_names (bool): Whether to query representation names.
        query_extra_delivery_fields (bool): Whether to query extra delivery fields.
        query_slate_fields (bool): Whether to query slate fields.
        query_ffmpeg_args (bool): Whether to query ffmpeg arguments of the output types.
        stop_when_found (bool): Whether to stop searching when a delivery override is
            found.

    Returns:
        dict: A dictionary containing the delivery overrides, if found. The keys are
            the entity names and the values are dictionaries containing the delivery
            overrides for each entity.
    """
    delivery_overrides = {}

    # Find the index on the hierarchy of the current entity
    if entity_type == "Asset":
        entity_index = list(SG_ASSET_HIERARCHY_MAP.keys()).index(entity_type)
        # Create an iterator object starting at the current entity index
        # We are also creating an iterator object so we can manually control
        # its iterations within the for loop
        iterator = itertools.islice(SG_ASSET_HIERARCHY_MAP.items(), entity_index, None)
    else:
        entity_index = list(SG_SHOT_HIERARCHY_MAP.keys()).index(entity_type)
        # Create an iterator object starting at the current entity index
        # We are also creating an iterator object so we can manually control
        # its iterations within the for loop
        iterator = itertools.islice(SG_SHOT_HIERARCHY_MAP.items(), entity_index, None)

    base_query_fields = []

    if query_representation_names or query_ffmpeg_args:
        base_query_fields.extend(SG_DELIVERY_OUTPUT_FIELDS)

    if query_delivery_names:
        base_query_fields.append(SG_DELIVERY_NAME_FIELD)

    if query_extra_delivery_fields:
        base_query_fields.extend(SG_EXTRA_DELIVERY_FIELDS)

    # If we are not requesting all delivery types we only keep the fields
    # that are specific to the delivery type being requested
    if len(delivery_types) == 1:
        base_query_fields = [f for f in base_query_fields if delivery_types[0] in f]

    # We add the slate fields after the filter as those are independent of
    # delivery type
    if query_slate_fields:
        base_query_fields.extend(SG_SLATE_FIELDS)

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
            sg_parent_entity = sg_entity.get(query_field)
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
