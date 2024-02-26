import sys
import click

from openpype.modules import (
    OpenPypeModule,
    ITrayAction
)
from openpype.lib import get_openpype_execute_args
from openpype.lib.execute import run_detached_process


class ArchiveModule(OpenPypeModule, ITrayAction):
    label = "Archive"
    name = "archive"

    def initialize(self, modules_settings):
        self.enabled = True

    def cli(self, click_group):
        click_group.add_command(cli_main)

    def tray_init(self):
        return

    def launch_archive_tool(self):
        args = get_openpype_execute_args(
            "module", self.name, "launch"
        )
        run_detached_process(args)

    def on_action_trigger(self):
        self.launch_archive_tool()


@click.command("clean_project")
@click.argument("proj_code")
@click.option("--archive/--no-archive", default=False)
def clean_project_command(
    proj_code,
    archive,
):
    """Perform a routine clean up of project by removing old files and folders
    that we consider irrelevant to keep through a production lifecycle.
    """
    sys.path.insert(0, "/sw/python/3.9.17/lib/python3.9/site-packages")
    from openpype.modules.archive.lib import expunge
    archive_proj = expunge.ArchiveProject(proj_code)
    return archive_proj.clean(archive=archive)


@click.command("purge_project")
@click.argument("proj_code")
def purge_project_command(
    proj_code,
):
    """Perform deep cleaning of the project by force deleting all the unnecessary
    files and folders and compressing the work directories.
    """
    sys.path.insert(0, "/sw/python/3.9.17/lib/python3.9/site-packages")
    from openpype.modules.archive.lib import expunge
    archive_proj = expunge.ArchiveProject(proj_code)
    return archive_proj.purge()


@click.group(ArchiveModule.name, help="Archive CLI")
def cli_main():
    pass


@cli_main.command()
def launch():
    """Launch TrayPublish tool UI."""
    sys.path.insert(0, "/sw/python/3.9.17/lib/python3.9/site-packages")
    from openpype.modules.archive.tray import archive_dialog
    archive_dialog.main()


cli_main.add_command(clean_project_command)
cli_main.add_command(purge_project_command)

if __name__ == "__main__":
    cli_main()