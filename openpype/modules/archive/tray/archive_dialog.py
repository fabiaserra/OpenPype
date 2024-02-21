import attr
import sys
import platform

from qtpy import QtCore, QtWidgets, QtGui
import qtawesome

from openpype import style
from openpype import resources
from openpype.lib import Logger
from openpype.client import get_projects
from openpype.pipeline import AvalonMongoDB
from openpype.tools.utils import lib as tools_lib
from openpype.modules.shotgrid.lib import credentials
from openpype.modules.archive.lib import archive
from openpype.tools.utils.constants import (
    HEADER_NAME_ROLE,
)

logger = Logger.get_logger(__name__)


class ArchiveDialog(QtWidgets.QDialog):
    """Interface to control the archive pipeline"""

    tool_title = "Archive Paths"
    tool_name = "archive_status"

    SIZE_W = 1800
    SIZE_H = 800

    DEFAULT_WIDTHS = (
        ("path", 1000),
        ("delete_time", 120),
        ("publish_path", 120),
        # ("family", 120),
        # ("subset", 120),
        # ("rep_name", 120),
        # ("version", 120)
    )

    def __init__(self, module, parent=None):
        super(ArchiveDialog, self).__init__(parent)

        self.setWindowTitle(self.tool_title)

        self._module = module

        icon = QtGui.QIcon(resources.get_openpype_icon_filepath())
        self.setWindowIcon(icon)

        self.setWindowFlags(
            QtCore.Qt.Window
            | QtCore.Qt.WindowTitleHint
            | QtCore.Qt.WindowCloseButtonHint
            | QtCore.Qt.WindowMaximizeButtonHint
            | QtCore.Qt.WindowMinimizeButtonHint
        )

        self.setMinimumSize(QtCore.QSize(self.SIZE_W, self.SIZE_H))

        self.sg = credentials.get_shotgrid_session()

        self._first_show = True
        self._initial_refresh = False
        self._ignore_project_change = False

        self._current_proj_name = None
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

        main_layout.addWidget(input_widget)

        # Table with all the products we find in the given folder
        table_view = QtWidgets.QTableView()
        headers = [item[0] for item in self.DEFAULT_WIDTHS]

        model = ArchivePathsTableModel(headers, parent=self)

        table_view.setModel(model)
        table_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        # TODO: Enable if we want to support publishing only selected
        # table_view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        # table_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        table_view.horizontalHeader().setSortIndicator(-1, QtCore.Qt.AscendingOrder)
        table_view.setAlternatingRowColors(True)
        table_view.verticalHeader().hide()
        table_view.viewport().setAttribute(QtCore.Qt.WA_Hover, True)

        table_view.setSortingEnabled(True)
        table_view.setTextElideMode(QtCore.Qt.ElideLeft)

        header = table_view.horizontalHeader()
        for column_name, width in self.DEFAULT_WIDTHS:
            idx = model.get_header_index(column_name)
            header.setSectionResizeMode(idx, QtWidgets.QHeaderView.Interactive)
            table_view.setColumnWidth(idx, width)

        header.setStretchLastSection(True)

        main_layout.addWidget(table_view)

        # Assign widgets we want to reuse to class instance
        self._projects_combobox = projects_combobox
        self._table_view = table_view
        self._model = model

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        # Ignore enter key
        if event.key() == QtCore.Qt.Key_Enter or event.key() == QtCore.Qt.Key_Return:
            event.ignore()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super(ArchiveDialog, self).showEvent(event)
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
        for project in get_projects(fields=["name", "data.active", "data.library_project"]):
            is_active = project.get("data", {}).get("active", False)
            is_library = project.get("data", {}).get("library_project", False)
            if is_active and not is_library:
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
            [["name", "is", project_name]],
            fields=["sg_code"]
        )

        project_name = self.dbcon.active_project() or "No project selected"
        title = "{} - {}".format(self.tool_title, project_name)
        self.setWindowTitle(title)

        # Store project name and code as class variable so we can reuse it throughout
        self._current_proj_name = project_name
        proj_code = sg_project.get("sg_code")
        self._current_proj_code = proj_code

        self._model.set_archive_paths(archive.get_archive_paths(proj_code))

    # -------------------------------
    # Delay calling blocking methods
    # -------------------------------

    def refresh(self):
        tools_lib.schedule(self._refresh, 50, channel="mongo")


