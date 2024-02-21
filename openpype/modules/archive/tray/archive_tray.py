# import os

from qtpy import QtWidgets

from openpype.lib import Logger
from openpype.modules.archive.tray.archive_dialog import ArchiveDialog


class ArchiveTrayWrapper:
    def __init__(self, module):
        self.module = module
        self.log = Logger.get_logger(self.__class__.__name__)

        self.archive_dialog = ArchiveDialog(module)

    def show_archive_dialog(self):
        self.archive_dialog.show()
        self.archive_dialog.activateWindow()
        self.archive_dialog.raise_()

    def tray_menu(self, parent_menu):
        tray_menu = QtWidgets.QMenu("Archive", parent_menu)

        show_archive_action = QtWidgets.QAction("Paths to archive", tray_menu)
        show_archive_action.triggered.connect(self.show_archive_dialog)
        tray_menu.addAction(show_archive_action)

        parent_menu.addMenu(tray_menu)
