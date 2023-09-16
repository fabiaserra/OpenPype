import platform
from qtpy import QtCore, QtWidgets, QtGui

from openpype import style
from openpype import resources
from openpype.client import get_projects
from openpype.pipeline import AvalonMongoDB
from openpype.tools.utils import lib as tools_lib
from openpype.modules.shotgrid.lib import delivery, credentials
from openpype.modules.delivery.scripts import media


class DeliveryOutputsWidget(QtWidgets.QWidget):
    def __init__(self, delivery_output_names=None):
        super().__init__()

        # Create the layout
        self.layout = QtWidgets.QFormLayout(self)
        self.setLayout(self.layout)

        self.delivery_outputs = {}
        self.update(delivery_output_names)

    def update(self, delivery_output_names):
        # Remove all existing rows
        for i in reversed(range(self.layout.count())):
            item = self.layout.itemAt(i)
            if item.widget() is not None:
                item.widget().deleteLater()
            self.layout.removeItem(item)

        self.delivery_outputs = {}
        if not delivery_output_names:
            return

        # Add the new rows
        for output_name in delivery_output_names:
            label = QtWidgets.QLabel(f"{output_name}")
            checkbox = QtWidgets.QCheckBox()
            checkbox.setChecked(True)
            # template_input = QtWidgets.QLineEdit()
            self.delivery_outputs[output_name] = checkbox
            self.layout.addRow(label, checkbox)

    def get_selected_outputs(self):
        return [
            output_name
            for output_name, checkbox in self.delivery_outputs.items()
            if checkbox.isChecked()
        ]


class KeyValueWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        # Create the layout
        self.layout = QtWidgets.QVBoxLayout(self)
        self.setLayout(self.layout)

        # Create the add button
        self.add_button = QtWidgets.QPushButton("Add")
        self.add_button.clicked.connect(self.add_pair)
        self.layout.addWidget(self.add_button)

        # Create the scroll area
        self.scroll_area = QtWidgets.QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.layout.addWidget(self.scroll_area)

        # Create the scroll area widget
        self.scroll_widget = QtWidgets.QWidget(self.scroll_area)
        self.scroll_area.setWidget(self.scroll_widget)

        # Create the scroll area layout
        self.scroll_layout = QtWidgets.QVBoxLayout(self.scroll_widget)
        self.scroll_widget.setLayout(self.scroll_layout)

        # Create the key-value pairs list
        self.pairs = []

    def add_pair(self):
        # Create the key-value pair widgets
        key_input = QtWidgets.QLineEdit()
        value_input = QtWidgets.QLineEdit()
        delete_button = QtWidgets.QPushButton("Delete")
        delete_button.clicked.connect(lambda: self.delete_pair(delete_button))

        # Add the key-value pair widgets to the layout
        pair_layout = QtWidgets.QHBoxLayout()
        pair_layout.addWidget(key_input)
        pair_layout.addWidget(value_input)
        pair_layout.addWidget(delete_button)
        self.scroll_layout.addLayout(pair_layout)

        # Add the key-value pair to the list
        self.pairs.append((key_input, value_input, delete_button))

    def delete_pair(self, delete_button):
        # Find the key-value pair that corresponds to the delete button
        for pair in self.pairs:
            if pair[2] == delete_button:
                key_input, value_input, delete_button = pair
                break

        # Remove the key-value pair from the layout and the list
        pair_layout = key_input.parent()
        self.scroll_layout.removeItem(pair_layout)
        pair_layout.deleteLater()
        self.pairs.remove((key_input, value_input, delete_button))

    def get_pairs(self):
        # Return the key-value pairs as a dictionary
        return {
            key_input.text(): value_input.text()
            for key_input, value_input in self.pairs
        }


