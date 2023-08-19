import ast
import bisect
import glob
import os
import pathlib
import PyOpenColorIO
import random
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy.QtGui import *

import hiero

from openpype.client import get_asset_by_name, get_project
from openpype.hosts.hiero.api.constants import OPENPYPE_TAG_NAME
from openpype.hosts.hiero.api.lib import (set_trackitem_openpype_tag)
from openpype.lib import Logger
from openpype.pipeline.context_tools import (
    get_current_project_name,
    get_hierarchy_env,
)
from openpype.settings import get_project_settings


log = Logger.get_logger(__name__)


def get_active_ocio_config():
    """Get the active OCIO configuration.

    This function retrieves the OCIO configuration from the environment
    variable 'OCIO' if available. Otherwise, it checks the current Hiero
    session's project settings for the OCIO configuration. If no active
    sequence is loaded, a default OCIO configuration is used.

    Returns:
        PyOpenColorIO.Config: The active OCIO configuration.
    """
    env_ocio_path = os.getenv("OCIO")

    if env_ocio_path:
        ocio_path = env_ocio_path
        ocio_config = PyOpenColorIO.Config.CreateFromFile(ocio_path)
        # Returning now. No need to search other places for config
        return ocio_config

    # If not OCIO found in environ then check project OCIO
    active_seq = hiero.ui.activeSequence()
    configs_path = __file__.split("plugins")[0] + "plugins/OCIOConfigs/configs"
    if active_seq:
        project = active_seq.project()
        if project.ocioConfigPath():
            ocio_path = project.ocioConfigPath()
        # Use default config path from sw
        elif project.ocioConfigName():
            hiero_configs = glob.glob(
                configs_path + "/**/*.ocio", recursive=True
            )
            for config in hiero_configs:
                config_name = pathlib.Path(config).parent.name
                if project.ocioConfigName() == config_name:
                    ocio_path = config

    # Else statement is a catch for when the spreadsheet runs without sequence
    # loaded
    else:
        ocio_path = os.path.join(configs_path, "nuke-default/config.ocio")

    ocio_config = PyOpenColorIO.Config.CreateFromFile(ocio_path)

    return ocio_config


