from openpype.lib import Logger
from openpype.client import create_project
from openpype.pipeline import project_folders
from openpype.modules.shotgrid.lib import credentials
from openpype.settings import get_project_settings


logger = Logger.get_logger(__name__)


def create_project(project_name):
    """Create a new project, set up its folders and populate it with the SG info."""

    # Query project in SG to grab its code name and id
    sg = credentials.get_shotgrid_session()
    sg_project = sg.find_one(
        "Project",
        [
            ["name", "is", project_name],
        ],
        ["sg_code", "id"],
    )
    if not sg_project["sg_code"]:
        logger.error("No 'sg_code' found on project %s", project_name)
        return

    # Create OP project
    create_project(
        project_name,
        sg_project["sg_code"],
        library_project=False,
    )

    # Set SG project id on project settings
    project_settings = get_project_settings(project_name)
    project_settings["shotgrid"]["shotgrid"]["shotgrid_project_id"] = sg_project["id"]

    # Create project folders
    project_folders.create_project_folders(project_name)
