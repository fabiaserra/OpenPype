"""
Provides:
    context -> projectName (str)
    context -> asset (str)
    context -> task (str)
"""

import pyblish.api
from openpype.pipeline import get_current_context


class CollectCurrentContext(pyblish.api.ContextPlugin):
    """Collect project context into publish context data.

    Plugin does not override any value if is already set.
    """

    order = pyblish.api.CollectorOrder - 0.48
    label = "Collect Current context"

    def process(self, context):
        # Check if values are already set
        project_name = context.data.get("projectName")
        asset_name = context.data.get("asset")
        task_name = context.data.get("task")

        current_context = get_current_context()
        if not project_name:
            project_name = current_context["project_name"]
            context.data["projectName"] = project_name

        if not asset_name:
            asset_name = current_context["asset_name"]
            context.data["asset"] = asset_name

        if not task_name:
            task_name = current_context["task_name"]
            context.data["task"] = task_name

        # QUESTION should we be explicit with keys? (the same on instances)
        #   - 'asset' -> 'assetName'
        #   - 'task' -> 'taskName'

        self.log.info((
            "Collected project context\n"
            "Project: {project_name}\n"
            "Asset: {asset_name}\n"
            "Task: {task_name}"
        ).format(
            project_name=context.data["projectName"],
            asset_name=context.data["asset"],
            task_name=context.data["task"]
        ))
