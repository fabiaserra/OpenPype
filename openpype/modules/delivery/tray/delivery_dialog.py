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

        # Add the new rows
        self.delivery_outputs = {}
        for output_name in delivery_output_names:
            label = QtWidgets.QLabel(f"{output_name}")
            template_input = QtWidgets.QLineEdit()
            self.delivery_outputs[output_name] = template_input
            self.layout.addRow(label, template_input)


class DeliveryDialog(QtWidgets.QDialog):
    """Interface to control SG deliveries"""

    tool_title = "Deliver SG Entities"
    tool_name = "sg_entity_delivery"

    SIZE_W = 1000
    SIZE_H = 650

    DELIVERY_TYPES = [
        "Final",
        "Review",
    ]

    TEMPLATE_ROOT = "{yyyy}{mm}{dd}_ALKX/_{representation}/{SEQ}_{shotnum}_{description}"
    DELIVERY_TEMPLATES = {
        "Single File": f"{TEMPLATE_ROOT}_v{{version:0>4}}_ALKX_<_{{delivery_suffix}}>.{{ext}}",
        "Sequence": f"{TEMPLATE_ROOT}_v{{version:0>4}}/{{SEQ}}_{{shotnum}}_{{description}}_v{{version:0>4}}_ALKX_<.{{frame:0>4}}>.{{ext}}",
        "V0 Single File": f"{TEMPLATE_ROOT}_v0000_ALKX_<_{{delivery_suffix}}>.{{ext}}",
        "V0 Sequence": f"{TEMPLATE_ROOT}_v0000/{{SEQ}}_{{shotnum}}_{{description}}_v0000_ALKX_<.{{frame:0>4}}>.{{ext}}",
    }

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

        #### COMMON ####

        # Project combobox
        projects_combobox = QtWidgets.QComboBox()
        combobox_delegate = QtWidgets.QStyledItemDelegate(self)
        projects_combobox.setItemDelegate(combobox_delegate)
        projects_combobox.currentTextChanged.connect(self.on_project_change)

        main_layout.addWidget(projects_combobox)

        self._projects_combobox = projects_combobox

        # Common input widgets for delivery and republish features
        sg_input_widget = QtWidgets.QWidget(self)
        sg_input_layout = QtWidgets.QFormLayout(sg_input_widget)
        sg_input_layout.setContentsMargins(5, 5, 5, 5)

        self.input_group = QtWidgets.QButtonGroup()
        self.input_group.setExclusive(True)

        # TODO: show only the available playlists

        self.sg_playlist_id_input = QtWidgets.QLineEdit()
        self.sg_playlist_id_input.setToolTip("Integer id of the SG Playlist (i.e., '3909')")
        self.sg_playlist_id_input.editingFinished.connect(self.handle_playlist_id_changed)
        self.playlist_radio_btn = QtWidgets.QRadioButton("SG Playlist Id")
        self.playlist_radio_btn.setChecked(True)
        self.input_group.addButton(self.playlist_radio_btn)
        sg_input_layout.addRow(self.playlist_radio_btn, self.sg_playlist_id_input)

        self.sg_version_id_input = QtWidgets.QLineEdit()
        self.sg_version_id_input.setToolTip("Integer id of the SG Version (i.e., '314726')")
        self.sg_version_id_input.editingFinished.connect(self.handle_version_id_changed)
        self.version_radio_btn = QtWidgets.QRadioButton("SG Version Id")
        self.input_group.addButton(self.version_radio_btn)
        sg_input_layout.addRow(self.version_radio_btn, self.sg_version_id_input)

        # Add combobox to choose which delivery type to do
        self.delivery_type_checkboxes = {}
        for delivery_type in self.DELIVERY_TYPES:
            checkbox = QtWidgets.QCheckBox()
            checkbox.setChecked(True)

            self.delivery_type_checkboxes[delivery_type] = checkbox
            # TODO: if we want to add some control
            # checkbox.stateChanged.connect(self._update_delivery)
            sg_input_layout.addRow(delivery_type, checkbox)

        main_layout.addWidget(sg_input_widget)

        # Add a stretch between sections
        main_layout.addStretch(1)

        delivery_outputs = DeliveryOutputsWidget()
        main_layout.addWidget(delivery_outputs)
        self._delivery_outputs = delivery_outputs

        #### DELIVERY ####
        # Widgets related to delivery functionality
        delivery_input_widget = QtWidgets.QWidget(self)
        delivery_input_layout = QtWidgets.QFormLayout(delivery_input_widget)
        delivery_input_layout.setContentsMargins(5, 5, 5, 5)

        main_layout.addWidget(delivery_input_widget)

        self.delivery_template_inputs = {}
        for key, delivery_template in self.DELIVERY_TEMPLATES.items():
            label = QtWidgets.QLabel(f"{key} Template")
            template_input = QtWidgets.QLineEdit(delivery_template)

            self.delivery_template_inputs[key] = template_input
            delivery_input_layout.addRow(label, template_input)

        #### GENERATE DELIVERY ####

        # TODO: validate whether version has already been generated or not
        # Add checkbox to choose whether we want to force the media to be
        # regenerated or not
        # self.ensure_delivery_media_cb = QtWidgets.QCheckBox()
        # self.ensure_delivery_media_cb.setChecked(False)
        # self.ensure_delivery_media_cb.setToolTip(
        #     "Whether we want to force the generation of the delivery media "\
        #     "representations regardless if they already exist or not " \
        #     "(i.e., need to create new slates)"
        # )
        # main_layout.addRow(
        #     "Force regeneration of media", self.ensure_delivery_media_cb
        # )

        # Widgets related to generate delivery functionality
        generate_delivery_input_widget = QtWidgets.QWidget(self)
        generate_delivery_input_layout = QtWidgets.QFormLayout(
            generate_delivery_input_widget
        )
        generate_delivery_input_layout.setContentsMargins(5, 5, 5, 5)

        self.description_combo = QtWidgets.QComboBox()
        self.description_combo.addItems(
            [
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
        generate_delivery_input_layout.addRow(
            "Description type", self.description_combo
        )
        self.delivery_output_template_input = QtWidgets.QLineEdit(
            "{SEQ}_{shotnum}_{description}_v{version:0>4}_ALKX<_{output_suffix}>"
        )
        self.delivery_output_template_input.setToolTip(
            "Template string to use for delivery file name. All the fields that " \
            "have the { } brackets will be replaced with the appropriate values " \
            "dynamically."
        )
        generate_delivery_input_layout.addRow(
            "Delivery output template", self.delivery_output_template_input
        )

        self.delivery_version_input = QtWidgets.QLineEdit("")
        self.delivery_version_input.setToolTip(
            "Override the version number of the delivery media. If left empty, " \
            "the version will just be increased from the last existing version. "
        )
        # Set the validator for the QLineEdit to QIntValidator
        self.delivery_version_input.setValidator(QtGui.QIntValidator())
        generate_delivery_input_layout.addRow(
            "Delivery version override", self.delivery_version_input
        )

        main_layout.addWidget(generate_delivery_input_widget)

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
        self.text_area = QtWidgets.QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setVisible(False)

        main_layout.addWidget(self.text_area)

    def showEvent(self, event):
        super(DeliveryDialog, self).showEvent(event)
        if self._first_show:
            self._first_show = False
            self.setStyleSheet(style.load_stylesheet())
            tools_lib.center_window(self)

        if not self._initial_refresh:
            self._initial_refresh = True
            self.refresh()

    def handle_playlist_id_changed(self):
        # If there's a comma in the text, remove it and set the modified text
        cur_text = self.sg_playlist_id_input.text()
        new_text = cur_text.replace("\t", "")
        new_text = cur_text.replace(" ", "")
        new_text = cur_text.replace(",", "")
        self.sg_playlist_id_input.setText(new_text)

    def handle_version_id_changed(self):
        # If there's a comma in the text, remove it and set the modified text
        cur_text = self.sg_version_id_input.text()
        new_text = cur_text.replace("\t", "")
        new_text = cur_text.replace(" ", "")
        new_text = cur_text.replace(",", "")
        self.sg_version_id_input.setText(new_text)

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

        sg = credentials.get_shotgrid_session()
        sg_project = sg.find_one("Project", [["name", "is", project_name]])
        representation_names, _ = delivery.get_representation_names(
            sg, sg_project["id"], "Project", self._get_selected_delivery_types()
        )
        print("REP NAMES: %s" % representation_names)
        self._delivery_outputs.update(representation_names)

        # self.family_config_cache.refresh()
        # self.groups_config.refresh()

        # self._refresh_assets()
        # self._assetschanged()

        project_name = self.dbcon.active_project() or "No project selected"
        title = "{} - {}".format(self.tool_title, project_name)
        self.setWindowTitle(title)

    # -------------------------------
    # Delay calling blocking methods
    # -------------------------------

    def refresh(self):
        self.echo("Fetching results..")
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

    def _get_selected_delivery_types(self):
        """Returns list of delivery types selected from checkboxes."""
        delivery_types = []
        for delivery_type, checkbox in self.delivery_type_checkboxes.items():
            if checkbox.isChecked():
                delivery_types.append(delivery_type.lower())

        return delivery_types

    def _get_delivery_templates(self):
        """Returns list of delivery types selected from checkboxes."""
        delivery_templates = {}
        for key in self.DELIVERY_TEMPLATES.keys():
            delivery_templates[key] = self.delivery_template_inputs[key].text()

        return delivery_templates

    def _on_generate_delivery_media_clicked(self):
        delivery_types = self._get_selected_delivery_types()

        if self.playlist_radio_btn.isChecked():
            report_items, success = media.generate_delivery_media_playlist_id(
                self.sg_playlist_id_input.text(),
                delivery_types=delivery_types,
                force=self.ensure_delivery_media_cb.isChecked(),
                description=self.description_combo.currentText(),
                override_version=self.delivery_version_input.text(),
                out_filename_template=self.delivery_output_template_input.text(),
            )
        else:
            report_items, success = media.generate_delivery_media_version_id(
                self.sg_version_id_input.text(),
                delivery_types=delivery_types,
                force=self.ensure_delivery_media_cb.isChecked(),
                description=self.description_combo.currentText(),
                override_version=self.delivery_version_input.text(),
                out_filename_template=self.delivery_output_template_input.text(),
            )

        self.text_area.setText(self._format_report(report_items, success))
        self.text_area.setVisible(True)


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
