import os
import re
import platform
import json
import traceback
from qtpy import QtCore, QtWidgets, QtGui

from openpype import style
from openpype import resources
from openpype.lib import Logger
from openpype.client import get_projects
from openpype.pipeline import AvalonMongoDB
from openpype.tools.utils import lib as tools_lib
from openpype.modules.ingest.scripts import outsource


logger = Logger.get_logger(__name__)


class IngestDialog(QtWidgets.QDialog):
    """Interface to control SG deliveries"""

    tool_title = "Ingest Products"
    tool_name = "batch_ingester"

    SIZE_W = 1200
    SIZE_H = 800

    def __init__(self, module, parent=None):
        super(IngestDialog, self).__init__(parent)

        self.setWindowTitle(self.tool_title)

        self._module = module

        icon = QtGui.QIcon(resources.get_openpype_icon_filepath())
        self.setWindowIcon(icon)

        self.setWindowFlags(
            QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowCloseButtonHint
            | QtCore.Qt.WindowMinimizeButtonHint
        )

        self.setMinimumSize(QtCore.QSize(self.SIZE_W, self.SIZE_H))

        # self.sg = credentials.get_shotgrid_session()

        self._first_show = True
        self._initial_refresh = False
        self._ignore_project_change = False

        # Short code name for currently selected project
        self._current_proj_code = None

        dbcon = AvalonMongoDB()
        dbcon.install()
        dbcon.Session["AVALON_PROJECT"] = None
        self.dbcon = dbcon

        self.ui_init()

    def ui_init(self):

        main_layout = QtWidgets.QVBoxLayout(self)

        input_widget = QtWidgets.QWidget()

        # Common input widgets for delivery and republish features
        input_layout = QtWidgets.QFormLayout(input_widget)
        input_layout.setContentsMargins(5, 5, 5, 5)

        # Project combobox
        projects_combobox = QtWidgets.QComboBox()
        combobox_delegate = QtWidgets.QStyledItemDelegate(self)
        projects_combobox.setItemDelegate(combobox_delegate)
        projects_combobox.currentTextChanged.connect(self.on_project_change)
        input_layout.addRow("Project", projects_combobox)

        folder_dialog = QtWidgets.QFileDialog()
        folder_dialog.setFileMode(QtWidgets.QFileDialog.Directory)

        input_layout.addRow("Folder to ingest", folder_dialog)

        # Add button to generate delivery media
        validate_ingest_btn = QtWidgets.QPushButton(
            "Validate ingest folder"
        )
        validate_ingest_btn.setDefault(True)
        validate_ingest_btn.setToolTip(
            "Run the ingest tool to validate which products will be published"
        )
        validate_ingest_btn.clicked.connect(
            self._on_validate_ingest_clicked
        )

        main_layout.addWidget(validate_ingest_btn)

        #### REPORT ####
        text_area = QtWidgets.QTextEdit()
        text_area.setReadOnly(True)
        text_area.setVisible(False)

        main_layout.addWidget(text_area)

        # Assign widgets we want to reuse to class instance

        self._projects_combobox = projects_combobox
        self._folder_dialog = folder_dialog
        self._text_area = text_area

    def showEvent(self, event):
        super(IngestDialog, self).showEvent(event)
        if self._first_show:
            self._first_show = False
            self.setStyleSheet(style.load_stylesheet())
            tools_lib.center_window(self)

        if not self._initial_refresh:
            self._initial_refresh = True
            self.refresh()

    def _refresh(self):
        if not self._initial_refresh:
            self._initial_refresh = True
        self._set_projects()

    def _set_projects(self):
        # Store current project
        old_project_name = self.current_project

        self._ignore_project_change = True

        # Cleanup
        self._projects_combobox.clear()

        # Fill combobox with projects
        select_project_item = QtGui.QStandardItem("< Select project >")
        select_project_item.setData(None, QtCore.Qt.UserRole + 1)

        combobox_items = [select_project_item]

        project_names = self.get_filtered_projects()

        for project_name in sorted(project_names):
            item = QtGui.QStandardItem(project_name)
            item.setData(project_name, QtCore.Qt.UserRole + 1)
            combobox_items.append(item)

        root_item = self._projects_combobox.model().invisibleRootItem()
        root_item.appendRows(combobox_items)

        index = 0
        self._ignore_project_change = False

        if old_project_name:
            index = self._projects_combobox.findText(
                old_project_name, QtCore.Qt.MatchFixedString
            )

        self._projects_combobox.setCurrentIndex(index)

    @property
    def current_project(self):
        return self.dbcon.active_project() or None

    def get_filtered_projects(self):
        projects = list()
        for project in get_projects(fields=["name"]):
            projects.append(project["name"])

        return projects

    def on_project_change(self):
        if self._ignore_project_change:
            return

        row = self._projects_combobox.currentIndex()
        index = self._projects_combobox.model().index(row, 0)
        project_name = index.data(QtCore.Qt.UserRole + 1)

        self.dbcon.Session["AVALON_PROJECT"] = project_name

        sg_project = self.sg.find_one(
            "Project",
            [["name", "is", project_name]]
            ["sg_code"]
        )

        project_name = self.dbcon.active_project() or "No project selected"
        title = "{} - {}".format(self.tool_title, project_name)
        self.setWindowTitle(title)

        # Store project code as class variable so we can reuse it throughout
        proj_code = sg_project.get("sg_code")
        self._current_proj_code = proj_code

    def _format_report(self, report_items, success):
        """Format final result and error details as html."""
        msg = "Delivery finished"
        if success:
            msg += " successfully"
        else:
            msg += " with errors"
        txt = "<h2>{}</h2>".format(msg)
        for header, data in report_items.items():
            txt += "<h3>{}</h3>".format(header)
            for item in data:
                txt += "{}<br>".format(item)

        return txt

    def _on_validate_ingest_clicked(self):

        try:
            folder_path = self._folder_dialog.selectedFiles()[0]
            report_items, success = outsource.ingest_vendor_package(
                folder_path,
            )

        except Exception:
            logger.error(traceback.format_exc())
            report_items = {
                "Error": [traceback.format_exc()]
            }
            success = False

        self._text_area.setText(self._format_report(report_items, success))
        self._text_area.setVisible(True)

    # -------------------------------
    # Delay calling blocking methods
    # -------------------------------

    def refresh(self):
        tools_lib.schedule(self._refresh, 50, channel="mongo")


def main():
    app_instance = QtWidgets.QApplication.instance()
    if app_instance is None:
        app_instance = QtWidgets.QApplication([])

    if platform.system().lower() == "windows":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("batch_ingester")

    window = IngestDialog()
    window.show()

    # Trigger on project change every time the tool loads
    window.on_project_change()

    app_instance.exec_()
