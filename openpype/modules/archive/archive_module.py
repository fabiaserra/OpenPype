import click

from openpype.modules import (
    OpenPypeModule,
    ITrayModule
)
from openpype.modules.archive.lib import expunge


class ArchiveModule(OpenPypeModule):
    label = "Archive"
    name = "archive"

    def initialize(self, modules_settings):
        self.enabled = True

    def cli(self, click_group):
        click_group.add_command(cli_main)


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
    archive_proj = expunge.ArchiveProject(proj_code)
    return archive_proj.purge()


@click.group(ArchiveModule.name, help="Archive CLI")
def cli_main():
    pass


cli_main.add_command(clean_project_command)
cli_main.add_command(purge_project_command)

if __name__ == "__main__":
    cli_main()
