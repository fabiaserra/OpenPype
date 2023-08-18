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
from openpype.pipeline.context_tools import (
    get_current_project_name,
    get_hierarchy_env,
)
from openpype.hosts.hiero.api.lib import (
    set_trackitem_openpype_tag,
    get_trackitem_openpype_tag,
)
from openpype.settings import get_project_settings


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

    # If not OCIO found in envion then check project OCIO
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
        menu_actions = []
        normal_actions = []
        for action, is_menu in actions:
            if is_menu:
                menu_actions.append((action, is_menu))
            else:
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


def is_valid_asset(asset_name):
    """Check if the given asset name is valid for the current project.

    Args:
        asset_name (str): The name of the asset to validate.

    Returns:
        dict: The asset document if found, otherwise an empty dictionary.
    """
    project_name = get_current_project_name()
    asset_doc = get_asset_by_name(project_name, asset_name)
    if asset_doc:
        return asset_doc
    else:
        return {}


def get_asset_envs(asset_name):
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

    entity_env = get_hierarchy_env(project_doc, asset_doc)

    return entity_env


def get_track_item_shot(track_item_name):
    """Validate if shot exists in DB and if so return shot name"""
    asset_envs = get_asset_envs(track_item_name)
    if asset_envs is None:
        return None
    else:
        return asset_envs.get("SHOT")


def get_track_item_episode(track_item_name):
    """Validate if shot exists in DB and if so return episode name"""
    asset_envs = get_asset_envs(track_item_name)
    if asset_envs is None:
        return None
    else:
        return asset_envs.get("EPISODE")


