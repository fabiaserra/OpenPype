from openpype.lib import Logger
from openpype.modules.shotgrid.lib import credentials


logger = Logger.get_logger(__name__)


# Dictionary of tasks -> pipeline step that we want created on all
# entities of a project
# NOTE: Currently the task names and the pipeline step names are
# matching but that wouldn't necessarily be the case for all
DEFAULT_TASKS = {
    "Edit": "Edit",
    "Generic": "Generic",
}


def add_tasks_to_sg_entities(project, sg_entities, entity_type):
    sg = credentials.get_shotgrid_session()

    # Create list of dictionaries with the common data we will be using to
    # create all tasks
    # NOTE: we do this outside of the other for loop as we don't want to query
    # the pipeline step for each single entity
    tasks_data = []
    for task_name, step_name in DEFAULT_TASKS.items():
        step = sg.find_one(
            "Step",
            [["code", "is", step_name], ["entity_type", "is", entity_type]]
        )
        # Create a task for this shot
        task_data = {
            "project": project,
            "content": task_name,
            "step": step,
        }
        tasks_data.append(task_data)

    # Loop through each entity and create the task
    for sg_entity in sg_entities:
        for task_data in tasks_data:
            task_data["entity"] = sg_entity
            sg.create("Task", task_data)
            logger.info(
                "Task '%s' created at '%s'", task_data["content"], sg_entity["code"]
            )


def populate_tasks(project_code):
    sg = credentials.get_shotgrid_session()

    # Find the project with the given code
    project = sg.find_one("Project", [["sg_code", "is", project_code]])

    # Try add tasks to all episodes
    episodes = sg.find("Episode", [["project", "is", project]], ["id", "code"])
    add_tasks_to_sg_entities(project, episodes, "Episode")

    # Try add tasks to all episodes
    sequences = sg.find("Sequence", [["project", "is", project]], ["id", "code"])
    add_tasks_to_sg_entities(project, sequences, "Sequence")

    # Try add tasks to all shots
    shots = sg.find("Shot", [["project", "is", project]], ["id", "code"])
    add_tasks_to_sg_entities(project, shots, "Shot")
