import platform
from qtpy import QtCore, QtWidgets, QtGui

from openpype import style
from openpype import resources
from openpype.modules.delivery.scripts import sg_delivery


class DeliveryDialog(QtWidgets.QDialog):
    SIZE_W = 550
    SIZE_H = 300

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

        self.deliver_button = QtWidgets.QPushButton("Deliver")
        self.deliver_button.setToolTip("Deliver given SG playlist assets")
        self.deliver_button.clicked.connect(self._on_delivery_clicked)

        self.text_area = QtWidgets.QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setVisible(False)
        self.text_area.setMinimumHeight(100)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(input_widget)
        layout.addStretch(1)
        layout.addWidget(self.deliver_button)
        layout.addWidget(self.text_area)

    def _format_report(self, report_items):
        """Format final result and error details as html."""
        msg = "Delivery finished"
        if not report_items:
            msg += " successfully"
        else:
            msg += " with errors"
        txt = "<h2>{}</h2>".format(msg)
        for header, data in report_items.items():
            txt += "<h3>{}</h3>".format(header)
            for item in data:
                txt += "{}<br>".format(item)

        return txt

    def _on_delivery_clicked(self):
        report_items = sg_delivery.deliver_playlist(self.sg_playlist_id_input.text())
        self.text_area.setText(self._format_report(report_items))
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
