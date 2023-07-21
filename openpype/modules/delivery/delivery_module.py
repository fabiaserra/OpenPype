import os

from openpype.modules import (
    OpenPypeModule,
    ITrayModule
)


class DeliveryModule(OpenPypeModule, ITrayModule):
    label = "Delivery"
    name = "delivery"
    enabled = True
    tray_wrapper = None

    def initialize(self, modules_settings):
        pass

    def tray_init(self):
        from .tray.delivery_tray import DeliveryTrayWrapper

        self.tray_wrapper = DeliveryTrayWrapper(self)

    def tray_start(self):
        return

    def tray_exit(self, *args, **kwargs):
        return self.tray_wrapper

    def tray_menu(self, tray_menu):
        return self.tray_wrapper.tray_menu(tray_menu)