def get_track_item_sequence(track_item_name):
    """
    Validate if shot exists in DB and if so return sequence name
    """
    asset_envs = get_asset_envs(track_item_name)
    if asset_envs is None:
        return None
    else:
        return asset_envs.get("SEQ")


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
        {"name": "cut_in", "cellType": "text"},
        {"name": "head_handles", "cellType": "text"},
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

    def getTagsString(self, item):
        """Convenience method for returning all the Notes in a Tag as a
        string
        """
        tag_names = []
        tags = item.tags()
        for tag in tags:
            tag_names += [tag.name()]
        tag_name_string = ",".join(tag_names)

        return tag_name_string

    def getNotes(self, item):
        """Convenience method for returning all the Notes in a Tag as a
        string
        """
        notes = ""
        tags = item.tags()
        for tag in tags:
            # Remove OpenPype Note from note field
            if not "openpypeData" in tag.name():
                note = tag.note()
                if len(note) > 0:
                    notes += tag.note() + ", "

        return notes[:-2]

    def getData(self, row, column, item):
        """Return the data in a cell"""
        current_column = self.column_list[column]
        if current_column["name"] == "Tags":
            return self.getTagsString(item)

        elif current_column["name"] == "Colorspace":
            column_transform = item.sourceMediaColourTransform()
            try:
                column_transform = item.sourceMediaColourTransform()
            except:
                column_transform = "--"
            return column_transform

        elif current_column["name"] == "Notes":
            try:
                note = self.getNotes(item)
            except:
                note = ""
            return note

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
            return get_track_item_episode(item.name()) or "--"

        elif current_column["name"] == "Sequence":
            return get_track_item_sequence(item.name()) or "--"

        elif current_column["name"] == "Shot":
            return get_track_item_shot(item.name()) or "--"

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
            "head_handles",
            "tail_handles",
        ]:
            tag_key = current_column["name"]
            current_tag_text = item.cut_tag().get(f"tag.{tag_key}", "--")

            return current_tag_text

        elif current_column["name"] in [
            "op_frame_start",
            "op_family",
            "op_handle_end",
            "op_handle_start",
            "op_use_nuke",
            "op_subset",
        ]:
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

        if current_column["name"] == "Notes":
            return str(self.getNotes(item))

        if current_column["name"] == "Episode":
            tooltip = (
                "Episode name of current track item if valid otherwise --"
            )
            return tooltip

        if current_column["name"] == "Sequence":
            tooltip = (
                "Sequence name of current track item if valid otherwise --"
            )
            return tooltip

        if current_column["name"] == "Shot":
            tooltip = (
                "Shot name of current track item if valid otherwise --"
            )
            return tooltip

        if current_column["name"] == "cut_in":
            tooltip = (
                "Shot 'cut in' frame. This is meant to be ground truth and can"
                " be used to sync to SG"
            )
            return tooltip

        if current_column["name"] == "head_handles":
            tooltip = (
                "Shot 'head handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG"
            )
            return tooltip

        if current_column["name"] == "tail_handles":
            tooltip = (
                "Shot 'tail handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG"
            )
            return tooltip

        if current_column["name"] == "valid_entity":
            tooltip = (
                "Whether this track items name is found as a valid"
                " entity in Avalon DB"
            )
            return tooltip

        if current_column["name"] == "op_frame_start":
            tooltip = (
                "Ingest first frame"
            )
            return tooltip

        if current_column["name"] == "op_family":
            tooltip = "Ingest first frame"

            return tooltip

        if current_column["name"] == "op_handle_start":
            tooltip = "Ingest head handle duration"

            return tooltip

        if current_column["name"] == "op_handle_end":
            tooltip = "Ingest tail handle duration"

            return tooltip

        if current_column["name"] == "op_subset":
            tooltip = (
                "Subset is the ingest descriptor\nExample: "
                "{trackItemName}_{subset}_{version}"
            )
            return tooltip

        if current_column["name"] == "op_use_nuke":
            tooltip = (
                "Ingest can use two different methods depending on media type "
                "Nuke or OIIO. If you need to force a Nuke ingest toggle "
                "use_nuke to True"
            )
            return tooltip

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
            if not is_valid_asset(item.name()):
                return QColor(255, 60, 30)

        return None

    def getIcon(self, row, column, item):
        """Return the icon for a cell"""
        current_column = self.column_list[column]
        if current_column["name"] == "Colorspace":
            return QIcon("icons:LUT.png")

        if current_column["name"] == "valid_entity":
            if is_valid_asset(item.name()):
                icon_name = "icons:status/TagFinal.png"
            else:
                icon_name = "icons:status/TagOmitted.png"

            return QIcon(icon_name)

        return None

    def getSizeHint(self, row, column, item):
        """Return the size hint for a cell"""
        current_columnSize = self.column_list[column].get("size", None)

        return current_columnSize

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
            edit_widget.root_menu.triggered.connect(self.colorspaceChanged)

            return edit_widget

        elif current_column["name"] in [
            "cut_in",
            "head_handles",
            "tail_handles",
        ]:
            tag_key = current_column["name"]
            current_text = item.cut_tag().get(f"tag.{tag_key}")
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(tag_key)
            edit_widget.returnPressed.connect(self.cut_info_changed)

            return edit_widget

        elif current_column["name"] == "op_family":
            if not is_valid_asset(item.name()):
                QMessageBox.warning(
                    hiero.ui.mainWindow(),
                    "Critical",
                    "Can't assign data to invalid entity",
                )

                readonly_widget = QLabel()
                readonly_widget.setEnabled(False)
                readonly_widget.setVisible(False)
                return readonly_widget

            tags = item.tags()
            instance_tag = {}
            for tag in tags:
                if not "openpypeData_" in tag.name():
                    continue
                else:
                    instance_tag = tag
                    break

            if not instance_tag:
                if "ref" in item.parentTrack().name():
                    family = "reference"
                else:
                    family = "plate"
                combo_text = family
            else:
                combo_text = instance_tag.metadata().value("family")

            instance_key = current_column["name"].split("op_")[-1]
            combo_widget = QComboBox()
            combo_widget.setObjectName(instance_key)
            combo_widget.addItem("plate")
            combo_widget.addItem("reference")
            combo_widget.setCurrentText(combo_text)
            combo_widget.currentIndexChanged.connect(
                self.openpype_instance_changed
            )

            return combo_widget

        elif current_column["name"] == "op_use_nuke":
            if not is_valid_asset(item.name()):
                QMessageBox.warning(
                    hiero.ui.mainWindow(),
                    "Critical",
                    "Can't assign data to invalid entity",
                )

                readonly_widget = QLabel()
                readonly_widget.setEnabled(False)
                readonly_widget.setVisible(False)

                return readonly_widget

            tags = item.tags()
            instance_tag = {}
            for tag in tags:
                if not "openpypeData_" in tag.name():
                    continue
                else:
                    instance_tag = tag
                    break

            if not instance_tag:
                check_state = "False"
            else:
                # For Openpype tags already made they won't have use nuke
                # instance data
                try:
                    check_state = instance_tag.metadata().value("use_nuke")
                except RuntimeError:
                    check_state = False

            instance_key = current_column["name"].split("op_")[-1]
            combo_widget = QComboBox()
            combo_widget.setObjectName(instance_key)
            combo_widget.addItem("True")
            combo_widget.addItem("False")
            combo_widget.setCurrentText(
                True if check_state == "True" else "False"
            )
            combo_widget.currentIndexChanged.connect(
                self.openpype_instance_changed
            )

            return combo_widget

        elif current_column["name"] in [
            "op_frame_start",
            "op_handle_end",
            "op_handle_start",
        ]:
            if not is_valid_asset(item.name()):
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
            current_text = item.cut_tag().get(f"tag.{instance_key}")
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(instance_key)
            edit_widget.returnPressed.connect(self.openpype_instance_changed)

            return edit_widget

        return None

    def setModelData(self, row, column, item, editor):
        return False

    def dropMimeData(self, row, column, item, data, items):
        """Handle a drag and drop operation - adds a Dragged Tag to the shot"""
        for thing in items:
            if isinstance(thing, hiero.core.Tag):
                item.addTag(thing)

        return None

    def colorspaceChanged(self, action):
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
        if value != "--" and value:
            for track_item in selection:
                track_item.set_cut_tag(key, value)
        else:
            # Double check that the other cut info tag values don't exist
            cut_info_keys = ["cut_in", "head_handles", "tail_handles"]
            cut_info_keys.remove(key)
            for track_item in selection:
                track_item_tags = track_item.tags()
                for tag in track_item_tags:
                    if tag.name() == "Cut Info":
                        for cut_key in cut_info_keys:
                            if tag.metadata().hasKey(f"tag.{cut_key}"):
                                break
                        else:
                            track_item.removeTag(tag)
                            break

    def openpype_instance_changed(self):
        sender = self.sender()
        key = sender.objectName()
        if isinstance(sender, QComboBox):
            value = sender.currentText()
        else:
            value = sender.text()

        view = hiero.ui.activeView()
        selection = view.selection()
        if value.strip() == "":
            return
        else:
            for track_item in selection:
                track_item.set_openpype_instance(key, value)


