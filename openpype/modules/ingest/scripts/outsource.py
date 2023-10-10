import os
import re
import click
import clique
from collections import defaultdict

from openpype.lib import Logger
from openpype.modules.shotgrid.lib import credentials

# from openpype.modules.shotgrid.scripts import populate_tasks
# from openpype.modules.deadline.lib import publish
from openpype.client import get_assets, get_asset_by_name


# Types of tasks that we support for outsource
ROTO_TASK = "roto"
PAINT_TASK = "paint"
TRACK_TASK = "track"
COMP_TASK = "comp"
OUTSOURCE_TASKS = {ROTO_TASK, PAINT_TASK, COMP_TASK, TRACK_TASK}

# Dictionary that maps the extension name to the representation name
# we want to use for it
EXT_TO_REP_NAME = {
    ".nk": "nuke",
    ".ma": "maya",
    ".mb": "maya",
    ".hip": "houdini",
    ".sfx": "silhouette",
    ".mocha": "mocha",
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
    "workfile": {".nk", ".ma", ".mb", ".hip", ".sfx", ".mocha"},
    "color_grade": {".ccc", ".cc"},
}

# Compatible file extensions for camera assets
CAMERA_EXTS = {".abc", ".fbx"}

# Dictionary that maps names that we find in a filename to different
# data that we want to override in the publish data
FUZZY_NAME_OVERRIDES = {
    ("camera", "cam"): {
        "family_name": "camera",
    },
}

# List of fields that are required in the products in order to publish them
MUST_HAVE_FIELDS = {
    "asset_name",
    "task_name",
    "family_name",
    "subset_name",
    "expected_representations",
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
    r"(?P<frame>\.\d+)?"
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


def ingest_vendor_package(folder_path):
    """Ingest incoming vendor package that contains different assets.

    Args:
        folder_path (str): Path to vendor package

    Returns:
        str: Path to the package to ingest its files
    """
    match = SHOW_MATCH_RE.search(folder_path)
    if not match:
        logger.error("No $SHOW found in path '%s'", folder_path)
        return False

    project_code = match.group("show")

    sg = credentials.get_shotgrid_session()
    sg_project = sg.find_one("Project", [["sg_code", "is", project_code]], ["name"])
    project_name = sg_project["name"]

    products, unassigned = find_products(folder_path, project_name, project_code)

    # Name of the package
    # package_name = os.path.basename(folder_path)
    # vendor_code = folder_path.rsplit("_", 1)[-1]
    # vendor_match = VENDOR_PACKAGE_RE.search(folder_path)

    # If products, print all the products that we will publish
    if products:
        click.echo(click.style("Found the following products to publish:", fg="green"))

        for asset_name, tasks in products.items():
            click.echo(
                click.style("- Shot: ", fg="green") +
                click.style(f"{asset_name}", fg="green", bold=True)
            )
            for task_name, families in tasks.items():
                click.echo(
                    click.style("  - Task: ", fg="green") +
                    click.style(f"{task_name}", fg="green", bold=True)
                )
                for family_name, subsets in families.items():
                    click.echo(
                        click.style("    - Family: ", fg="green") +
                        click.style(f"{family_name}", fg="green", bold=True)

                    )
                    for subset_name, publish_data in subsets.items():
                        click.echo(
                            click.style("\t- Subset: ", fg="green") +
                            click.style(f"{subset_name}", fg="green", bold=True)
                        )
                        for rep_name, path in publish_data["expected_representations"].items():
                            click.echo(
                                click.style(
                                    f"\t    * {rep_name}",
                                    fg="white", bold=True
                                ) +
                                click.style(
                                    f" - {path}",
                                    fg="white",
                                )
                            )

    # Print unassigned products too
    if unassigned:
        click.echo(
            click.style(
                "\n\nWe were unable to find enough information to publish the "
                "following files:",
                fg="bright_red",
            )
        )
        click.echo(
            click.style("\n".join(f"\t- {file}" for file in unassigned), fg="bright_red")
        )
        click.echo(
            click.style(
                "\n\nIf there's some of these that you'd expect the tool "
                "to automatically ingest, please send the path to @pipe "
                "so we can add more logic to identify them.",
                fg="bright_red", bold=True
            )
        )

    if products:
        if input("Publish? [Y/n]: ") != "n":
            for asset_name, tasks in products.items():
                for task_name, families in tasks.items():
                    for family_name, subsets in families.items():
                        for subset_name, publish_data in subsets.items():
                            click.echo(
                                click.style(
                                    f" - Publishing {asset_name} - {task_name} - {family_name} - {subset_name}",
                                    fg="white", bold=True
                                )
                            )
                            # publish.publish_version(
                            #     project_name,
                            #     asset_name,
                            #     task_name,
                            #     family_name,
                            #     subset_name,
                            #     publish_data["expected_representations"],
                            #     publish_data,
                            # )


def find_products(package_path, project_name, project_code):

    # Created nested dictionaries for storing all the products we find
    # categorized by asset -> task -> family -> subset
    products = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(dict)
            )
        )
    )
    unassigned = []

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
    for root, _, files in os.walk(package_path):
        # Create a list of all the collections of files and single files that
        # we find that could potentially be an ingestable product
        collections, remainders = clique.assemble(files)
        filepaths = [
            os.path.join(root, collection.format("{head}{padding}{tail}"))
            for collection in collections
        ]
        filepaths.extend(os.path.join(root, remainder) for remainder in remainders)

        for filepath in filepaths:
            publish_data = get_product_from_filepath(
                project_name,
                project_code,
                filepath,
                strict_regex,
                asset_names,
            )

            # Validate that we have all the required fields to publish
            if not all([publish_data[field_name] for field_name in MUST_HAVE_FIELDS]):
                unassigned.append(filepath)
                continue

            asset_name = publish_data["asset_name"]
            task_name = publish_data["task_name"]
            family_name = publish_data["family_name"]
            subset_name = publish_data["subset_name"]

            # Check if we already had added a product in the same destination
            # so we just append it as another representation if that's the case
            existing_data = (
                products.get(asset_name, {})
                .get(task_name, {})
                .get(family_name, {})
                .get(subset_name, {})
            )

            # Update expected representations if subset is the same
            if existing_data:
                logger.debug("Found existing data: %s", existing_data)
                existing_rep_names = set(
                    existing_data["expected_representations"].keys()
                )
                new_rep_name = next(iter(publish_data["expected_representations"].keys()))
                if new_rep_name in existing_rep_names:
                    orig_rep_name = new_rep_name
                    index = 1
                    while new_rep_name in existing_rep_names:
                        new_rep_name = f"{orig_rep_name}_{index}"
                        index += 1
                    publish_data["expected_representations"][
                        new_rep_name
                    ] = publish_data["expected_representations"][orig_rep_name]
                    publish_data["expected_representations"].pop(orig_rep_name)
                else:
                    existing_data["expected_representations"].update(
                        publish_data["expected_representations"]
                    )
            else:
                logger.debug("Adding product: %s", publish_data)
                products[asset_name][task_name][family_name][subset_name] = publish_data

    return products, unassigned