class Colorspace_Widget(QMainWindow):
    def __init__(self, ocio_config, parent=None):
        super(Colorspace_Widget, self).__init__(parent)

        # Change how roles are added - add them to the base menu using the
        # getRoles method
        self.colorspace_button = QPushButton("Colorspaces")
        # Menu must be stored on self. Button won't react properly without
        self.root_menu = QMenu("Main")

        menu_dict = {}
        color_roles = [f"{x[0]} ({x[1]})" for x in ocio_config.getRoles()]
        color_spaces = []
        for color_space in ocio_config.getColorSpaces():
            color_spaces.append(
                (color_space.getName(), color_space.getFamily())
            )

        for role in color_roles:
            role_action = QAction(role, self.root_menu)
            self.root_menu.addAction(role_action)

        # Create menu_dict which stores the hierarchy and associated colorspace
        for name, family in color_spaces:
            parts = family.split("/")
            current_dict = menu_dict
            for part in parts:
                current_dict = current_dict.setdefault(part, {})
            current_dict[name] = None

        self.colorspace_menu = QMenu("Colorspaces")
        self.root_menu.addMenu(self.colorspace_menu)
        for key, value in menu_dict.items():
            submenu = self.build_menu(value, key)
            self.colorspace_menu.addMenu(submenu)

        self.colorspace_button.setMenu(self.root_menu)
        self.setCentralWidget(self.colorspace_button)

    def menu_insertion_target(self, actions, menu_text):
        """Determine the insertion point for a menu or action within a list of
        actions.

        Args:
            actions (list): List of actions, where each item is a tuple
                            containing an action and a boolean indicating
                            whether it's a menu.
            menu_text (str): The text of the menu to insert.

        Returns:
            tuple: A tuple containing the action to insert before and its
                            index.
        """
        menu_actions = []
        normal_actions = []

        for action, is_menu in actions:
            if is_menu:
                menu_actions.append((action, is_menu))
            else:
                normal_actions.append((action, is_menu))

        if menu_actions:
            # Sort menus alphabetically
            index = bisect.bisect_left(
                [x[0].text() for x in menu_actions], menu_text
            )
            if index == len(menu_actions):
                if normal_actions:
                    action_index = actions.index(normal_actions[0])

                    return (normal_actions[0][0], action_index)
                else:
                    return (None, None)

            action_index = actions.index(menu_actions[index])

            return (menu_actions[index][0], action_index)

        elif normal_actions:
            # Otherwise place before first action
            return (normal_actions[0][0], 0)
        else:
            return (None, None)

    def action_insert_target(self, actions, action_text):
        """Determine the insertion point for an action within a list of
        actions.

        Args:
            actions (list): List of actions, where each item is a tuple
                            containing an action and a boolean indicating
                            whether it's a menu.

            action_text (str): The text of the action to insert.

        Returns:
            tuple: A tuple containing the action to insert before and its
                            index.
        """
        normal_actions = []
        for action, is_menu in actions:
            if not is_menu:
                normal_actions.append((action, is_menu))

        if normal_actions:
            # Sort actions alphabetically
            index = bisect.bisect_left(
                [x[0].text() for x in normal_actions], action_text
            )
            if index == len(normal_actions):
                return (None, None)
            else:
                action_index = actions.index(normal_actions[index])

                return (normal_actions[index][0], action_index)

        else:
            return (None, None)

    def build_menu(self, menu_data, family_name):
        """Build a hierarchical menu from the given menu data.

        Args:
            menu_data (dict): The hierarchical menu data.
            family_name (str): The name of the menu.

        Returns:
            QMenu: The constructed menu.
        """
        menu = QMenu(family_name)
        # Can't rely on widget children since the menu is built recursively
        prev_items = []
        for key, value in menu_data.items():
            if value is None:
                action = QAction(key, menu)
                target_action, insert_index = self.action_insert_target(
                    prev_items, key
                )
                if target_action:
                    menu.insertAction(target_action, action)
                    prev_items.insert(insert_index, (action, False))
                else:
                    menu.addAction(action)
                    prev_items.append((action, False))
            else:
                # Since value is not None then this is a submenu
                # Need to place submenu at beginning of current submenu
                submenu = self.build_menu(value, key)
                target_submenu, insert_index = self.menu_insertion_target(
                    prev_items, key
                )
                if target_submenu:
                    menu.insertMenu(target_submenu, submenu)
                    prev_items.insert(
                        insert_index, (submenu.menuAction(), True)
                    )
                else:
                    menu.addMenu(submenu)
                    prev_items.append((submenu.menuAction(), True))

        return menu


def is_valid_asset(track_item):
    """Check if the given asset name is valid for the current project.

    Args:
        asset_name (str): The name of the asset to validate.

    Returns:
        dict: The asset document if found, otherwise an empty dictionary.
    """
    # Track item may not have ran through callback to is valid attr
    if "valid_avalon_track_item" in track_item.__dir__():

        return track_item.valid_avalon_track_item

    project_name = get_current_project_name()
    asset_doc = get_asset_by_name(project_name, track_item.name())
    if asset_doc:
        return True
    else:
        return False


def get_track_item_envs(asset_name):
    """
    Get the asset environment from an asset stored in the Avalon database.

    Args:
        asset_name (str): The name of the asset.

    Returns:
        dict: The asset environment if found, otherwise an empty dictionary.
    """
    project_name = get_current_project_name()
    project_doc = get_project(project_name)
    asset_doc = get_asset_by_name(project_name, asset_name)
    if not asset_doc:
        return {}

    hierarchy_env = get_hierarchy_env(project_doc, asset_doc)

    return hierarchy_env


