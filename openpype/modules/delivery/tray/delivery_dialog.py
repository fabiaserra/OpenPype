import platform
from qtpy import QtCore, QtWidgets, QtGui

from openpype import style
from openpype import resources
from openpype.modules.delivery.scripts import sg_delivery


class DeliveryDialog(QtWidgets.QDialog):
    SIZE_W = 1000
    SIZE_H = 500

    DELIVERY_TYPES = [
        "final",
        "review",
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
        self.sg_playlist_id = QtWidgets.QLabel("SG Playlist id:")
        self.sg_playlist_id_input = QtWidgets.QLineEdit()

        input_widget = QtWidgets.QWidget(self)
        input_layout = QtWidgets.QFormLayout(input_widget)
        input_layout.setContentsMargins(10, 15, 5, 5)
        input_layout.addRow(self.sg_playlist_id, self.sg_playlist_id_input)

        self.delivery_template_inputs = {}
        for key, delivery_template in self.DELIVERY_TEMPLATES.items():
            label = QtWidgets.QLabel(f"{key} Template")
            template_input = QtWidgets.QLineEdit(delivery_template)

            self.delivery_template_inputs[key] = template_input
            input_layout.addRow(label, template_input)

        # Add combobox to choose which delivery type to do
        self.delivery_type_checkboxes = {}
        for delivery_type in self.DELIVERY_TYPES:
            checkbox = QtWidgets.QCheckBox()
            checkbox.setChecked(False)

            self.delivery_type_checkboxes[delivery_type] = checkbox
            # TODO: if we want to add some control
            # checkbox.stateChanged.connect(self._update_delivery)
            input_layout.addRow(delivery_type, checkbox)

        deliver_button = QtWidgets.QPushButton("Deliver")
        deliver_button.setToolTip("Deliver given SG playlist assets")
        deliver_button.clicked.connect(self._on_delivery_clicked)

        self.text_area = QtWidgets.QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setVisible(False)
        self.text_area.setMinimumHeight(250)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(input_widget)
        layout.addStretch(1)
        layout.addWidget(deliver_button)
        layout.addWidget(self.text_area)

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
                delivery_types.append(delivery_type)

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

        report_items, success = sg_delivery.deliver_playlist(
            self.sg_playlist_id_input.text(),
            delivery_types=delivery_types,
            delivery_templates=delivery_templates,
        )
        self.text_area.setText(self._format_report(report_items, success))
        self.text_area.setVisible(True)


def main():

    app_instance = QtWidgets.QApplication.instance()
    if app_instance is None:
        app_instance = QtWidgets.QApplication([])

    if platform.system().lower() == "windows":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            u"traypublisher"
        )

    window = DeliveryDialog()
    window.show()
    app_instance.exec_()
