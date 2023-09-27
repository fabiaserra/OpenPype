import os
import click

from openpype.modules import (
    OpenPypeModule,
    ITrayModule,
    IPluginPaths,
)
from openpype.modules.shotgrid.scripts import populate_tasks, create_project


SHOTGRID_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


class ShotgridModule(OpenPypeModule, ITrayModule, IPluginPaths):
    leecher_manager_url = None
    name = "shotgrid"
    enabled = False
    project_id = None
    tray_wrapper = None

    def initialize(self, modules_settings):
        shotgrid_settings = modules_settings.get(self.name, dict())
        self.enabled = shotgrid_settings.get("enabled", False)
        self.leecher_manager_url = shotgrid_settings.get(
            "leecher_manager_url", ""
        )

    def cli(self, click_group):
        click_group.add_command(cli_main)

    def connect_with_modules(self, enabled_modules):
        pass

    def get_global_environments(self):
        return {"PROJECT_ID": self.project_id}

    def get_plugin_paths(self):
        return {
            "publish": [
                os.path.join(SHOTGRID_MODULE_DIR, "plugins", "publish")
            ]
        }

    def get_launch_hook_paths(self):
        return os.path.join(SHOTGRID_MODULE_DIR, "hooks")

    def tray_init(self):
        from .tray.shotgrid_tray import ShotgridTrayWrapper

        self.tray_wrapper = ShotgridTrayWrapper(self)

    def tray_start(self):
        return self.tray_wrapper.validate()

    def tray_exit(self, *args, **kwargs):
        return self.tray_wrapper

    def tray_menu(self, tray_menu):
        return self.tray_wrapper.tray_menu(tray_menu)


@click.command("populate_tasks")
@click.argument("project_code")
def populate_tasks_command(project_code):
    """Given a SG project code, populate the default tasks to all its entities."""
    return populate_tasks.populate_tasks(project_code)


@click.command("create_project")
@click.argument("project_code")
def create_project_command(project_code):
    """Given a SG project code, populate the default tasks to all its entities."""
    return create_project.create_project(project_code)


@click.group(ShotgridModule.name, help="Shotgrid CLI")
def cli_main():
    pass


cli_main.add_command(populate_tasks_command)
cli_main.add_command(create_project_command)


if __name__ == "__main__":
    cli_main()