class DeliveryDialog(QtWidgets.QDialog):
    """Interface to control SG deliveries"""

    tool_title = "Deliver SG Entities"
    tool_name = "sg_entity_delivery"

    SIZE_W = 1200
    SIZE_H = 650

    TOKENS_HELP = """
        {project[name]}: Project's full name
        {project[code]}: Project's code
        {asset}: Name of asset or shot
        {task[name]}: Name of task
        {task[type]}: Type of task
        {task[short]}: Short name of task type (eg. 'Modeling' > 'mdl')
        {parent}: Name of hierarchical parent
        {version}: Version number
        {subset}: Subset name
        {family}: Main family name
        {ext}: File extension
        {representation}: Representation name
        {frame}: Frame number for sequence files.
    """

    DELIVERY_TYPES = [
        "Final",
        "Review",
    ]

    DELIVERY_TEMPLATE_DEFAULT = "{package_name}/{output}/<{is_sequence}<{filename}/>>{filename}_<.{frame:0>4}>.{ext}"
    FILENAME_TEMPLATE_DEFAULT = "{SEQ}_{shotnum}_{task[short]}_v{version:0>4}_{vendor}"

    def __init__(self, module, parent=None):
        super(DeliveryDialog, self).__init__(parent)

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

        self.sg = credentials.get_shotgrid_session()

        self._first_show = True
        self._initial_refresh = False
        self._ignore_project_change = False

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

        delivery_outputs = DeliveryOutputsWidget()
        self._delivery_outputs = delivery_outputs
        input_layout.addRow("Outputs {output}", delivery_outputs)

        # Add combobox to choose which delivery type to do
        # delivery_type_widget = QtWidgets.QWidget()
        # delivery_type_layout = QtWidgets.QFormLayout(delivery_type_widget)
        # self.delivery_type_checkboxes = {}
        # for delivery_type in self.DELIVERY_TYPES:
        #     checkbox = QtWidgets.QCheckBox()
        #     checkbox.setChecked(True)

        #     self.delivery_type_checkboxes[delivery_type] = checkbox
        #     # TODO: if we want to add some control
        #     # checkbox.stateChanged.connect(self._update_delivery)
        #     delivery_type_layout.addRow(delivery_type, checkbox)

        # input_layout.addRow("Delivery Type", delivery_type_widget)

        # TODO: validate whether version has already been generated or not
        # Add checkbox to choose whether we want to force the media to be
        # regenerated or not
        # ensure_delivery_media_cb = QtWidgets.QCheckBox()
        # ensure_delivery_media_cb.setChecked(False)
        # ensure_delivery_media_cb.setToolTip(
        #     "Whether we want to force the generation of the delivery media "\
        #     "representations regardless if they already exist or not " \
        #     "(i.e., need to create new slates)"
        # )
        # main_layout.addRow(
        #     "Force regeneration of media", ensure_delivery_media_cb
        # )

        vendor_input = QtWidgets.QLineEdit(
            "ALKX"
        )
        vendor_input.setToolTip(
            "Template string used as a replacement of {vendor} on the path template."
        )
        input_layout.addRow("Vendor {vendor}", vendor_input)

        package_name_input = QtWidgets.QLineEdit(
            "{yyyy}{mm}{dd}_{vendor}_A"
        )
        package_name_input.setToolTip(
            "Template string used as a replacement of {package_name} on the path template."
        )
        input_layout.addRow("Package name {package_name}", package_name_input)

        filename_input = QtWidgets.QLineEdit(self.FILENAME_TEMPLATE_DEFAULT)
        filename_input.setToolTip(
            "Template string used as a replacement of {filename} on the path template."
        )
        input_layout.addRow("File name {filename}", filename_input)

        version_input = QtWidgets.QLineEdit("")
        version_input.setToolTip(
            "Override the version number of the delivery media. If left empty, " \
            "the version will just be increased from the last existing version. "
        )
        # Set the validator for the QLineEdit to QIntValidator
        version_input.setValidator(QtGui.QIntValidator())
        input_layout.addRow(
            "Version override {version}", version_input
        )

        task_override_combo = QtWidgets.QComboBox()
        task_override_combo.addItems(
            [
                "-- Use source task --",
                "blockvis",
                "previs",
                "techvis",
                "postvis",
                "color",
                "dev",
                "layout",
                "anim",
                "comp",
                "precomp",
                "prod",
                "howto",
            ]
        )
        task_override_combo.setEditable(True)
        input_layout.addRow("Task code {task[code]}", task_override_combo)

        common_key_values = KeyValueWidget()
        input_layout.addRow("Custom tokens", common_key_values)

        template_input = QtWidgets.QLineEdit(self.DELIVERY_TEMPLATE_DEFAULT)
        template_input.setToolTip(
            "Template string used as a replacement for where the delivery media "
            "will be written to.\nAvailable tokens: {}\nTo make a token optional"
            "so it's ignored if it's not available on the entity you can just "
            "wrap it with '<' and '>' (i.e., <{{frame}}> will only be added in the "
            "case where {{frame}} doesn't exist on that output)".format(
                self.TOKENS_HELP
            )
        )

        input_layout.addRow("Path template", template_input)

        main_layout.addWidget(input_widget)

        # SG input widgets
        sg_input_widget = QtWidgets.QWidget()
        input_group = QtWidgets.QButtonGroup(sg_input_widget)
        input_group.setExclusive(True)

        # TODO: show only the available playlists

        sg_playlist_id_input = QtWidgets.QLineEdit()
        sg_playlist_id_input.setToolTip("Integer id of the SG Playlist (i.e., '3909')")
        sg_playlist_id_input.editingFinished.connect(self._playlist_id_changed)
        self.playlist_radio_btn = QtWidgets.QRadioButton("SG Playlist Id")
        self.playlist_radio_btn.setChecked(True)
        input_group.addButton(self.playlist_radio_btn)
        input_layout.addRow(self.playlist_radio_btn, sg_playlist_id_input)

        sg_version_id_input = QtWidgets.QLineEdit()
        sg_version_id_input.setToolTip("Integer id of the SG Version (i.e., '314726')")
        sg_version_id_input.editingFinished.connect(self._version_id_changed)
        self.version_radio_btn = QtWidgets.QRadioButton("SG Version Id")
        input_group.addButton(self.version_radio_btn)
        input_layout.addRow(self.version_radio_btn, sg_version_id_input)

        main_layout.addWidget(sg_input_widget)

        generate_delivery_media_btn = QtWidgets.QPushButton(
            "Generate delivery media"
        )
        generate_delivery_media_btn.setToolTip(
            "Run the delivery media pipeline and ensure delivery media exists for all " \
            "outputs (Final Output, Review Output in ShotGrid)"
        )
        generate_delivery_media_btn.clicked.connect(
            self._on_generate_delivery_media_clicked
        )

        main_layout.addWidget(generate_delivery_media_btn)

        #### REPORT ####
        text_area = QtWidgets.QTextEdit()
        text_area.setReadOnly(True)
        text_area.setVisible(False)

        main_layout.addWidget(text_area)

        # Assign widgets we want to reuse to class instance

        self._projects_combobox = projects_combobox
        self._sg_playlist_id_input = sg_playlist_id_input
        self._sg_version_id_input = sg_version_id_input
        self._text_area = text_area
        self._task_override_combo = task_override_combo

    def showEvent(self, event):
        super(DeliveryDialog, self).showEvent(event)
        if self._first_show:
            self._first_show = False
            self.setStyleSheet(style.load_stylesheet())
            tools_lib.center_window(self)

        if not self._initial_refresh:
            self._initial_refresh = True
            self.refresh()

    def _playlist_id_changed(self):
        # If there's a comma in the text, remove it and set the modified text
        cur_text = self._sg_playlist_id_input.text()
        new_text = cur_text.replace("\t", "")
        new_text = cur_text.replace(" ", "")
        new_text = cur_text.replace(",", "")
        self._sg_playlist_id_input.setText(new_text)

    def _version_id_changed(self):
        # If there's a comma in the text, remove it and set the modified text
        cur_text = self._sg_version_id_input.text()
        new_text = cur_text.replace("\t", "")
        new_text = cur_text.replace(" ", "")
        new_text = cur_text.replace(",", "")
        self._sg_version_id_input.setText(new_text)

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

        sg_project = self.sg.find_one("Project", [["name", "is", project_name]])
        representation_names, _ = delivery.get_representation_names(
            self.sg, sg_project["id"], "Project", ["review", "final"]
        )
        self._delivery_outputs.update(representation_names)

        project_name = self.dbcon.active_project() or "No project selected"
        title = "{} - {}".format(self.tool_title, project_name)
        self.setWindowTitle(title)

    # -------------------------------
    # Delay calling blocking methods
    # -------------------------------

    def refresh(self):
        tools_lib.schedule(self._refresh, 50, channel="mongo")

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

    def _on_generate_delivery_media_clicked(self):
        output_names = self._delivery_outputs.get_selected_outputs()

        if self.playlist_radio_btn.isChecked():
            report_items, success = media.generate_delivery_media_playlist_id(
                self._sg_playlist_id_input.text(),
                delivery_types=output_names,
                force=self._ensure_delivery_media_cb.isChecked(),
                description=self._task_override_combo.currentText(),
                override_version=self.version_input.text(),
            )
        else:
            report_items, success = media.generate_delivery_media_version_id(
                self._sg_version_id_input.text(),
                delivery_types=output_names,
                force=self._ensure_delivery_media_cb.isChecked(),
                description=self._task_override_combo.currentText(),
                override_version=self.version_input.text(),
            )

        self._text_area.setText(self._format_report(report_items, success))
        self._text_area.setVisible(True)


def main():
    app_instance = QtWidgets.QApplication.instance()
    if app_instance is None:
        app_instance = QtWidgets.QApplication([])

    if platform.system().lower() == "windows":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("sg_delivery")

    window = DeliveryDialog()
    window.show()
    app_instance.exec_()