def create_unique_tag(tag_name):
    """Create a unique Hiero Tag object with the specified tag name.

    A random number is added to the tag name to ensure that metadata is empty
    on creation. The added number is then removed after creation.

    Args:
        tag_name (str): The name of the tag.

    Returns:
        hiero.core.Tag: The created Hiero Tag object.
    """
    unique_tag_number = random.randint(99999999, 1000000000)
    tag = hiero.core.Tag(f"{tag_name} {unique_tag_number}")
    tag.metadata().setValue("tag.label", tag_name)
    tag.setName(tag_name)

    return tag


def _set_cut_tag(self, key, value):
    if not key:
        return

    # Cut tag can be set from a variety of columns
    # Need to logic for each case
    tags = self.tags()
    cut_tag = {}
    for tag in tags:
        if not tag.name() == "Cut Info":
            continue

        cut_tag = tag
        break

    if not cut_tag:
        cut_tag = create_unique_tag("Cut Info")
        cut_tag.metadata().setValue("tag.label", "Cut Info")
        cut_tag.setName("Cut Info")

        # Have yet to find icon for cut info
        cut_tag.setIcon("icons:TagKeylight.png")
        # Do i need this duplicate code?
        cut_tag.metadata().setValue(f"tag.{key}", value)
        self.sequence().editFinished()
        self.addTag(cut_tag)
        self.sequence().editFinished()

        return

    cut_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()

    return


def _cut_tag(self):
    tags = self.tags()
    cut_tag = {}
    for tag in tags:
        if not tag.name() == "Cut Info":
            continue

        cut_tag = tag.metadata().dict()
        break

    return cut_tag


