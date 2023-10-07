import os
import re
import click
import clique
from collections import defaultdict
from openpype.lib import Logger
from openpype.modules.shotgrid.lib import credentials
from openpype.modules.shotgrid.scripts import populate_tasks
from openpype.modules.deadline.lib import publish
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
    "render": {".exr", ".tif"},
    "pointcache": {".abc"},
    "review": {".mov", ".mp4", ".mxf", ".avi", ".wmv"},
    "workfile": {".nk", ".ma", ".mb", ".hip", ".sfx", ".mocha"}
}

# Regular expression that matches the generic file name format that we
# expect from the vendor
# Examples:
# uni_pg_0455_plt_01_roto_output-01_v001
# uni_pg_0455_plt_01_roto_output-02_v001
# uni_pg_0380_denoise_dn_plt_01_v004_paint_v001
GENERIC_FILENAME_RE = re.compile(
    r"^(?P<shot_code>[a-zA-Z0-9]+_[a-zA-Z0-9]+_\d+)_"
    r"(?P<subset>[a-zA-Z0-9_]+)_"
    r"(?P<task>[a-zA-Z0-9]+)_"
    r"(?P<variant>[a-zA-Z0-9_\-]*_)?"
    r"(?P<delivery_version>v\d+)"
    r"(?P<frame>\.\d+)?"
    r"(?P<extension>\.[a-zA-Z]+)$"
)

# Less greedy regular expression that matches the file name format that we
# expect from the vendor
TASKS_RE = "|".join(OUTSOURCE_TASKS)
STRICT_FILENAME_RE_STR = (
    r"^(?P<shot_code>{{shot_codes}})_"
    r"(?P<subset>[a-zA-Z0-9_]+)_"
    r"(?P<task>[{}])_"
    r"(?P<variant>[a-zA-Z0-9_\-]*_)?"
    r"(?P<delivery_version>v\d+)"
    r"(?P<frame>\.\d+)?"
    r"(?P<extension>\.[a-zA-Z]+)$"
).format(TASKS_RE)

# Regular expression to find show code given a media path that starts with the
# project root folder
SHOW_MATCH_RE = re.compile(r"/proj/(?P<show>\w+)")

# Fields we want to query from OP Assets
ASSET_FIELDS = ["name", "data.tasks"]


logger = Logger.get_logger(__name__)
sg = credentials.get_shotgrid_session()


def ingest_vendor_package(package_path):
    """Ingest incoming vendor package that contains different assets.

    Args:
        package_path (str): Path to vendor package

    Returns:
        str: Path to the package to ingest its files
    """
    match = SHOW_MATCH_RE.search(package_path)
    if not match:
        logger.error("No $SHOW found in path '%s'", package_path)
        return False

    project_code = match.group("show")
    sg_project = sg.find_one(
        "Project", [["sg_code", "is", project_code]], ["name"]
    )
    project_name = sg_project["name"]

    products, unaccurate, unassgined = find_products(
        package_path, project_name
    )

    # Name of the package
    # package_name = os.path.basename(package_path)
    # vendor_code = package_path.rsplit("_", 1)[-1]

    if products:
        click.echo(
            click.style(
                "Found the following products to publish:", fg="green"
            )
        )
        products_str = "\n".join(
            "\t- {source}: {asset_name} - {task_name} - {family_name} - {subset_name}".format(
                **product
            )
            for product in products
        )
        click.echo(click.style(products_str, fg="green"))

        if input("Publish? [y/n]: ") == "y":
            for product in products:
                click.echo(click.style(f"Publishing {product}", fg="green"))
                # publish.publish_version(
                #     project_name,
                #     product["asset_name"],
                #     product["task_name"],
                #     product["family_name"],
                #     product["subset_name"],
                #     product["expected_representations"],
                #     product["publish_data"],
                # )

    if unaccurate:
        click.echo(
            click.style(
                "Found some products to publish that we are not sure about some information",
                fg="orange",
            )
        )
        unaccurate_str = "\n".join(
            "\t- {source}: {asset_name} - {task_name} - {family_name} - {subset_name}".format(
                **unaccurate
            )
            for unaccurate in unaccurate
        )
        click.echo(click.style(unaccurate_str, fg="orange"))

        if input("Publish? [y/n]: ") == "y":
            for product in products:
                click.echo(click.style(f"Publishing {product}", fg="green"))
                # publish.publish_version(
                #     project_name,
                #     product["asset_name"],
                #     product["task_name"],
                #     product["family_name"],
                #     product["subset_name"],
                #     product["expected_representations"],
                #     product["publish_data"],
                # )


