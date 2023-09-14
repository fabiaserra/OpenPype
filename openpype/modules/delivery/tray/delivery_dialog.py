import platform
from qtpy import QtCore, QtWidgets, QtGui

from openpype import style
from openpype import resources
from openpype.modules.delivery.scripts import sg_delivery


class DeliveryDialog(QtWidgets.QDialog):
    SIZE_W = 1000
    SIZE_H = 650

    DELIVERY_TYPES = [
        "Final",
        "Review",
    ]

    TEMPLATE_ROOT = "{yyyy}{mm}{dd}_ALKX/_{representation}/{delivery_name}_{description}"
    DELIVERY_TEMPLATES = {
        "Single File": f"{TEMPLATE_ROOT}_v{{version:0>4}}_ALKX_<_{{delivery_suffix}}>.{{ext}}",
        "Sequence": f"{TEMPLATE_ROOT}_v{{version:0>4}}/{{SEQ}}_{{shotnum}}_{{description}}_v{{version:0>4}}_ALKX_<.{{frame:0>4}}>.{{ext}}",
        "V0 Single File": f"{TEMPLATE_ROOT}_v0000_ALKX_<_{{delivery_suffix}}>.{{ext}}",
        "V0 Sequence": f"{TEMPLATE_ROOT}_v0000/{{SEQ}}_{{shotnum}}_{{description}}_v0000_ALKX_<.{{frame:0>4}}>.{{ext}}",
    }

    def __init__(self, module, parent=None):
        super(DeliveryDialog, self).__init__(parent)

        self.setWindowTitle("Deliver SG Entities")

        self._module = module

        icon = QtGui.QIcon(resources.get_openpype_icon_filepath())
        self.setWindowIcon(icon)

        self.setWindowFlags(
            QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowCloseButtonHint
            | QtCore.Qt.WindowMinimizeButtonHint
        )

        self.setMinimumSize(QtCore.QSize(self.SIZE_W, self.SIZE_H))
        self.setStyleSheet(style.load_stylesheet())

        self.ui_init()

    def ui_init(self):

        main_layout = QtWidgets.QVBoxLayout(self)

        #### COMMON ####
        # Common input widgets for delivery and republish features
        sg_input_widget = QtWidgets.QWidget(self)
        sg_input_layout = QtWidgets.QFormLayout(sg_input_widget)
        sg_input_layout.setContentsMargins(5, 5, 5, 5)

        self.input_group = QtWidgets.QButtonGroup()
        self.input_group.setExclusive(True)

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

        deliver_btn = QtWidgets.QPushButton("Deliver")
        deliver_btn.setToolTip("Deliver given SG entity assets")
        deliver_btn.clicked.connect(self._on_delivery_clicked)

        main_layout.addWidget(deliver_btn)

        # Add a stretch between sections
        main_layout.addStretch(1)

        #### REPUBLISH ####
        # Widgets related to republish functionality
        republish_input_widget = QtWidgets.QWidget(self)
        republish_input_layout = QtWidgets.QFormLayout(republish_input_widget)
        republish_input_layout.setContentsMargins(5, 5, 5, 5)

        # Add checkbox to choose whether we want to force the media to be
        # regenerated or not
        self.ensure_delivery_media_cb = QtWidgets.QCheckBox()
        self.ensure_delivery_media_cb.setChecked(False)
        self.ensure_delivery_media_cb.setToolTip(
            "Whether we want to force the generation of the delivery media "\
            "representations regardless if they already exist or not " \
            "(i.e., need to create new slates)"
        )
        republish_input_layout.addRow(
            "Force regeneration of media", self.ensure_delivery_media_cb
        )

        main_layout.addWidget(republish_input_widget)

        republish_media_btn = QtWidgets.QPushButton("Republish delivery media")
        republish_media_btn.setToolTip(
            "Run the publish pipeline and ensure delivery media exists for all " \
            "representations"
        )
        republish_media_btn.clicked.connect(self._on_republish_media_clicked)

        main_layout.addWidget(republish_media_btn)

        #### GENERATE DELIVERY ####
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
        self.delivery_name_template_input = QtWidgets.QLineEdit(
            "{SEQ}_{shotnum}_{description}_v{version:0>4}_ALKX<_{delivery_suffix}>"
        )
        self.delivery_name_template_input.setToolTip(
            "Template string to use for delivery file name. All the fields that " \
            "have the { } brackets will be replaced with the appropriate values " \
            "dynamically."
        )
        generate_delivery_input_layout.addRow(
            "Delivery name template", self.delivery_name_template_input
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
            "Run the publish pipeline and ensure delivery media exists for all " \
            "representations"
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

    def _on_delivery_clicked(self):
        delivery_types = self._get_selected_delivery_types()
        delivery_templates = self._get_delivery_templates()

        if self.playlist_radio_btn.isChecked():
            report_items, success = sg_delivery.deliver_playlist_id(
                self.sg_playlist_id_input.text(),
                delivery_types=delivery_types,
                delivery_templates=delivery_templates,
            )
        else:
            report_items, success = sg_delivery.deliver_version_id(
                self.sg_version_id_input.text(),
                delivery_types=delivery_types,
                delivery_templates=delivery_templates,
            )

        self.text_area.setText(self._format_report(report_items, success))
        self.text_area.setVisible(True)

    def _on_republish_media_clicked(self):
        delivery_types = self._get_selected_delivery_types()

        if self.playlist_radio_btn.isChecked():
            report_items, success = sg_delivery.republish_playlist_id(
                self.sg_playlist_id_input.text(),
                delivery_types=delivery_types,
                force=self.ensure_delivery_media_cb.isChecked(),
            )
        else:
            report_items, success = sg_delivery.republish_version_id(
                self.sg_version_id_input.text(),
                delivery_types=delivery_types,
                force=self.ensure_delivery_media_cb.isChecked(),
            )

        self.text_area.setText(self._format_report(report_items, success))
        self.text_area.setVisible(True)

    def _on_generate_delivery_media_clicked(self):
        delivery_types = self._get_selected_delivery_types()

        if self.playlist_radio_btn.isChecked():
            report_items, success = sg_delivery.generate_delivery_media_playlist_id(
                self.sg_playlist_id_input.text(),
                delivery_types=delivery_types,
                force=self.ensure_delivery_media_cb.isChecked(),
                description=self.description_combo.currentText(),
                override_version=self.delivery_version_input.text(),
            )
        else:
            report_items, success = sg_delivery.generate_delivery_media_version_id(
                self.sg_version_id_input.text(),
                delivery_types=delivery_types,
                force=self.ensure_delivery_media_cb.isChecked(),
                description=self.description_combo.currentText(),
                override_version=self.delivery_version_input.text(),
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
