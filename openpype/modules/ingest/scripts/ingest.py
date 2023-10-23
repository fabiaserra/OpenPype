import os
import re
import clique
from collections import defaultdict

from openpype.lib import Logger
from openpype.pipeline import legacy_io

from openpype.modules.deadline.lib import publish
from openpype.client import get_assets, get_asset_by_name


# Types of tasks that we support for outsource
ROTO_TASK = "roto"
PAINT_TASK = "paint"
TRACK_TASK = "track"
COMP_TASK = "comp"
EDIT_TASK = "edit"
OUTSOURCE_TASKS = [ROTO_TASK, PAINT_TASK, COMP_TASK, TRACK_TASK, EDIT_TASK]

# Dictionary that maps the extension name to the representation name
# we want to use for it
EXT_TO_REP_NAME = {
    ".nk": "nuke",
    ".ma": "maya",
    ".mb": "maya",
    ".hip": "houdini",
    ".sfx": "silhouette",
    ".mocha": "mocha",
    ".psd": "photoshop"
}

# Dictionary that maps the file extensions to the family name we want to
# ingest it as
# This is only used as a fallback in case we can't extract the family
# information from other contexts like the file name (i.e, cameras would
# for example have .abc as extension too)
FAMILY_EXTS_MAP = {
    "render": {".exr", ".tif", ".jpg", ".jpeg"},
    "pointcache": {".abc"},
    "camera": {".abc", ".fbx"},
    "reference": {".mov", ".mp4", ".mxf", ".avi", ".wmv"},
    "workfile": {".nk", ".ma", ".mb", ".hip", ".sfx", ".mocha", ".psd"},
    "distortion": {".nk", ".exr"},
    "color_grade": {".ccc", ".cc"},
}

# Compatible file extensions for camera assets
CAMERA_EXTS = {".abc", ".fbx"}

# Dictionary that maps names that we find in a filename to different
# data that we want to override in the publish data
FUZZY_NAME_OVERRIDES = {
    ("_cam", "camera"): {
        "family_name": "camera",
    },
    ("_mm", "_trk", "matchmove", "tracking"): {
        "task_name": TRACK_TASK
    },
    ("distortion", "distortion_node"): {
        "family_name": "distortion"
    }
}

# List of fields that are required in the products in order to publish them
MUST_HAVE_FIELDS = {
    "asset_name",
    "task_name",
    "family_name",
    "subset_name",
    "rep_name",
}

# Regular expression that matches file names following precisely our naming convention
# format that we expect from the vendor
# Examples:
# uni_pg_0455_plt_01_roto_output-01_v001
# uni_pg_0455_plt_01_roto_output-02_v001
# uni_pg_0380_denoise_dn_plt_01_v004_paint_v001
TASKS_RE = "|".join(OUTSOURCE_TASKS)
STRICT_FILENAME_RE_STR = (
    r"^(?P<shot_code>({{shot_codes}}))_"
    r"(?P<subset>[a-zA-Z0-9_]+)_"
    r"(?P<task>({}))_"
    r"(?P<variant>[a-zA-Z0-9_\-]*_)?"
    r"(?P<delivery_version>v\d+)"
    r"(?P<frame>\.(\*|%0?\d*d)+)?"
    r"(?P<extension>\.[a-zA-Z]+)$"
).format(TASKS_RE)

# Less greedy regular expression that matches the generic file name format that we
# expect from the vendor
GENERIC_FILENAME_RE = re.compile(
    r"^(?P<shot_code>[a-zA-Z0-9]+_[a-zA-Z0-9]+_\d+)_"
    r"(?P<subset>[a-zA-Z0-9_]+)_"
    r"(?P<task>[a-zA-Z0-9]+)_"
    r"(?P<variant>[a-zA-Z0-9_\-]*_)?"
    r"(?P<delivery_version>v\d+)"
    r"(?P<frame>\.(\*|%0?\d*d)+)?"
    r"(?P<extension>\.[a-zA-Z]+)$",
    re.IGNORECASE
)