# The Custom Spreadsheet Columns
class CustomSpreadsheetColumns(QObject):
    """A class defining custom columns for Hiero's spreadsheet view. This has a
    similar, but slightly simplified, interface to the QAbstractItemModel and
    QItemDelegate classes.
    """

    currentView = hiero.ui.activeView()

    # This is the list of Columns available
    # readonly implies QLabel
    # dropdown implies QCombo
    # text implies QTextEdit
    column_list = [
        {"name": "Tags", "cellType": "readonly"},
        {"name": "Colorspace", "cellType": "dropdown"},
        {"name": "Notes", "cellType": "readonly"},
        {"name": "FileType", "cellType": "readonly"},
        {"name": "WidthxHeight", "cellType": "readonly"},
        {"name": "Pixel Aspect", "cellType": "readonly"},
        {"name": "Episode", "cellType": "readonly"},
        {"name": "Sequence", "cellType": "readonly"},
        {"name": "Shot", "cellType": "readonly"},
        {"name": "head_handles", "cellType": "text"},
        {"name": "cut_in", "cellType": "text"},
        {"name": "cut_out", "cellType": "text"},
        {"name": "tail_handles", "cellType": "text"},
        {
            "name": "valid_entity",
            "cellType": "readonly",
        },
        {"name": "op_frame_start", "cellType": "text", "size": QSize(40, 20)},
        {"name": "op_family", "cellType": "dropdown", "size": QSize(10, 10)},
        {"name": "op_handle_start", "cellType": "text", "size": QSize(10, 10)},
        {"name": "op_handle_end", "cellType": "text", "size": QSize(10, 10)},
        {"name": "op_subset", "cellType": "readonly"},
        {"name": "op_use_nuke", "cellType": "checkbox"},
    ]

    def numColumns(self):
        """Return the number of custom columns in the spreadsheet view"""

        return len(self.column_list)

    def columnName(self, column):
        """Return the name of a custom column"""

        return self.column_list[column]["name"]

    def get_tags_string(self, item):
        """Convenience method for returning all the Notes in a Tag as a
        string
        """
        tag_names = []
        tags = item.tags()
        for tag in tags:
            tag_names += [tag.name()]
        tag_name_string = ",".join(tag_names)

        return tag_name_string

    def get_notes(self, item):
        """Convenience method for returning all the Notes in a Tag as a
        string
        """
        notes = []
        for tag in item.tags():
            # Skip OpenPype notes
            if "openpypeData" in tag.name():
                continue
            note = tag.note()
            if note:
                notes.append(note)

        return ", ".join(notes)

    def getData(self, row, column, item):
        """Return the data in a cell"""
        current_column = self.column_list[column]
        if current_column["name"] == "Tags":
            return self.get_tags_string(item)

        elif current_column["name"] == "Colorspace":

            return item.sourceMediaColourTransform()

        elif current_column["name"] == "Notes":

            return self.get_notes(item)

        elif current_column["name"] == "FileType":
            fileType = "--"
            item_metadata = item.source().mediaSource().metadata()
            if item_metadata.hasKey("foundry.source.type"):
                fileType = item_metadata.value("foundry.source.type")
            elif item_metadata.hasKey("media.input.filereader"):
                fileType = item_metadata.value("media.input.filereader")
            return fileType

        elif current_column["name"] == "WidthxHeight":
            width = str(item.source().format().width())
            height = str(item.source().format().height())
            return f"{width}x{height}"

        elif current_column["name"] == "Episode":
            track_item_episode = get_track_item_envs(item.name()).get("EPISODE")

            return track_item_episode or "--"

        elif current_column["name"] == "Sequence":
            track_item_sequence = get_track_item_envs(item.name()).get("SEQ")

            return track_item_sequence or "--"

        elif current_column["name"] == "Shot":
            track_item_shot = get_track_item_envs(item.name()).get("SHOT")

            return track_item_shot or "--"

        elif current_column["name"] == "Pixel Aspect":

            return str(item.source().format().pixelAspect())

        elif current_column["name"] == "Artist":
            if item.artist():
                name = item.artist()["artistName"]
                return name
            else:
                return "--"

        elif current_column["name"] == "Department":
            if item.artist():
                dep = item.artist()["artistDepartment"]
                return dep
            else:
                return "--"

        elif current_column["name"] in [
            "cut_in",
            "cut_out",
            "head_handles",
            "tail_handles",
        ]:
            tag_key = current_column["name"]
            current_tag_text = item.cut_info().get(tag_key, "--")

            return current_tag_text

        elif "op_" in current_column["name"]:
            instance_key = current_column["name"]
            current_tag_text = item.openpype_instance().get(
                f"{instance_key.split('op_')[-1]}", "--"
            )

            return current_tag_text

        return ""

    def setData(self, row, column, item, data):
        """Set the data in a cell - unused in this example"""

        return None

    def getTooltip(self, row, column, item):
        """Return the tooltip for a cell"""
        current_column = self.column_list[column]

        if current_column["name"] == "Tags":
            return str([item.name() for item in item.tags()])

        elif current_column["name"] == "Notes":
            return str(self.get_notes(item))

        elif current_column["name"] == "Episode":
            return (
                "Episode name of current track item if valid otherwise --"
            )

        elif current_column["name"] == "Sequence":
            return (
                "Sequence name of current track item if valid otherwise --"
            )

        elif current_column["name"] == "Shot":
            return (
                "Shot name of current track item if valid otherwise --"
            )

        elif current_column["name"] == "cut_in":
            return (
                "Shot 'cut in' frame. This is meant to be ground truth and can"
                " be used to sync to SG."
            )

        elif current_column["name"] == "cut_out":
            return (
                "Shot 'cut out' frame. This is meant to be ground truth and can"
                " be used to sync to SG."
            )

        elif current_column["name"] == "head_handles":
            return (
                "Shot 'head handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG."
            )

        elif current_column["name"] == "tail_handles":
            return (
                "Shot 'tail handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG."
            )

        elif current_column["name"] == "valid_entity":
            return (
                "Whether this track items name is found as a valid"
                " entity in Avalon DB."
            )

        elif current_column["name"] == "op_frame_start":
            return "Ingest first frame."

        elif current_column["name"] == "op_family":
            return "Ingest family."


        elif current_column["name"] == "op_handle_start":
            return "Ingest head handle duration."


        elif current_column["name"] == "op_handle_end":
            return "Ingest tail handle duration."


        elif current_column["name"] == "op_subset":
            return (
                "Subset is the ingest descriptor\nExample: "
                "{trackItemName}_{subset}_{version}"
            )

        elif current_column["name"] == "op_use_nuke":
            return (
                "Ingest can use two different methods depending on media type "
                "Nuke or OIIO. If you need to force a Nuke ingest toggle "
                "use_nuke to True"
            )

        return ""

    def getFont(self, row, column, item):
        """Return the font for a cell"""

        return None

    def getBackground(self, row, column, item):
        """Return the background color for a cell"""
        if not item.source().mediaSource().isMediaPresent():
            return QColor(80, 20, 20)

        column_name = self.column_list[column]["name"]
        if column_name.startswith("op_") or column_name == "valid_entity":
            if row % 2 == 0:
                # For reference default even row is 61, 61, 61
                return QColor(61, 61, 66)
            else:
                # For reference default even row is 53, 53, 53
                return QColor(53, 53, 57)

        return None

    def getForeground(self, row, column, item):
        """Return the text color for a cell"""
        if self.column_list[column]["name"] in [
            "op_family",
            "op_frame_start",
            "op_handle_end",
            "op_handle_start",
            "op_use_nuke",
            "op_subset",
        ]:

            if not is_valid_asset(item):
                return QColor(255, 60, 30)

        return None

    def getIcon(self, row, column, item):
        """Return the icon for a cell"""
        current_column = self.column_list[column]
        if current_column["name"] == "Colorspace":
            return QIcon("icons:LUT.png")

        elif current_column["name"] == "valid_entity":
            if is_valid_asset(item):
                icon_name = "icons:status/TagFinal.png"
            else:
                icon_name = "icons:status/TagOmitted.png"

            return QIcon(icon_name)

        return None

    def getSizeHint(self, row, column, item):
        """Return the size hint for a cell"""

        return self.column_list[column].get("size", None)

    def paintCell(self, row, column, item, painter, option):
        """Paint a custom cell. Return True if the cell was painted, or False
        to continue with the default cell painting.
        """
        current_column = self.column_list[column]
        if current_column["name"] == "Tags":
            if option.state & QStyle.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())
            iconSize = 20
            rectangle = QRect(
                option.rect.x(),
                option.rect.y() + (option.rect.height() - iconSize) / 2,
                iconSize,
                iconSize,
            )
            tags = item.tags()
            if len(tags) > 0:
                painter.save()
                painter.setClipRect(option.rect)
                for tag in item.tags():
                    tag_metadata = tag.metadata()
                    if not (
                        tag_metadata.hasKey("tag.status")
                        or tag_metadata.hasKey("tag.artistID")
                    ):
                        QIcon(tag.icon()).paint(
                            painter, rectangle, Qt.AlignLeft
                        )
                        rectangle.translate(rectangle.width() + 2, 0)
                painter.restore()
                return True

        return False

    def createEditor(self, row, column, item, view):
        """Create an editing widget for a custom cell"""
        self.currentView = view
        current_column = self.column_list[column]

        if current_column["cellType"] == "readonly":
            # readonly is done by removing visibility and useability of the
            # returned widget to the widget viewer
            edit_widget = QLabel()
            edit_widget.setEnabled(False)
            edit_widget.setVisible(False)

            return edit_widget

        elif current_column["name"] == "Colorspace":
            ocio_config = get_active_ocio_config()
            edit_widget = Colorspace_Widget(ocio_config)
            edit_widget.root_menu.triggered.connect(self.colorspace_changed)

            return edit_widget

        elif current_column["name"] in [
            "cut_in",
            "cut_out",
            "head_handles",
            "tail_handles",
        ]:
            tag_key = current_column["name"]
            current_text = item.cut_info().get(tag_key)
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(tag_key)
            edit_widget.returnPressed.connect(self.cut_info_changed)

            return edit_widget

        elif current_column["name"] == "op_family":
            if not is_valid_asset(item):
                QMessageBox.warning(
                    hiero.ui.mainWindow(),
                    "Critical",
                    "Can't assign data to invalid entity",
                )

                readonly_widget = QLabel()
                readonly_widget.setEnabled(False)
                readonly_widget.setVisible(False)
                return readonly_widget

            instance_tag = item.openpype_instance_tag()
            if not instance_tag:
                combo_text = "--"
            else:
                combo_text = instance_tag.metadata().value("family")

            instance_key = current_column["name"].split("op_")[-1]
            combo_widget = QComboBox()
            combo_widget.setObjectName(instance_key)
            # Since trigger is on index change. Need to make sure valid options
            # will also be a change of index
            combo_widget.addItem("--")
            combo_widget.addItem("plate")
            combo_widget.addItem("reference")
            combo_widget.setCurrentText(combo_text)
            combo_widget.currentIndexChanged.connect(
                self.openpype_instance_changed
            )

            return combo_widget

        elif current_column["name"] == "op_use_nuke":
            if not is_valid_asset(item):
                QMessageBox.warning(
                    hiero.ui.mainWindow(),
                    "Critical",
                    "Can't assign data to invalid entity",
                )

                readonly_widget = QLabel()
                readonly_widget.setEnabled(False)
                readonly_widget.setVisible(False)

                return readonly_widget

            instance_tag = item.openpype_instance_tag()
            if not instance_tag:
                check_state = "--"
            else:
                # For Openpype tags already made they won't have use nuke
                # instance data
                try:
                    check_state = instance_tag.metadata().value("use_nuke")
                except RuntimeError:
                    check_state = "--"

            instance_key = current_column["name"].split("op_")[-1]
            combo_widget = QComboBox()
            combo_widget.setObjectName(instance_key)
            # Since trigger is on index change. Need to make sure valid options
            # will also be a change of index
            combo_widget.addItem("--")
            combo_widget.addItem("True")
            combo_widget.addItem("False")
            combo_widget.setCurrentText(check_state)
            combo_widget.currentIndexChanged.connect(
                self.openpype_instance_changed
            )

            return combo_widget

        elif current_column["name"] in [
            "op_frame_start",
            "op_handle_end",
            "op_handle_start",
        ]:
            if not is_valid_asset(item):
                QMessageBox.warning(
                    hiero.ui.mainWindow(),
                    "Critical",
                    "Can't assign data to invalid entity",
                )

                readonly_widget = QLabel()
                readonly_widget.setEnabled(False)
                readonly_widget.setVisible(False)

                return readonly_widget

            instance_key = current_column["name"].split("op_")[-1]
            current_text = item.cut_info().get(f"tag.{instance_key}")
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(instance_key)
            edit_widget.returnPressed.connect(self.openpype_instance_changed)

            return edit_widget

        return None

    def setModelData(self, row, column, item, editor):
        return False

    def dropMimeData(self, row, column, item, data, items):
        """Handle a drag and drop operation - adds a Dragged Tag to the shot"""
        for drop_item in drop_items:
            if isinstance(drop_item, hiero.core.Tag):
                item.addTag(drop_item)

        return None

    def colorspace_changed(self, action):
        """This method is called when Colorspace widget changes index."""
        colorspace = action.text()
        selection = self.currentView.selection()
        project = selection[0].project()
        with project.beginUndo("Set Colorspace"):
            items = [
                item
                for item in selection
                if (item.mediaType() == hiero.core.TrackItem.MediaType.kVideo)
            ]
            for track_item in items:
                track_item.setSourceMediaColourTransform(colorspace)

    def cut_info_changed(self):
        sender = self.sender()
        key = sender.objectName()
        value = sender.text().strip()

        # Only pass on edit if user unintentionally erased value from column
        if value not in ["--", ""] and not value.isdigit():
            return
        else:
            # Remove preceding zeros
            value = value if value == "0" else value.lstrip("0")

        view = hiero.ui.activeView()
        selection = view.selection()
        project = selection[0].project()
        with project.beginUndo("Set Cut Info"):
            if value != "--":
                for track_item in selection:
                    track_item.set_cut_info_tag(key, value)

            # If value is -- this is used as an easy to remove Cut Info tag
            else:
                for track_item in selection:
                    cut_info_tag = track_item.cut_info_tag()
                    if cut_info_tag:
                        log.info(f"{track_item.name()}: Removing 'Cut Info' tag")
                        track_item.removeTag(cut_info_tag)

    def openpype_instance_changed(self):
        sender = self.sender()
        key = sender.objectName()
        if isinstance(sender, QComboBox):
            value = sender.currentText()
        else:
            value = sender.text()

        view = hiero.ui.activeView()
        selection = view.selection()
        project = selection[0].project()
        with project.beginUndo("Set Openpype Instance"):
            # If value is -- this is used as an easy to remove openpype tag
            if value.strip() == "--":
                for track_item in selection:
                    openpype_instance_tag = track_item.openpype_instance_tag()
                    if openpype_instance_tag:
                        log.info(f"{track_item.name()}: Removing 'Cut Info' tag")
                        track_item.removeTag(openpype_instance_tag)
            else:
                for track_item in selection:
                    track_item.set_openpype_instance(key, value)

