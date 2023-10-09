import click

from openpype.modules import (
    OpenPypeModule
)
from openpype.modules.ingest.scripts import outsource


class IngestModule(OpenPypeModule):
    label = "Ingest"
    name = "ingest"

    def initialize(self, modules_settings):
        self.enabled = True

    def cli(self, click_group):
        click_group.add_command(cli_main)


@click.command("ingest_vendor_package")
@click.argument("folder_path", type=click.Path(exists=True))
def ingest_vendor_package(
    folder_path,
):
    """Given an outsource vendor package folder, try ingest all its contents.

    Args:
        path (str): Path to the outsource package received.

    """
    return outsource.ingest_vendor_package(
        folder_path
    )


@click.group(IngestModule.name, help="Ingest CLI")
def cli_main():
    pass


cli_main.add_command(ingest_vendor_package)


if __name__ == "__main__":
    cli_main()


# Examples:
# /proj/uni/io/incoming/20230926/From_rotomaker/A/uni_pg_0430_plt_01_roto_output_v001
# /proj/uni/io/incoming/20230926/From_rotomaker/A/uni_pg_0440_plt_01_roto_output_v001
# /proj/uni/io/incoming/20230926/From_rotomaker/C/uni_pg_0380_denoise_dn_plt_01_v004_paint_v001
# /proj/uni/io/incoming/20230926/From_rotomaker/C/workfile/uni_pg_0380_denoise_dn_plt_01_v004_paint_v001_workfile
# /proj/uni/io/incoming/20230928/B/uni_ci_4088_plt_01_v001_MM_v001
# /proj/uni/io/incoming/20230928/B/uni_ci_4098_plt_01_v001_MM_v001