# Regular expression that we use as a final resource if we haven't found any
# of the publish data yet
# Example: MP01_V0001_CC.%04d.exr
FALLBACK_FILENAME_RE = re.compile(
    r"(?P<subset>\w+)_"
    r"(?P<delivery_version>v\d+)"
    r"_?(?P<suffix>[a-zA-Z0-9_\-]*)"
    r"(?P<frame>\.(\*|%0?\d*d)+)?"
    r"(?P<extension>\.[a-zA-Z]+)$",
    re.IGNORECASE
)

# Fallback task name if we can't find any
TASK_NAME_FALLBACK = "edit"

# Regular expression to find show code given a media path that starts with the
# project root folder
SHOW_MATCH_RE = re.compile(r"/proj/(?P<show>\w+)")

# Fields we want to query from OP Assets
ASSET_FIELDS = ["name", "data.tasks"]

# Regular expression to match package name to extract vendor code
VENDOR_PACKAGE_RE = r"From_(\w+)"


logger = Logger.get_logger(__name__)


def publish_products(
    project_name, products_data, overwrite_version=False, force_task_creation=False
):
    """Given a list of ProductRepresentation objects, publish them to OP and SG

    Args:
        project_name (str): Name of the project to publish to
        products_data (list): List of ProductRepresentation objects

    Returns:
        tuple: Tuple containing:
            report_items (dict): Dictionary with the messages to show in the
                report.
            success (bool): Whether the publish was successful or not

    """
    report_items = defaultdict(list)
    success = True

    if not project_name:
        return report_items["Project not selected"].append(
            "Select project before publishing!"
        ), False

    # Hack required for environment to pick up in the farm
    legacy_io.Session["AVALON_PROJECT"] = project_name
    legacy_io.Session["AVALON_APP"] = "traypublisher"

    # Go through list of products data from ingest dialog table and combine the
    # representations dictionary for the products that target the same subset
    products = {}
    for product_item in products_data:
        item_str = f"{product_item.asset} - {product_item.task} - {product_item.family} - {product_item.subset}"
        logger.debug(item_str)

        key = (
            product_item.asset,
            product_item.task,
            product_item.family,
            product_item.subset
        )
        if not all(key):
            logger.debug(
                "Skipping product as it doesn't have all required fields to publish"
            )
            continue
        elif key not in products:
            products[key] = {
                "expected_representations": {
                    product_item.rep_name: product_item.path,
                },
                "version": product_item.version,
            }
        else:
            if product_item.rep_name in products[key]["expected_representations"]:
                logger.debug("Duplicated representation")
                report_items["Duplicated representation in product"].append(
                    item_str
                )
                continue

            products[key]["expected_representations"][product_item.rep_name] = product_item.path

    logger.debug("Flattened products: %s", products)

    for product_fields, product_data in products.items():
        asset, task, family, subset = product_fields
        msg, success = publish.publish_version(
            project_name,
            asset,
            task,
            family,
            subset,
            product_data["expected_representations"],
            {"version": product_data.get("version")},
            overwrite_version,
            force_task_creation,
        )
        if success:
            report_items["Successfully submitted products to publish"].append(msg)
        else:
            report_items["Failed submission for products"].append(msg)

    return report_items, success


def get_products_from_filepath(package_path, project_name, project_code):
    """Given a path to a folder, find all the products that we can ingest from it"""
    def _split_camel_case(name):
        """Split camel case name into words separated by underscores"""
        result = ""
        for i, c in enumerate(name):
            # If letter is capital and it's not after a "_"
            # we add a lowercase
            if i > 0 and c.isupper() and name[i-1] != "_":
                result += "_"
            result += c.lower()
        return result

    asset_names = [
        asset_doc["name"] for asset_doc in get_assets(project_name, fields=["name"])
    ]
    assets_re = "|".join(asset_names)
    strict_regex_str = STRICT_FILENAME_RE_STR.format(shot_codes=assets_re)
    strict_regex = re.compile(
        strict_regex_str, re.IGNORECASE
    )
    logger.debug("Strict regular expression: %s", strict_regex_str)

    # Remove asset names that don't contain underscores as those are very short and easy
    # to get false positives
    asset_names = [asset_name for asset_name in asset_names if "_" in asset_name]

    # Recursively find all paths on folder and check if it's a product we can ingest
    products = {}
    for root, _, files in os.walk(package_path):
        # Create a list of all the collections of files and single files that
        # we find that could potentially be an ingestable product
        collections, remainders = clique.assemble(files)
        filepaths_frame_range = [
            (
                os.path.join(root, collection.format("{head}{padding}{tail}")),
                min(collection.indexes),
                max(collection.indexes)
            )
            for collection in collections
        ]
        filepaths_frame_range.extend(
            (
                os.path.join(root, remainder),
                None,
                None
            )
            for remainder in remainders
        )

        for filepath_frame_range in filepaths_frame_range:

            filepath, frame_start, frame_end = filepath_frame_range

            publish_data = get_product_from_filepath(
                project_name,
                project_code,
                filepath,
                strict_regex,
                asset_names,
            )

            # Add frame range to publish data
            if frame_start:
                publish_data["frame_start"] = frame_start

            if frame_end:
                publish_data["frame_end"] = frame_end

            # Validate that we have all the required fields to publish
            if not all([publish_data.get(field_name) for field_name in MUST_HAVE_FIELDS]):
                logger.warning("Missing fields in publish data: %s", publish_data)

            subset_name = publish_data["subset_name"]
            if subset_name:
                # Make sure subset name is always lower case and split by underscores
                subset_name = _split_camel_case(subset_name)
                publish_data["subset_name"] = subset_name

            products[filepath] = publish_data

    return products