# def get_product_from_filepath(
#     filepath,
#     project_name,
#     asset_docs,
#     strict_regex,
# ):
#     """Try to parse out asset name from file name provided.

#     Artists might provide various file name formats.
#     Currently handled:
#         - chair.mov
#         - chair_v001.mov
#         - my_chair_to_upload.mov
#     """
#     publish_data = _get_product_from_filepath(
#         project_name, filepath, strict_regex, asset_docs
#     )
#     # if asset_doc:
#     #     task_name = publish_data["task_name"]
#     #     asset_tasks = asset_doc.get("data", {}).get("tasks", {})
#     #     # If task name found is one of the outsource tasks and it's not
#     #     # in the SG shot tasks, we add it
#     #     if task_name in OUTSOURCE_TASKS and task_name not in asset_tasks:
#     #         logger.warning(
#     #             "Task '%s' not found on asset '%s', adding it.",
#     #             task_name, asset_doc["name"]
#     #         )
#     #         sg_shot = sg.find_one(
#     #             "Shot", [["code", "is", asset_doc["name"]]]
#     #         )
#     #         populate_tasks.add_tasks_to_sg_entities(
#     #             sg_project,
#     #             [sg_shot],
#     #             "Shot",
#     #             tasks={task_name: task_name}
#     #         )

#     return asset_doc, publish_data, confidence_level


def get_product_from_filepath(
    project_name, project_code, filepath, strict_regex, asset_names
):
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
        asset_doc, found_name = parse_containing(project_name, project_code, filepath, asset_names)
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
                logger.warning(
                    "Subset name not found yet, trying last resort with %s, %s",
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
        logger.warning("Couldn't find a family for the file extension '%s'", extension)

    # Create representation name from extension
    rep_name = EXT_TO_REP_NAME.get(extension)
    if not rep_name:
        rep_name = extension.rsplit(".", 1)[-1]

    # Create dictionary of representations to create for
    # the found product
    expected_representations = {rep_name: filepath}

    publish_data = {
        "project_name": project_name,
        "asset_name": asset_name,
        "task_name": task_name,
        "family_name": family_name,
        "subset_name": subset_name,
        "variant_name": variant_name,
        "delivery_version": delivery_version,
        "expected_representations": expected_representations,
        "source": filepath,
    }

    # Override task name if we find any of the names of the supported tasks in the
    # filepath
    for possible_task_name in OUTSOURCE_TASKS:
        if possible_task_name in filepath.lower():
            logger.debug(
                "Found '%s' in filepath '%s', assuming it's a '%s' task",
                possible_task_name,
                filepath,
                possible_task_name,
            )
            publish_data["task_name"] = possible_task_name
            break

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

    # Add variant name to subset name if we have one
    if variant_name:
        # Remove the last underscore from captured variant name
        variant_name = variant_name.rsplit("_", 1)[0]
        publish_data["subset_name"] = f"{subset_name}_{variant_name}"

    logger.debug("Publish data for filepath %s: %s", filepath, publish_data)

    return publish_data


def get_asset_by_name_case_not_sensitive(project_name, asset_name):
    """Handle more cases in file names"""
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
    """Look if file name contains any existing asset name"""
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
