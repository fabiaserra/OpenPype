import platform
from qtpy import QtCore, QtWidgets, QtGui

from openpype import style
from openpype import resources
from openpype.modules.delivery.scripts import sg_delivery


class DeliveryDialog(QtWidgets.QDialog):
    SIZE_W = 1000
    SIZE_H = 500

    DELIVERY_TYPES = [
        "Final",
        "Review",
    ]

    TEMPLATE_ROOT = "{yyyy}{mm}{dd}/{representation}/{asset}_{task[short]}"
    DELIVERY_TEMPLATES = {
        "Single File": f"{TEMPLATE_ROOT}_v{{version:0>3}}.{{ext}}",
        "Sequence": f"{TEMPLATE_ROOT}_v{{version:0>3}}/{{asset}}_{{task[short]}}_v{{version:0>3}}<.{{frame:0>4}}>.{{ext}}",
        "V0 Single File": f"{TEMPLATE_ROOT}_v0.{{ext}}",
        "V0 Sequence": f"{TEMPLATE_ROOT}_v0/{{asset}}_{{task[short]}}_v0<.{{frame:0>4}}>.{{ext}}",
    }

    def __init__(self, module, parent=None):
        super(DeliveryDialog, self).__init__(parent)

        self.setWindowTitle("Deliver SG Playlist")

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
        self.playlist_radio_btn = QtWidgets.QRadioButton("SG Playlist")
        self.playlist_radio_btn.setChecked(True)
        self.input_group.addButton(self.playlist_radio_btn)
        sg_input_layout.addRow(self.playlist_radio_btn, self.sg_playlist_id_input)

        self.sg_version_id_input = QtWidgets.QLineEdit()
        self.version_radio_btn = QtWidgets.QRadioButton("SG Version")
        self.input_group.addButton(self.version_radio_btn)
        sg_input_layout.addRow(self.version_radio_btn, self.sg_version_id_input)

        # Add combobox to choose which delivery type to do
        self.delivery_type_checkboxes = {}
        for delivery_type in self.DELIVERY_TYPES:
            checkbox = QtWidgets.QCheckBox()
            checkbox.setChecked(False)

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

        #### REPORT ####
        self.text_area = QtWidgets.QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setVisible(False)
        self.text_area.setMinimumHeight(250)

        main_layout.addWidget(self.text_area)


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
