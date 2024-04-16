# -*- coding: utf-8 -*-
"""Pipeline tools for OpenPype Mari integration."""
import os
import logging
from operator import attrgetter

import json

from openpype.host import HostBase, IWorkfileHost, ILoadHost, IPublishHost
import pyblish.api
from openpype.pipeline import (
    register_creator_plugin_path,
    register_loader_plugin_path,
    register_inventory_action_path,
    AVALON_CONTAINER_ID,
)
# from openpype.hosts.mari.api.menu import OpenPypeMenu
# from openpype.hosts.mari.api import lib
# from openpype.hosts.mari.api.plugin import MS_CUSTOM_ATTRIB
from openpype.hosts.mari import MARI_HOST_DIR

log = logging.getLogger("openpype.hosts.mari")

PLUGINS_DIR = os.path.join(MARI_HOST_DIR, "plugins")
PUBLISH_PATH = os.path.join(PLUGINS_DIR, "publish")
LOAD_PATH = os.path.join(PLUGINS_DIR, "load")
CREATE_PATH = os.path.join(PLUGINS_DIR, "create")
INVENTORY_PATH = os.path.join(PLUGINS_DIR, "inventory")


class MariHost(HostBase, IWorkfileHost, ILoadHost, IPublishHost):

    name = "mari"
    menu = None

    def __init__(self):
        super(MariHost, self).__init__()
        self._op_events = {}
        self._has_been_setup = False

    def install(self):
        pyblish.api.register_host("mari")

        pyblish.api.register_plugin_path(PUBLISH_PATH)
        register_loader_plugin_path(LOAD_PATH)
        register_creator_plugin_path(CREATE_PATH)
        register_inventory_action_path(INVENTORY_PATH)

        # self._register_callbacks()
        # self.menu = OpenPypeMenu()

        self._has_been_setup = True

    def has_unsaved_changes(self):
        # TODO: how to get it from mari?
        return True

    def get_workfile_extensions(self):
        return [".mari"]

    def save_workfile(self, dst_path=None):
        rt.saveMaxFile(dst_path)
        return dst_path

    def open_workfile(self, filepath):
        pass
        # rt.checkForSave()
        # rt.loadMaxFile(filepath)
        # return filepath

    def get_current_workfile(self):
        return os.path.join(rt.mariFilePath, rt.mariFileName)

    def get_containers(self):
        return ls()

    def _register_callbacks(self):
        pass
        # rt.callbacks.removeScripts(id=rt.name("OpenPypeCallbacks"))

        # rt.callbacks.addScript(
        #     rt.Name("postLoadingMenus"),
        #     self._deferred_menu_creation, id=rt.Name('OpenPypeCallbacks'))

    def _deferred_menu_creation(self):
        self.log.info("Building menu ...")
        # self.menu = OpenPypeMenu()

    @staticmethod
    def create_context_node():
        """Helper for creating context holding node."""
        pass

    def update_context_data(self, data, changes):
        pass

    def get_context_data(self):
        pass

    def save_file(self, dst_path=None):
        pass


def ls() -> list:
    """Get all OpenPype instances."""
    pass


def containerise(name: str, nodes: list, context,
                 namespace=None, loader=None, suffix="_CON"):
    data = {
        "schema": "openpype:container-2.0",
        "id": AVALON_CONTAINER_ID,
        "name": name,
        "namespace": namespace or "",
        "loader": loader,
        "representation": context["representation"]["_id"],
    }
    container_name = f"{namespace}:{name}{suffix}"
    container = rt.container(name=container_name)
    import_custom_attribute_data(container, nodes)
    # if not lib.imprint(container_name, data):
    #     print(f"imprinting of {container_name} failed.")
    return container


def load_custom_attribute_data():
    """Re-loading the Openpype/AYON custom parameter built by the creator

    Returns:
        attribute: re-loading the custom OP attributes set in Maxscript
    """
    pass


def import_custom_attribute_data(container: str, selections: list):
    """Importing the Openpype/AYON custom parameter built by the creator

    Args:
        container (str): target container which adds custom attributes
        selections (list): nodes to be added into
        group in custom attributes
    """
    pass


def update_custom_attribute_data(container: str, selections: list):
    """Updating the Openpype/AYON custom parameter built by the creator

    Args:
        container (str): target container which adds custom attributes
        selections (list): nodes to be added into
        group in custom attributes
    """
    pass


def get_previous_loaded_object(container: str):
    """Get previous loaded_object through the OP data

    Args:
        container (str): the container which stores the OP data

    Returns:
        node_list(list): list of nodes which are previously loaded
    """
    pass