def find_products(package_path, project_name):
    products = []
    unaccurate_products = []
    unassigned_products = []

    asset_docs = get_assets(project_name, fields=["name", "data.tasks"])
    assets_re = "|".join([asset_doc["name"] for asset_doc in asset_docs])
    strict_regex = re.compile(
        STRICT_FILENAME_RE_STR.format(shot_codes=assets_re), re.IGNORECASE
    )

    for root, _, files in os.walk(package_path):
        collections, remainders = clique.assemble(files)
        filepaths = [
            collection.format("{root}{basename}{extension}", root=root)
            for collection in collections
        ]
        filepaths.extend(remainders)

        for filepath in filepaths:
            asset_doc, publish_data, confidence = get_product_from_filepath(
                filepath,
                project_name,
                asset_docs,
                strict_regex,
            )
            if not asset_doc:
                unassigned_products.append(filepath)
                continue

            if confidence == 3:
                products.append(publish_data)
            else:
                unaccurate_products.append(publish_data)

    return products, unaccurate_products, unassigned_products


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
#     asset_doc, publish_data, confidence_level = _get_product_from_filepath(
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


def get_product_from_filepath(project_name, filepath, strict_regex, asset_docs):
    # The level of confidence we have that we guessed the right tokens
    # from the filepath. The higher the number, the more confident we are
    confidence_level = 0
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
    if re_match:
        confidence_level = 3

    if not re_match:
        logger.info("Strict regular expression didn't match filename '%s'.", filename)
        re_match = GENERIC_FILENAME_RE.match(filename)
        confidence_level = 2

    if re_match:
        logger.info("Found matching regular expression for '%s'.", filename)
        shot_code = re_match.group("shot_code")
        subset_name = re_match.group("subset")
        task_name = re_match.group("task")
        variant_name = re_match.group("variant")
        delivery_version = re_match.group("delivery_version")
        extension = re_match.group("extension")

        logger.info("Shot code: '%s'", shot_code)
        logger.info("Subset name: '%s'", subset_name)
        logger.info("Task name: '%s'", task_name)
        logger.info("Variant name: '%s'", variant_name)
        logger.info("Delivery version: '%s'", delivery_version)
        logger.info("Extension: '%s'", extension)

        asset_doc = get_asset_by_name_case_not_sensitive(project_name, shot_code)
    else:
        logger.info("Looking for asset name in filename '%s'", filename)
        confidence_level = 1
        asset_doc = parse_containing(project_name, filename, asset_docs)

    if asset_doc:
        asset_name = asset_doc["name"]
    else:
        logger.warning("Couldn't find asset with name '%s'", shot_code)

    # TODO: this is not enough for catching camera assets
    for family, extensions in FAMILY_EXTS_MAP.items():
        if extension in extensions:
            family_name = family
            break

    if not family_name:
        logger.warning(
            "Couldn't find a family for the file extension '%s'", extension
        )

    rep_name = EXT_TO_REP_NAME.get(extension)
    if not rep_name:
        rep_name = extension

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

    return asset_doc, publish_data, confidence_level


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


def parse_containing(project_name, asset_name, asset_docs):
    """Look if file name contains any existing asset name"""
    for asset_doc in asset_docs:
        if asset_doc["name"].lower() in asset_name.lower():
            return get_asset_by_name(
                project_name, asset_doc["name"], fields=ASSET_FIELDS
            )