def _set_cut_info_tag(self, key, value):
    """Empty value is allowed incase editor wants to create a cut tag with
    default values
    """
    # Cut tag can be set from a variety of columns
    # Need to logic for each case
    cut_tag = self.cut_info_tag()

    if not cut_tag:
        # get default handles
        cut_tag = hiero.core.Tag("Cut Info")
        cut_tag.setIcon("icons:TagKeylight.png")

        frame_start, handle_start, handle_end = openpype_setting_defaults()

        frame_offset = frame_start + handle_start
        if value:
            if key == "cut_in":
                frame_offset = int(value)
            elif key == "cut_out":
                frame_offset = int(value) - self.duration() + 1

        cut_data = {}
        cut_data["cut_in"] = frame_offset
        cut_data["cut_out"] = frame_offset + self.duration() - 1
        cut_data["head_handles"] = handle_start
        cut_data["tail_handles"] = handle_end

        if value:
            cut_data.update({key: value})

        for key, value in cut_data.items():
            if not isinstance(value, str):
                value = str(value)
            cut_tag.metadata().setValue(f"tag.{key}", value)

        self.sequence().editFinished()
        self.addTag(cut_tag)

    if value:
        cut_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()


def _cut_info(self):
    cut_info_tag = self.cut_info_tag()
    cut_info = {}

    if cut_info_tag:
        cut_info_data = cut_info_tag.metadata().dict()
        for key, value in cut_info_data.items():
            cut_info[key.split("tag.")[-1]] = value

    return cut_info