def openpype_setting_defaults():
    project_name = get_current_project_name()
    project_settings = get_project_settings(project_name)

    create_settings = project_settings["hiero"]["create"]
    create_shot_clip_defaults = create_settings["CreateShotClip"]
    frame_start_default = create_shot_clip_defaults["workfileFrameStart"]
    handle_start_default = create_shot_clip_defaults["handleStart"]
    handle_end_default = create_shot_clip_defaults["handleEnd"]

    return (frame_start_default, handle_start_default, handle_end_default)


def get_entity_links(asset_name):
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

    entity_links = get_hierarchy_env(project_doc, asset_doc)
    if asset_doc:
        parents = asset_doc["data"]["parents"]

        # Breakdown the first folders by which ones are not epi/seq/shot
        sub_directories = []
        for parent in parents:
            if parent in entity_links.values():
                break
            else:
                sub_directories.append(parent)

    # Add standard entities to dict
    asset_entities = {}
    episode = entity_links.get("EPISODE")
    if episode:
        asset_entities["episode"] = episode

    sequence = entity_links.get("SEQ")
    if sequence:
        asset_entities["sequence"] = sequence

    asset = entity_links.get("SHOT")
    if asset:
        asset_entities["shot"] = asset

    # Add folder to dict
    asset_entities["folder"] = os.sep.join(sub_directories)

    return asset_entities


def get_hierarchy_data(asset_name, track_name):
    hierarchy_data = get_entity_links(asset_name)
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


def _set_openpype_instance(self, key, value):
    """
    Only one key of the tag can be modified at a time for items that already
    have a tag.
    """
    value = value if value == "0" else value.lstrip("0")

    # Validate key value
    # No need to validate family as it's a prefilled combobox
    if key in ["frame_start", "handle_start", "handle_end"]:
        if not value.isdigit():
            print(f"{key} must be a valid number")
            return

    convert_keys = {
        "frame_start": "workfileFrameStart",
        "handle_start": "handleStart",
        "handle_end": "handleEnd",
    }
    # Convert data from column names into OP instance names
    if key in convert_keys:
        key = convert_keys[key]

    # Cut tag can be set from a variety of columns
    # Need to logic for each case
    tags = self.tags()
    instance_tag = {}
    for tag in tags:
        if not "openpypeData_" in tag.name():
            continue

        instance_tag = tag
        break

    track_item_name = self.name()
    track_name = self.parentTrack().name()
    valid_asset = is_valid_asset(track_item_name)
    # Check if asset has valid name
    # if not don't create instance
    if not valid_asset:
        if instance_tag:
            print("Tag tack name no longer valid. Removing Openpype tag")
            self.removeTag(instance_tag)
        else:
            print("Track item name not found in DB!")

        return
    else:
        asset_doc = valid_asset

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
        instance_data["asset"] = get_track_item_shot(track_item_name)
        instance_data["subset"] = track_name
        instance_data["family"] = family
        instance_data["workfileFrameStart"] = frame_start
        instance_data["handleStart"] = handle_start
        instance_data["handleEnd"] = handle_end

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

    instance_data.update({key: value})

    set_trackitem_openpype_tag(self, instance_data)


def _openpype_instance(self):
    instance_tag = get_trackitem_openpype_tag(self)
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
        tags = track_item.tags()
        instance_tag = {}
        for tag in tags:
            if not "openpypeData_" in tag.name():
                continue

            instance_tag = tag
            break
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
        instance_data["asset"] = get_track_item_shot(track_item_name)
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
            print(f"{track_item_name}: OP Instance updated - data modified")


# Inject cut tag getter and setter methods into hiero.core.TrackItem
hiero.core.TrackItem.set_cut_tag = _set_cut_tag
hiero.core.TrackItem.cut_tag = _cut_tag


# Add openpype_instance methods to track item object
hiero.core.TrackItem.set_openpype_instance = _set_openpype_instance
hiero.core.TrackItem.openpype_instance = _openpype_instance


# Register openpype instance update event
hiero.core.events.registerInterest(
    "kSelectionChanged", _update_op_instance_asset
)
# Register our custom columns
hiero.ui.customColumn = CustomSpreadsheetColumns()