def get_product_from_filepath(
    project_name, project_code, filepath, strict_regex, asset_names
):
    """Given a filepath, try to extract the publish data from it"""
    filename = os.path.basename(filepath)
    extension = os.path.splitext(filename)[-1]

    asset_doc = None
    asset_name = None
    task_name = None
    family_name = None
    subset_name = None
    variant_name = None
    delivery_version = None

    re_match = strict_regex.match(filename)
    if not re_match:
        logger.debug("Strict regular expression didn't match filename '%s'.", filename)
        re_match = GENERIC_FILENAME_RE.match(filename)

    if re_match:
        logger.debug("Found matching regular expression for '%s'.", filename)

        shot_code = re_match.group("shot_code")
        subset_name = re_match.group("subset")
        task_name = re_match.group("task")
        variant_name = re_match.group("variant")
        delivery_version = re_match.group("delivery_version")
        extension = re_match.group("extension") or extension

        logger.debug("Shot code: '%s'", shot_code)
        logger.debug("Subset name: '%s'", subset_name)
        logger.debug("Task name: '%s'", task_name)
        logger.debug("Variant name: '%s'", variant_name)
        logger.debug("Delivery version: '%s'", delivery_version)
        logger.debug("Extension: '%s'", extension)

        logger.debug("Looking for asset name '%s'", shot_code)

        asset_doc = get_asset_by_name_case_not_sensitive(project_name, shot_code)

    # If asset doc hasn't been found yet, try find it by just looking if the
    # string exists anywhere in the filepath
    if not asset_doc:
        logger.debug(
            "Asset name not found yet, doing string comparison in filepath '%s'",
            filepath
        )
        asset_doc, found_name = parse_containing(
            project_name, project_code, filepath, asset_names
        )
        if asset_doc:
            logger.debug(
                "Found asset name '%s' in filepath '%s'.", asset_doc["name"], filepath
            )
            if not subset_name:
                simple_filename = filename.lower().replace(
                    found_name, ""
                )
                # Remove the first character after removing the asset name
                # which is likely a "_" or "-"
                simple_filename = simple_filename[1:]
                logger.debug(
                    "Subset name not found yet, trying last resort with '%s' after removing prefix '%s'",
                    simple_filename, "{}_".format(found_name)
                )
                fallback_re = FALLBACK_FILENAME_RE.match(simple_filename)
                if fallback_re:
                    subset_name = fallback_re.group("subset")
                    delivery_version = fallback_re.group("delivery_version")
                    extension = fallback_re.group("extension")

            if not task_name:
                task_name = TASK_NAME_FALLBACK

    if asset_doc:
        asset_name = asset_doc["name"]
    else:
        logger.warning("Couldn't find asset in file '%s'", filepath)

    # TODO: this is not enough for catching camera assets
    for family, extensions in FAMILY_EXTS_MAP.items():
        if extension in extensions:
            family_name = family
            break

    if extension in CAMERA_EXTS:
        if "camera" in filepath.lower():
            logger.debug(
                "Found 'camera' string in filepath %s, assuming it's a 'camera' product",
                filepath,
            )
            family_name = "camera"

    if not family_name:
        logger.warning(
            "Couldn't find a family for the file extension '%s'", extension
        )

    # Create representation name from extension
    rep_name = EXT_TO_REP_NAME.get(extension)
    if not rep_name:
        rep_name = extension.rsplit(".", 1)[-1]

    # Override task name if we find any of the names of the supported tasks in the
    # filepath
    if task_name not in OUTSOURCE_TASKS:
        logger.debug(
            "Overriding subset name '%s' with task name '%s'",
            subset_name, task_name
        )
        subset_name = task_name
        for possible_task_name in OUTSOURCE_TASKS:
            if possible_task_name in filepath.split("/") or f"_{possible_task_name}_" in filepath:
                logger.debug(
                    "Found '%s' in filepath '%s', assuming it's a '%s' task",
                    possible_task_name,
                    filepath,
                    possible_task_name,
                )
                task_name = possible_task_name
                break

    # Make sure delivery version is an integer
    if delivery_version:
        delivery_version = int(delivery_version.strip("v"))

    publish_data = {
        "project_name": project_name,
        "asset_name": asset_name,
        "task_name": task_name,
        "family_name": family_name,
        "subset_name": subset_name,
        "variant_name": variant_name,
        "version": delivery_version,
        "rep_name": rep_name
    }

    # Go through the fuzzy name overrides and apply them if we find
    # a match
    for fuzzy_names, overrides in FUZZY_NAME_OVERRIDES.items():
        for fuzzy_name in fuzzy_names:
            if fuzzy_name in filepath.lower():
                logger.debug(
                    "Found fuzzy name '%s' in filename '%s', applying overrides %s",
                    fuzzy_name,
                    filename,
                    overrides,
                )
                publish_data.update(overrides)

    # If task name is still not one of the supported ones, mark it as None so we
    # can clearly see what happened and not error out during the publish
    if publish_data["task_name"] not in OUTSOURCE_TASKS:
        logger.error(
            "Task name found '%s' in filepath '%s' is not one of the supported ones "
            "by this tool: %s.\nIf you think the task should be parsed by the tool "
            "please report it to @pipe",
            task_name,
            filepath,
            OUTSOURCE_TASKS
        )
        publish_data["task_name"] = None

    # Add variant name to subset name if we have one
    if publish_data["variant_name"] and publish_data["subset_name"]:
        # Remove the last underscore from captured variant name
        variant_name = publish_data["variant_name"].rsplit("_", 1)[0]
        publish_data["subset_name"] = f"{publish_data['subset_name']}_{variant_name}"

    # Append task name to subset name by default
    if publish_data["task_name"] and publish_data["subset_name"]:
        publish_data["subset_name"] = f"{publish_data['task_name']}_{publish_data['subset_name']}"

    logger.debug("Publish data for filepath %s: %s", filepath, publish_data)

    return publish_data


def get_asset_by_name_case_not_sensitive(project_name, asset_name):
    """Get asset by name ignoring case"""
    asset_name = re.compile(asset_name, re.IGNORECASE)

    assets = list(
        get_assets(project_name, asset_names=[asset_name], fields=ASSET_FIELDS)
    )
    if assets:
        if len(assets) > 1:
            logger.warning(
                "Too many records found for {}, using first.".format(asset_name)
            )

        return assets.pop()


def parse_containing(project_name, project_code, filepath, asset_names):
    """Parse filepath to find asset name"""
    for asset_name in asset_names:
        if asset_name.lower() in filepath.lower():
            return get_asset_by_name(
                project_name, asset_name, fields=ASSET_FIELDS
            ), asset_name.lower()

    # If we haven't find it yet check asset name without project code
    for asset_name in asset_names:
        if asset_name.lower().startswith(project_code.lower()):
            # Remove the project code + "_" from the asset name
            short_asset_name = asset_name[len(project_code)+1:]
            if short_asset_name in filepath.lower():
                return get_asset_by_name(
                    project_name, asset_name, fields=ASSET_FIELDS
                ), short_asset_name
        else:
            logger.warning(
                "Assets aren't starting with project code %s", project_code
            )
            return None, None

    return None, None