def _cut_info_tag(self):
    tags = self.tags()
    for tag in tags:
        if tag.name() == "Cut Info":
            return tag

    return None


def openpype_setting_defaults():
    project_name = get_current_project_name()
    project_settings = get_project_settings(project_name)

    create_settings = project_settings["hiero"]["create"]
    create_shot_clip_defaults = create_settings["CreateShotClip"]
    frame_start_default = create_shot_clip_defaults["workfileFrameStart"]
    handle_start_default = create_shot_clip_defaults["handleStart"]
    handle_end_default = create_shot_clip_defaults["handleEnd"]

    return (frame_start_default, handle_start_default, handle_end_default)


def get_entity_hierarchy(asset_name):
    """Retrieve entity links for the given asset.

    This function creates a dictionary of linked entities for the specified
    asset. The linked entities may include:
    - episode
    - sequence
    - shot
    - folder

    Args:
        asset_name (str): The name of the asset.

    Returns:
        dict: A dictionary containing linked entities, including episode,
                sequence, shot, and folder information.
    """
    project_name = get_current_project_name()
    project_doc = get_project(project_name)
    asset_doc = get_asset_by_name(project_name, asset_name)

    hierarchy_env = get_hierarchy_env(project_doc, asset_doc)
    if asset_doc:
        parents = asset_doc["data"]["parents"]

        # Breakdown the first folders by which ones are not epi/seq/shot
        sub_directories = []
        for parent in parents:
            if parent in hierarchy_env.values():
                break
            else:
                sub_directories.append(parent)
    hierarchy_env = get_hierarchy_env(project_doc, asset_doc)

    asset_entities = {}
    episode = hierarchy_env.get("EPISODE")
    if episode:
        asset_entities["episode"] = episode

    sequence = hierarchy_env.get("SEQ")
    if sequence:
        asset_entities["sequence"] = sequence

    asset = hierarchy_env.get("SHOT")
    if asset:
        asset_entities["shot"] = asset

    if hierarchy_env.get("ASSET_TYPE"):
        folder = "asset"
    else:
        folder = "shots"
    asset_entities["folder"] = folder

    return asset_entities