class ArchivePathsTableModel(QtCore.QAbstractTableModel):

    COLUMN_LABELS = [
        ("path", "Filepath"),
        ("delete_time", "Delete Time"),
        ("publish_path", "Publish Path"),
    ]

    _tooltips = [
        "Path to archive",
        "Time when the path will be deleted",
        "Path where the file was published to",
    ]

    @attr.s
    class ProductRepresentation:
        path = attr.ib()
        delete_time = attr.ib()
        publish_path = attr.ib()

    def __init__(self, header, parent=None):
        super().__init__(parent=parent)
        self._header = header
        self._data = []

        self.edit_icon = qtawesome.icon("fa.edit", color="white")

    def rowCount(self, parent=None):
        return len(self._data)

    def columnCount(self, parent=None):
        return len(self._header)

    def get_column(self, index):
        """Return info about column

        Args:
            index (QModelIndex)

        Returns:
            (tuple): (COLUMN_NAME: COLUMN_LABEL)
        """
        return self.COLUMN_LABELS[index]

    def get_header_index(self, value):
        """Return index of 'value' in headers

        Args:
            value (str): header name value

        Returns:
            (int)
        """
        return self._header.index(value)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        """Return data depending on index, Qt::ItemDataRole and data type of the column.

        Args:
            index (QtCore.QModelIndex): Index to define column and row you want to return
            role (Qt::ItemDataRole): Define which data you want to return.

        Returns:
            None if index is invalid
            None if role is none of: DisplayRole, EditRole, CheckStateRole, DATAFRAME_ROLE
        """
        if not index.isValid():
            return

        if index.column() >= len(self.COLUMN_LABELS):
            return

        prod_item = self._data[index.row()]
        if role in (QtCore.Qt.DisplayRole):
            return attr.asdict(prod_item)[self._header[index.column()]]

        # TODO: change color if date is close to be deleted?
        # if role == QtCore.Qt.ForegroundRole:
            # product_dict = attr.asdict(prod_item)
            # publishable = all(
            #     value
            #     for key, value in product_dict.items()
            #     if key not in self.UNNECESSARY_COLUMNS
            # )
            # if not publishable:
            #     return QtGui.QColor(QtCore.Qt.yellow)
            # if any(value is None or value == "" for value in product_dict.values()):
                # return QtGui.QColor(QtCore.Qt.yellow)

        return None

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if section >= len(self.COLUMN_LABELS):
            return

        if role == QtCore.Qt.DisplayRole:
            if orientation == QtCore.Qt.Horizontal:
                return self.COLUMN_LABELS[section][1]

        elif role == HEADER_NAME_ROLE:
            if orientation == QtCore.Qt.Horizontal:
                return self.COLUMN_LABELS[section][0]  # return name

        elif role == QtCore.Qt.ToolTipRole:
            if orientation == QtCore.Qt.Horizontal:
                return self._tooltips[section]

    def sort(self, column, order):
        self.layoutAboutToBeChanged.emit()

        if column == 0:
            self._data.sort(key=lambda x: x.path)

        if column == 1:
            self._data.sort(key=lambda x: x.delete_time)

        # For the columns that could be empty, we need to make sure we
        # sort None type values
        if column == 2:
            self._data.sort(key=lambda x: (x.publish_path is not None, x.publish_path))

        if order == QtCore.Qt.DescendingOrder:
            self._data.reverse()

        self.layoutChanged.emit()

    def set_archive_paths(self, archive_paths):

        self.beginResetModel()

        self._data = []

        for filepath, archive_data in archive_paths.items():
            item = self.ProductRepresentation(
                filepath,
                archive_data["delete_time"],
                archive_data.get("publish_path", ""),
            )
            self._data.append(item)

        self.endResetModel()


def main():
    app_instance = QtWidgets.QApplication.instance()
    if app_instance is None:
        app_instance = QtWidgets.QApplication([])

    if platform.system().lower() == "windows":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("archive_status")

    window = ArchiveDialog()
    window.show()

    # Trigger on project change every time the tool loads
    window.on_project_change()

    sys.exit(app_instance.exec_())
