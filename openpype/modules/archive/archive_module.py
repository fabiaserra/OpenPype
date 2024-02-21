import click

from openpype.modules import (
    OpenPypeModule,
    ITrayModule
)
from openpype.modules.archive.lib import archive


class ArchiveModule(OpenPypeModule, ITrayModule):
    label = "Archive"
    name = "archive"
    tray_wrapper = None

    def initialize(self, modules_settings):
        self.enabled = True

    def cli(self, click_group):
        click_group.add_command(cli_main)

    def tray_init(self):
        from .tray.archive_tray import ArchiveTrayWrapper

        self.tray_wrapper = ArchiveTrayWrapper(self)

    def tray_start(self):
        return

    def tray_exit(self, *args, **kwargs):
        return self.tray_wrapper

    def tray_menu(self, tray_menu):
        return self.tray_wrapper.tray_menu(tray_menu)


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
    archive_proj = archive.ArchiveProject(proj_code)
    return archive_proj.clean(archive=archive)


@click.command("purge_project")
@click.argument("proj_code")
def purge_project_command(
    proj_code,
):
    """Perform deep cleaning of the project by force deleting all the unnecessary
    files and folders and compressing the work directories.
    """
    archive_proj = archive.ArchiveProject(proj_code)
    return archive_proj.purge()


@click.group(ArchiveModule.name, help="Archive CLI")
def cli_main():
    pass


cli_main.add_command(clean_project_command)
cli_main.add_command(purge_project_command)

if __name__ == "__main__":
    cli_main()