def get_hierarchy_data(asset_name, track_name):
    hierarchy_data = get_entity_hierarchy(asset_name)
    hierarchy_data["track"] = track_name

    return hierarchy_data


def get_hierarchy_path(asset_doc):
    """Asset path is always the joining of the asset parents"""
    hierarchy_path = os.sep.join(asset_doc["data"]["parents"])

    return hierarchy_path


def get_hierarchy_parents(hierarchy_data):
    parents = []
    parents_types = ["folder", "episode", "sequence"]
    for key, value in hierarchy_data.items():
        if key in parents_types:
            entity = {"entity_type": key, "entity_name": value}
            parents.append(entity)

    return parents


def _openpype_instance_tag(self):
    tags = self.tags()
    for tag in tags:
        if OPENPYPE_TAG_NAME in tag.name():
            return tag

    return None


def _set_openpype_instance(self, key, value):
    """
    Only one key of the tag can be modified at a time for items that already
    have a tag.
    """
    value = value if value == "0" else value.strip().lstrip("0")

    # Validate key value
    # No need to validate family as it's a prefilled combobox
    if key in ["frame_start", "handle_start", "handle_end"]:
        # Skip validation if user simply wants to create default tag
        if value:
            if not value.isdigit():
                log.info(f"{self.name()}: {key} must be a valid number")
                return

    convert_keys = {
        "frame_start": "workfileFrameStart",
        "handle_start": "handleStart",
        "handle_end": "handleEnd",
    }
    # Convert data from column names into OP instance names
    key = convert_keys.get(key, key)

    instance_tag = self.openpype_instance_tag()
    track_item_name = self.name()
    track_name = self.parentTrack().name()
    # Check if asset has valid name
    # if not don't create instance
    if not is_valid_asset(self):
        if instance_tag:
            log.info(f"{self.name()}: Track item name no longer valid. "
                  "Removing Openpype tag")
            self.removeTag(instance_tag)
        else:
            log.info(f"{self.name()}: Track item name not found in DB!")

        return
    else:
        project_name = get_current_project_name()
        asset_doc = get_asset_by_name(project_name, self.name())

    instance_data = {}
    if not instance_tag:
        # First fill default instance if no tag found and then update with
        # data parameter
        if "ref" in track_name:
            family = "reference"
        else:
            family = "plate"

        hierarchy_data = get_hierarchy_data(track_item_name, track_name)
        hierarchy_path = get_hierarchy_path(asset_doc)
        hierarchy_parents = get_hierarchy_parents(hierarchy_data)
        frame_start, handle_start, handle_end = openpype_setting_defaults()

        instance_data["hierarchyData"] = hierarchy_data
        instance_data["hierarchy"] = hierarchy_path
        instance_data["parents"] = hierarchy_parents
        instance_data["asset"] = track_item_name
        instance_data["subset"] = track_name
        instance_data["family"] = family
        instance_data["workfileFrameStart"] = frame_start
        instance_data["handleStart"] = handle_start \
            if family == "plate" else "0"
        instance_data["handleEnd"] = handle_end \
            if family == "plate" else "0"

        # Constants
        instance_data["audio"] = "False"
        instance_data["heroTrack"] = "True"
        instance_data["families"] = "['clip']"
        instance_data["id"] = "pyblish.avalon.instance"
        instance_data["publish"] = "True"
        instance_data["reviewTrack"] = "None"
        instance_data["sourceResolution"] = "False"
        instance_data["variant"] = "Main"
        instance_data["use_nuke"] = "False"

    if value:
        instance_data.update({key: value})

    set_trackitem_openpype_tag(self, instance_data)

    self.sequence().editFinished()


def _openpype_instance(self):
    instance_tag = self.openpype_instance_tag()
    instance_data = {}
    if instance_tag:
        tag_data = instance_tag.metadata().dict()
        # Convert data from column names into OP instance names
        convert_keys = {
            "tag.workfileFrameStart": "frame_start",
            "tag.handleStart": "handle_start",
            "tag.handleEnd": "handle_end",
        }
        for key, value in tag_data.items():
            if key in convert_keys:
                instance_data[convert_keys[key]] = value
            else:
                instance_data[key.split("tag.")[-1]] = value

    return instance_data


def _update_op_instance_asset(event):
    # Always iter through all items since the user may never reselected the
    active_sequence = hiero.ui.activeSequence()
    track_items = []
    if active_sequence:
        for video_track in active_sequence.videoTracks():
            for item in video_track.items():
                if isinstance(item, hiero.core.TrackItem):
                    track_items.append(item)

    for track_item in track_items:
        instance_tag = track_item.openpype_instance_tag()
        if not instance_tag:
            continue

        track_item_name = track_item.name()
        track_name = track_item.parentTrack().name()
        project_name = get_current_project_name()
        asset_doc = get_asset_by_name(project_name, track_item_name)
        if not asset_doc:
            track_item.removeTag(instance_tag)
            continue

        hierarchy_data = get_hierarchy_data(track_item_name, track_name)
        hierarchy_path = get_hierarchy_path(asset_doc)
        hierarchy_parents = get_hierarchy_parents(hierarchy_data)

        instance_data = {}
        instance_data["hierarchyData"] = hierarchy_data
        instance_data["hierarchy"] = hierarchy_path
        instance_data["parents"] = hierarchy_parents
        instance_data["asset"] = track_item_name
        instance_data["subset"] = track_name
        update = False
        for key, value in instance_data.items():
            current_value = instance_tag.metadata().value(f"tag.{key}")
            # Need to compare objects in true form
            if (
                f"{current_value[0]}{current_value[-1]}" in ["{}", "[]", "()"]
                or current_value == "None"
            ):
                current_value = ast.literal_eval(current_value)

            if current_value != value:
                # Change value into string form if not already a string
                if not isinstance(value, str):
                    value = value.__repr__()

                instance_tag.metadata().setValue(f"tag.{key}", value)
                update = True

        if update:
            log.info(f"{track_item_name}: OP Instance updated - data modified")


def validate_avalon_track_item(event):
    """
    Event driven function that iters through all items as SequenceEdited
    doesn't have self.sender and assign a validation attribute to each track
    item
    """
    sequence = event.sequence
    track_items = []
    if sequence:
        for video_track in sequence.videoTracks():
            for item in video_track.items():
                if isinstance(item, hiero.core.TrackItem):
                    track_items.append(item)

    project_name = get_current_project_name()
    for track_item in track_items:
        asset_doc = get_asset_by_name(project_name, track_item.name())
        if asset_doc:
            track_item.valid_avalon_track_item = True
        else:
            track_item.valid_avalon_track_item = False


# Inject cut tag getter and setter methods into hiero.core.TrackItem
hiero.core.TrackItem.set_cut_info_tag = _set_cut_info_tag
hiero.core.TrackItem.cut_info_tag = _cut_info_tag
hiero.core.TrackItem.cut_info = _cut_info


# Add openpype_instance methods to track item object
hiero.core.TrackItem.set_openpype_instance = _set_openpype_instance
hiero.core.TrackItem.openpype_instance_tag = _openpype_instance_tag
hiero.core.TrackItem.openpype_instance = _openpype_instance


# Register openpype instance update event
hiero.core.events.registerInterest(
    "kSequenceEdited", _update_op_instance_asset
)

# Register validation query to avalon
hiero.core.events.registerInterest("kSequenceEdited", validate_avalon_track_item)

# Register our custom columns
hiero.ui.customColumn = CustomSpreadsheetColumns()
