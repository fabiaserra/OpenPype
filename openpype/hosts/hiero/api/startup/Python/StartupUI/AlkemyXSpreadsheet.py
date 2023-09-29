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

from openpype.client import (
    get_asset_by_name,
    get_project,
    get_last_version_by_subset_name,
)
from openpype.hosts.hiero.api.constants import OPENPYPE_TAG_NAME
from openpype.hosts.hiero.api.lib import set_trackitem_openpype_tag
from openpype.lib import Logger
from openpype.modules.shotgrid.lib import credentials
from openpype.pipeline.context_tools import (
    get_current_project_name,
    get_hierarchy_env,
)


SHOTGRID = credentials.get_shotgrid_session()

FORMATS = {
    fmt.name(): {
        "width": fmt.width(),
        "height": fmt.height(),
        "format": fmt.toString(),
        "pixelAspect": fmt.pixelAspect(),
    }
    for fmt in hiero.core.formats()
}

TAG_DATA_KEY_CONVERT = {
    OPENPYPE_TAG_NAME: {
        "tag.workfileFrameStart": "frame_start",
        "tag.handleStart": "handle_start",
        "tag.handleEnd": "handle_end",
    }
}

SG_TAG_ICONS = {
    "retime": "icons:TagKronos.png",
    "repo": "icons:ExitFullScreen.png",
    "split": "icons:TimelineToolSlide.png",
    "insert": "icons:SyncPush.png",
}

INGEST_EFFECTS = ["flip", "flop"]
SG_TAGS = ["retime", "repo", "insert", "split"]
EVEN_COLUMN_COLOR = QColor(61, 61, 66)
ODD_COLUMN_COLOR = QColor(53, 53, 57)
NO_OP_TRANSLATE = {43: None, 45: None, 42: None, 47: None}

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
        # Returning now. No need to search other places for config
        return PyOpenColorIO.Config.CreateFromFile(env_ocio_path)

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
                    break

    # Else statement is a catch for when the spreadsheet runs without sequence
    # loaded
    else:
        ocio_path = os.path.join(configs_path, "nuke-default/config.ocio")

    ocio_config = PyOpenColorIO.Config.CreateFromFile(ocio_path)

    return ocio_config


class CheckboxMenu(QMenu):
    mouse_in_view = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def accept(self, event):
        if self.can_close:
            event.accept()
        else:
            event.ignore()

    def enterEvent(self, event):
        self.mouse_in_view = True

    def leaveEvent(self, event):
        self.mouse_in_view = False

    def keyPressEvent(self, event):
        if event.key() in [Qt.Key_Return, Qt.Key_Enter]:
            self.can_close = True
            self.close()

    def mousePressEvent(self, event):
        if not self.mouse_in_view:
            self.can_close = True
            self.close()

        if event.button() == Qt.LeftButton:
            action = self.activeAction()
            if action:
                if action.isChecked():
                    action.setChecked(False)
                else:
                    action.setChecked(True)

                # Suppress the event to prevent the menu from closing
                event.accept()
                return

        super().mouseReleaseEvent(event)


class ColorspaceWidget(QMainWindow):
    def __init__(self, ocio_config, parent=None):
        super().__init__(parent)

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


class IngestResWidget(QComboBox):
    def __init__(self, item, current_format):
        super().__init__()

        default_working_resolution = self.get_default_working_resolution(item.name())
        if default_working_resolution:
            default_format_width, default_format_height = \
                default_working_resolution
        elif "x" in current_format:
            default_format_width, default_format_height = current_format.split(
                "x"
            )
        else:
            default_format = item.source().format()
            default_format_width = default_format.width()
            default_format_height = default_format.height()

        self.setEditable(True)
        validator = QRegExpValidator(r"^\d+[x]\d+$", self.lineEdit())
        self.setValidator(validator)
        self.lineEdit().setText("--")

        # Use base settings from current combobox defaults
        completer = self.completer()
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)
        self.addItem("--")

        # Move current resolution to the top
        proper_res = ""
        for res in sorted(FORMATS, key=lambda x: FORMATS[x]["width"]):
            width = FORMATS[res]["width"]
            height = FORMATS[res]["height"]
            if (
                width == default_format_width
                and height == default_format_height
            ):
                proper_res = res
            else:
                self.addItem("{0}x{1} - {2}".format(width, height, res))

        # Move current resolution to the top
        if proper_res:
            width = FORMATS[proper_res]["width"]
            height = FORMATS[proper_res]["height"]
            default_format_string = f"{width}x{height} - {proper_res}"
        else:
            default_format_string = (
                f"{default_format_width}x{default_format_height}"
            )

        self.insertItem(0, default_format_string)

        # Will need to add current format if found as tag on clip
        if not current_format:
            self.setCurrentIndex(1)
        else:
            self.setCurrentIndex(0)

        # Select all for easy editing
        self.lineEdit().selectAll()


    def get_default_working_resolution(self, asset_name):
        """Set resolution to project resolution."""
        # If Asset has working resolution pull from asset
        # If not pull from Project default working res
        project_name = get_current_project_name()
        asset_doc = get_asset_by_name(project_name, asset_name)

        if asset_doc:
            asset_data = asset_doc["data"]
            width = asset_data.get('resolutionWidth', "")
            height = asset_data.get('resolutionHeight', "")
            if width and height:

                return (width, height)

        else:
            filters = [
                [
                    "name",
                    "is",
                    project_name,
                ],
            ]
            fields = [
                "sg_project_resolution",
            ]
            sg_project = SHOTGRID.find_one("Project", filters, fields)
            if not sg_project:
                return None

            show_resolution = sg_project["sg_project_resolution"]
            if "x" in show_resolution:
                width, height = show_resolution.split("x")

                return (width, height)

        return None


class IngestEffectsWidget(QMainWindow):
    can_close = False
    effects_data = {}
    effect_actions = {}

    def __init__(self, tag_state):
        super().__init__()

        self.effects_button = QPushButton("Effects")

        # Menu must be stored on self. Button won't react properly without
        self.root_menu = CheckboxMenu("Main")

        # set default state to effect data that exists
        for effect_type in INGEST_EFFECTS:
            effect_action = QAction(effect_type)
            effect_action.setObjectName(effect_type)
            effect_type_state = (
                True
                if tag_state.get(effect_type, "False") == "True"
                else False
            )
            effect_action.setCheckable(True)
            effect_action.setChecked(effect_type_state)

            self.effect_actions[effect_type] = effect_action
            self.root_menu.addAction(effect_action)

        self.effects_button.setMenu(self.root_menu)
        self.setCentralWidget(self.effects_button)

    def set_effects_data(self):
        for key, widget in self.effect_actions.items():
            self.effects_data[key] = widget.isChecked()


class CurrentGradeDialog(QDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def leaveEvent(self, event):
        self.close()


# QT widget type doesn't matter. Only used for the show event
class CurrentGradeWidget(QLabel):
    def __init__(self, text):
        super().__init__()
        self.ingest_grade = text

    def showEvent(self, event):
        # On show pop out separate dialog widget
        dialog = CurrentGradeDialog()

        line_edit = QLineEdit()
        line_edit.setReadOnly(True)
        line_edit.setText(self.ingest_grade)
        line_edit.editingFinished.connect(dialog.close)
        line_edit.returnPressed.connect(dialog.close)
        line_edit.setFrame(False)

        layout_widget = QVBoxLayout()
        layout_widget.addWidget(line_edit)
        dialog.setLayout(layout_widget)
        dialog.move(self.mapToGlobal(self.rect().topLeft()))
        dialog.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)

        metrics = line_edit.fontMetrics()
        margin = line_edit.textMargins()
        content = line_edit.contentsMargins()
        width = (
            metrics.width(self.ingest_grade)
            + margin.left()
            + margin.right()
            + content.left()
            + content.right()
        )
        # 32 is the dialog window margin
        dialog.setFixedWidth(width + 32)
        dialog.exec()


class SGTagsWidget(QMainWindow):
    can_close = False
    tag_data = {}
    tag_actions = {}

    def __init__(self, tag_state):
        super().__init__()

        self.sg_tags_button = QPushButton("SG Tags")

        # Menu must be stored on self. Button won't react properly without
        self.root_menu = CheckboxMenu("Main")

        # set default state to tag data that exists
        for tag_type in SG_TAGS:
            tag_action = QAction(tag_type)
            tag_action.setObjectName(tag_type)
            tag_type_state = (
                True if tag_state.get(tag_type, "False") == "True" else False
            )
            tag_action.setCheckable(True)
            tag_action.setChecked(tag_type_state)

            self.tag_actions[tag_type] = tag_action
            self.root_menu.addAction(tag_action)

        self.sg_tags_button.setMenu(self.root_menu)
        self.setCentralWidget(self.sg_tags_button)

    def set_tag_data(self):
        for key, widget in self.tag_actions.items():
            self.tag_data[key] = widget.isChecked()


def is_valid_asset(track_item):
    """Check if the given asset name is valid for the current project.

    Args:
        asset_name (str): The name of the asset to validate.

    Returns:
        dict: The asset document if found, otherwise an empty dictionary.
    """
    # Track item may not have ran through callback to is valid attr
    if "hierarchy_env" in track_item.__dir__():
        return track_item.hierarchy_env

    project_name = get_current_project_name()
    asset_doc = get_asset_by_name(project_name, track_item.name())
    if asset_doc:
        return True
    else:
        return False


def get_track_item_env(track_item):
    """
    Get the asset environment from an asset stored in the Avalon database.

    Args:
        track_item (str): Track item.

    Returns:
        dict: The asset environment if found, otherwise an empty dictionary.
    """
    if "hierarchy_env" in track_item.__dir__():
        return track_item.hierarchy_env

    project_name = get_current_project_name()
    project_doc = get_project(project_name)
    asset_doc = get_asset_by_name(project_name, track_item.name())
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

    # Decorator function for widget callbacks
    def column_widget_callback(callback):
        def wrapper(self, *args, **kwargs):
            view = hiero.ui.activeView()
            selection = [
                item
                for item in view.selection()
                if isinstance(item.parent(), hiero.core.VideoTrack)
            ]
            project = selection[0].project()

            result = callback(self, selection, project, *args, **kwargs)

            sequence = hiero.ui.activeSequence()
            # There may not be an active sequence
            if sequence:
                # Force sequence update
                sequence.editFinished()

            return result

        return wrapper

    currentView = hiero.ui.activeView()

    # This is the list of Columns that will be added
    column_list = [
        {"name": "FileType", "cellType": "readonly"},
        {"name": "Tags", "cellType": "readonly"},
        {"name": "Colorspace", "cellType": "custom"},
        {"name": "Episode", "cellType": "readonly"},
        {"name": "Sequence", "cellType": "readonly"},
        {"name": "Shot", "cellType": "readonly"},
        {"name": "WidthxHeight", "cellType": "readonly"},
        {"name": "Pixel Aspect", "cellType": "readonly"},
        {"name": "ingest_res", "cellType": "custom", "size": QSize(100, 25)},
        {"name": "resize_type", "cellType": "checkbox"},
        {"name": "ingest_effects", "cellType": "custom"},
        {"name": "cur_version", "cellType": "readonly"},
        {"name": "cur_grade", "cellType": "custom"},
        {"name": "sg_tags", "cellType": "custom"},
        {"name": "edit_note", "cellType": "checkbox"},
        {"name": "head_handles", "cellType": "text"},
        {"name": "cut_in", "cellType": "text"},
        {"name": "cut_out", "cellType": "text"},
        {"name": "tail_handles", "cellType": "text"},
        {
            "name": "valid_entity",
            "cellType": "readonly",
        },
        {"name": "op_frame_start", "cellType": "text", "size": QSize(40, 25)},
        {"name": "op_family", "cellType": "dropdown", "size": QSize(10, 25)},
        {"name": "op_handle_start", "cellType": "text", "size": QSize(10, 25)},
        {"name": "op_handle_end", "cellType": "text", "size": QSize(10, 25)},
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
            track_item_episode = get_track_item_env(item).get("EPISODE")

            return track_item_episode or "--"

        elif current_column["name"] == "Sequence":
            track_item_sequence = get_track_item_env(item).get("SEQ")

            return track_item_sequence or "--"

        elif current_column["name"] == "Shot":
            track_item_shot = get_track_item_env(item).get("SHOT")

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

        elif current_column["name"] == "cur_version":
            instance_data = item.openpype_instance_data()
            if not instance_data:
                return "--"

            project_name = os.getenv("AVALON_PROJECT")
            asset = instance_data.get("asset")
            subset = instance_data.get("subset")
            last_version_doc = get_last_version_by_subset_name(
                project_name, subset, asset_name=asset
            )

            if last_version_doc:
                last_version = last_version_doc["name"]
                return str(last_version)
            else:
                return "--"

        elif current_column["name"] == "ingest_res":
            return item.ingest_res_data().get("resolution", "--")

        elif current_column["name"] == "resize_type":
            return item.ingest_res_data().get("resize", "--")

        elif current_column["name"] == "edit_note":
            return item.edit_note_data().get("note", "--")

        elif current_column["name"] == "ingest_effects":
            effects_data = item.ingest_effects_data()

            effects = []
            for key in sorted(effects_data, key=INGEST_EFFECTS.index):
                if effects_data[key] == "True":
                    effects.append(key)

            if effects:
                return ", ".join(effects)
            else:
                return "--"

        elif current_column["name"] in [
            "cut_in",
            "cut_out",
            "head_handles",
            "tail_handles",
        ]:
            if not isinstance(item.parent(), hiero.core.VideoTrack):
                return "--"

            tag_key = current_column["name"]
            current_tag_text = item.cut_info_data().get(tag_key, "--")

            return current_tag_text

        elif "op_" in current_column["name"]:
            instance_key = current_column["name"]
            current_tag_text = item.openpype_instance_data().get(
                f"{instance_key.split('op_')[-1]}", "--"
            )
            if not isinstance(item.parent(), hiero.core.VideoTrack):
                return "--"

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

        elif current_column["name"] == "Episode":
            return "Episode name of current track item if valid otherwise --"

        elif current_column["name"] == "Sequence":
            return "Sequence name of current track item if valid otherwise --"

        elif current_column["name"] == "Shot":
            return "Shot name of current track item if valid otherwise --"

        elif current_column["name"] == "ingest_res":
            return (
                "When provided overrides the default resolution value from "
                "both Plate resolution and Shotgrid ingest resolution.\n\n"
                "Text input format:\n{width}x{height}\ni.e. 1920x1080"
            )

        elif current_column["name"] == "resize_type":
            return (
                "Nuke like resize types that is used for determining how to "
                "perform the reformating action when aspect ratio differs"
            )

        elif current_column["name"] == "ingest_effects":
            return "Effects to apply to track item on ingest"

        elif current_column["name"] == "cur_version":
            return "Current ingested items latest current published version"

        elif current_column["name"] == "cur_grade":
            return (
                "After ingesting media the grade (if one was used) will show "
                "up here.\nDouble click to see full path"
            )

        elif current_column["name"] == "sg_tags":
            return (
                "Shot tags that you'd like applied to the items Shotgrid Shot"
            )

        elif current_column["name"] == "edit_note":
            return (
                "Editorial Note that gets applied to the items Shotgrid Shot\n"
                "If this note was already made it be created again"
            )

        elif current_column["name"] == "cut_in":
            return (
                "Shot 'cut in' frame. This is meant to be ground truth and can"
                " be used to sync to SG.\n\n Operators are supported "
                "i.e:\n'+20' -> 1001+20=1021\n'-10' -> 1010-10=1000\n'*2' -> "
                "8*2=16\n'/2' -> 16/2=8\n\nWhen written in 1001-10 form the "
                "expression will evaluate first. For multi-select updates that"
                " may yield unintended results"
            )

        elif current_column["name"] == "cut_out":
            return (
                "Shot 'cut out' frame. This is meant to be ground truth and can"
                " be used to sync to SG.\n\n Operators are supported "
                "i.e:\n'+20' -> 1001+20=1021\n'-10' -> 1010-10=1000\n'*2' -> "
                "8*2=16\n'/2' -> 16/2=8\n\nWhen written in 1001-10 form the "
                "expression will evaluate first. For multi-select updates that"
                " may yield unintended results"
            )

        elif current_column["name"] == "head_handles":
            return (
                "Shot 'head handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG.\n\n Operators are supported "
                "i.e:\n'+20' -> 1001+20=1021\n'-10' -> 1010-10=1000\n'*2' -> "
                "8*2=16\n'/2' -> 16/2=8\n\nWhen written in 1001-10 form the "
                "expression will evaluate first. For multi-select updates that"
                " may yield unintended results"
            )

        elif current_column["name"] == "tail_handles":
            return (
                "Shot 'tail handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG.\n\n Operators are supported "
                "i.e:\n'+20' -> 1001+20=1021\n'-10' -> 1010-10=1000\n'*2' -> "
                "8*2=16\n'/2' -> 16/2=8\n\nWhen written in 1001-10 form the "
                "expression will evaluate first. For multi-select updates that"
                " may yield unintended results"
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
                return EVEN_COLUMN_COLOR
            else:
                # For reference default odd row is 53, 53, 53
                return ODD_COLUMN_COLOR

        return None

    def getForeground(self, row, column, item):
        """Return the text color for a cell"""
        if self.column_list[column]["name"].startswith("op_"):
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
        # Save the painter so it can restored later
        painter.save()

        # Set highlight for selected items
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            if row % 2 == 0:
                painter.setPen(EVEN_COLUMN_COLOR)
            else:
                painter.setPen(ODD_COLUMN_COLOR)

        else:
            painter.setPen(QColor(195, 195, 195))

        current_column = self.column_list[column]
        if current_column["name"] == "Tags":
            # Set highlight for selected items
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
                painter.setClipRect(option.rect)
                for tag in item.tags():
                    QIcon(tag.icon()).paint(painter, rectangle, Qt.AlignCenter)
                    rectangle.translate(rectangle.width() + 2, 0)

                painter.restore()
                return True

        elif current_column["name"] == "cur_grade":
            # Set highlight for selected items
            if option.state & QStyle.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())
                if row % 2 == 0:
                    painter.setPen(EVEN_COLUMN_COLOR)
                else:
                    painter.setPen(ODD_COLUMN_COLOR)

            else:
                painter.setPen(QColor(195, 195, 195))

            ingest_grade = item.openpype_instance_data().get("ingested_grade")

            painter.setClipRect(option.rect)

            if not ingest_grade or ingest_grade == "None":
                painter.drawText(option.rect, Qt.AlignLeft, "--")

                painter.restore()
                return True

            margin = QMargins(0, 0, 5, 0)
            text = option.fontMetrics.elidedText(
                ingest_grade, Qt.ElideLeft, option.rect.width()
            )
            painter.drawText(
                option.rect - margin, Qt.AlignRight | Qt.AlignVCenter, text
            )

            painter.restore()
            return True

        elif current_column["name"] == "sg_tags":
            painter.setClipRect(option.rect)
            sg_tag_data = item.sg_tags_data()
            if not sg_tag_data:
                painter.drawText(option.rect, Qt.AlignLeft, "--")

                painter.restore()
                return True

            iconSize = 20
            rectangle = QRect(
                option.rect.x(),
                option.rect.y() + (option.rect.height() - iconSize) / 2,
                iconSize,
                iconSize,
            )

            # Need to make sure the icons are sorted for easy readability
            for key in sorted(sg_tag_data, key=SG_TAGS.index):
                if sg_tag_data[key] == "True":
                    QIcon(SG_TAG_ICONS[key]).paint(
                        painter, rectangle, Qt.AlignCenter
                    )
                    rectangle.translate(rectangle.width() + 2, 0)

            painter.restore()
            return True

        painter.restore()
        return False

    def createEditor(self, row, column, item, view):
        """Create an editing widget for a custom cell"""
        self.currentView = view
        current_column = self.column_list[column]

        if current_column["cellType"] == "readonly" or not isinstance(
            item.parent(), hiero.core.VideoTrack
        ):
            # readonly is done by removing visibility and useability of the
            # returned widget to the widget viewer
            edit_widget = QLabel()
            edit_widget.setEnabled(False)
            edit_widget.setVisible(False)

            return edit_widget

        elif current_column["name"] == "Colorspace":
            ocio_config = get_active_ocio_config()
            edit_widget = ColorspaceWidget(ocio_config)
            edit_widget.root_menu.triggered.connect(self.colorspace_changed)

            return edit_widget

        elif current_column["name"] == "ingest_res":
            current_format = item.ingest_res_data().get("resolution", "")

            resolution_combo = IngestResWidget(item, current_format)

            resolution_combo.currentIndexChanged.connect(
                lambda: self.ingest_res_changed(resolution_combo, "index")
            )
            resolution_combo.lineEdit().returnPressed.connect(
                lambda: self.ingest_res_changed(
                    resolution_combo.lineEdit(), "return"
                )
            )

            return resolution_combo

        elif current_column["name"] == "resize_type":
            # Let user know that ingest format must exist first
            current_resize_type = item.ingest_res_data().get("resize")
            if not current_resize_type:
                QMessageBox.warning(
                    hiero.ui.mainWindow(),
                    "Critical",
                    "No Ingest Resolution found\n"
                    "Please assign an Ingest Resolution first",
                )

            resize_type = QComboBox()
            resize_type.addItem("none")
            resize_type.addItem("width")
            resize_type.addItem("height")
            resize_type.addItem("fit")
            resize_type.addItem("fill")
            resize_type.addItem("distort")

            resize_index = resize_type.findText(current_resize_type)
            resize_type.setCurrentIndex(resize_index)
            resize_type.currentIndexChanged.connect(
                lambda: self.ingest_res_type_changed(resize_type)
            )

            return resize_type

        elif current_column["name"] == "ingest_effects":
            ingest_effects_state = item.ingest_effects_data()
            ingest_effects_edit_widget = IngestEffectsWidget(
                ingest_effects_state
            )
            ingest_effects_edit_widget.root_menu.aboutToHide.connect(
                lambda: self.ingest_effect_changed(ingest_effects_edit_widget)
            )

            return ingest_effects_edit_widget

        elif current_column["name"] == "cur_grade":
            # If user double clicks on current grade. Show the full path and
            # disable editing
            ingest_grade = item.openpype_instance_data().get("ingested_grade")
            if not ingest_grade or ingest_grade == "None":
                edit_widget = QLabel()
                edit_widget.setEnabled(False)
                edit_widget.setVisible(False)

                return edit_widget

            widget = CurrentGradeWidget(ingest_grade)

            return widget

        elif current_column["name"] == "sg_tags":
            sg_tag_state = item.sg_tags_data()
            sg_tag_edit_widget = SGTagsWidget(sg_tag_state)
            sg_tag_edit_widget.root_menu.aboutToHide.connect(
                lambda: self.sg_tags_changed(sg_tag_edit_widget)
            )

            return sg_tag_edit_widget

        elif current_column["name"] == "edit_note":
            current_edit_note = item.edit_note_data().get("note", "")

            edit_widget = QLineEdit()
            edit_widget.setText(current_edit_note)

            edit_widget.returnPressed.connect(
                lambda: self.edit_note_changed(edit_widget)
            )

            return edit_widget

        elif current_column["name"] in [
            "cut_in",
            "cut_out",
            "head_handles",
            "tail_handles",
        ]:
            tag_key = current_column["name"]
            current_text = item.cut_info_data().get(tag_key)
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(tag_key)
            edit_widget.returnPressed.connect(
                lambda: self.cut_info_changed(edit_widget)
            )

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

            instance_tag = item.get_openpype_instance()
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
                lambda: self.openpype_instance_changed(combo_widget)
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
            current_text = item.cut_info_data().get(f"tag.{instance_key}")
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(instance_key)
            edit_widget.returnPressed.connect(
                lambda: self.openpype_instance_changed(edit_widget)
            )

            return edit_widget

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

            instance_tag = item.get_openpype_instance()
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
                lambda: self.openpype_instance_changed(combo_widget)
            )

            return combo_widget

        return None

    def setModelData(self, row, column, item, editor):
        return False

    def dropMimeData(self, row, column, item, data, drop_items):
        """Handle a drag and drop operation - adds a Dragged Tag to the shot"""
        for drop_item in drop_items:
            if isinstance(drop_item, hiero.core.Tag):
                item.addTag(drop_item)

        return None

    @column_widget_callback
    def colorspace_changed(self, selection, project, action):
        """This method is called when Colorspace widget changes index."""
        colorspace = action.text()

        with project.beginUndo("Set Colorspace"):
            track_item = None
            for track_item in selection:
                track_item.setSourceMediaColourTransform(colorspace)

    @column_widget_callback
    def ingest_res_changed(self, selection, project, sender, signal_type):
        if signal_type == "index":
            # Don't want current text incase it's from line edit?
            ingest_resolution = sender.currentText()
        else:
            ingest_resolution = sender.text().strip()

        key = "resolution"

        with project.beginUndo("Set Ingest Resolution"):
            if ingest_resolution != "--":
                ingest_resolution = ingest_resolution.split(" - ")[0]
                for track_item in selection:
                    track_item.set_ingest_res(key, ingest_resolution)

            else:
                for track_item in selection:
                    ingest_res_tag = track_item.get_ingest_res()
                    if ingest_res_tag:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Ingest Resolution' tag"
                        )
                        track_item.removeTag(ingest_res_tag)

    @column_widget_callback
    def ingest_res_type_changed(self, selection, project, sender):
        resize_type = sender.currentText()
        key = "resize"

        with project.beginUndo("Set Ingest Resolution Resize Type"):
            for track_item in selection:
                track_item.set_ingest_res(key, resize_type)

    @column_widget_callback
    def ingest_effect_changed(
        self, selection, project, ingest_effects_edit_widget
    ):
        ingest_effects_edit_widget.set_effects_data()
        effect_states = ingest_effects_edit_widget.effects_data

        with project.beginUndo("Update Ingest Effects"):
            for track_item in selection:
                track_item.set_ingest_effects(effect_states)

    @column_widget_callback
    def sg_tags_changed(self, selection, project, sg_tag_edit_widget):
        sg_tag_edit_widget.set_tag_data()
        tag_states = sg_tag_edit_widget.tag_data

        with project.beginUndo("Update SG Tag Toggle"):
            for track_item in selection:
                track_item.set_sg_tags(tag_states)

    @column_widget_callback
    def edit_note_changed(self, selection, project, sender):
        text = sender.text()

        with project.beginUndo("Set Edit Note"):
            if text != "--":
                for track_item in selection:
                    track_item.set_edit_note(text)

            # If value is -- this is used as an easy to remove Edit Note tag
            else:
                for track_item in selection:
                    edit_note_tag = track_item.get_edit_note()
                    if edit_note_tag:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Edit Note' tag"
                        )
                        track_item.removeTag(edit_note_tag)

    @column_widget_callback
    def cut_info_changed(self, selection, project, sender):
        key = sender.objectName()
        value = sender.text().strip()

        value_no_operators = value.translate(NO_OP_TRANSLATE)
        # Only pass on edit if user unintentionally erased value from column
        if value not in ["--", ""] and not value_no_operators.isdigit():
            return
        else:
            # Remove preceding zeros
            value = value if value == "0" else value.lstrip("0")

        with project.beginUndo("Set Cut Info"):
            operate = value != value_no_operators
            if value != "--":
                for track_item in selection:
                    track_item.set_cut_info(key, value, operate)

            # If value is -- this is used as an easy to remove Cut Info tag
            else:
                for track_item in selection:
                    cut_info_tag = track_item.get_cut_info()
                    if cut_info_tag:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Cut Info' tag"
                        )
                        track_item.removeTag(cut_info_tag)

    @column_widget_callback
    def openpype_instance_changed(self, selection, project, sender):
        key = sender.objectName()
        if isinstance(sender, QComboBox):
            value = sender.currentText()
        else:
            value = sender.text()

        with project.beginUndo("Set Openpype Instance"):
            # If value is -- this is used as an easy to remove openpype tag
            if value.strip() == "--":
                for track_item in selection:
                    openpype_instance = track_item.get_openpype_instance()
                    if openpype_instance:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Cut Info' tag"
                        )
                        track_item.removeTag(openpype_instance)
            else:
                for track_item in selection:
                    track_item.set_openpype_instance(key, value)


def _set_cut_info(self, key, value, operate):
    """Empty value is allowed incase editor wants to create a cut tag with
    default values
    """
    # Cut tag can be set from a variety of columns
    # Need to logic for each case
    cut_tag = self.get_cut_info()

    # Can't do operations on an empty value
    if not cut_tag and operate:
        return

    if not cut_tag:
        # get default handles
        cut_tag = hiero.core.Tag("Cut Info")
        cut_tag.setIcon("icons:TagKeylight.png")
        project_name = get_current_project_name()
        frame_start, handle_start, handle_end = get_frame_defaults(
            project_name
        )

        if frame_start and handle_start:
            frame_offset = frame_start + handle_start
            if value:
                if key == "cut_in":
                    frame_offset = int(value)
                elif key == "cut_out":
                    frame_offset = int(value) - self.duration() + 1

            cut_in = frame_offset
            cut_out = frame_offset + self.duration() - 1

        else:
            cut_in = None
            cut_out = None

        cut_data = {}
        cut_data["cut_in"] = cut_in
        cut_data["cut_out"] = cut_out
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
        if operate:
            # Leave operation as is if value has valid expression
            if value[0].isdigit():
                operation = value
            else:
                current_value = cut_tag.metadata().value(f"tag.{key}")
                operation = f"{current_value}{value}"

            try:
                # Frames must be integers
                value = str(int(eval(operation)))
            except SyntaxError:
                log.info(
                    f"{self.parent().name()}.{self.name()}: "
                    f"{value} must be properly formatted. Read"
                    "tooltip for more information"
                )
                return

        cut_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()


def get_tag(self, name, contains=False):
    tags = self.tags()
    for tag in tags:
        if contains:
            if name in tag.name():
                return tag
        else:
            if name == tag.name():
                return tag

    return None


def get_tag_data(self, name, contains=False):
    tag = get_tag(self, name, contains)
    tag_data = {}

    if not tag:
        return tag_data

    convert_keys = TAG_DATA_KEY_CONVERT.get(name, {})
    tag_meta_data = tag.metadata().dict()
    for key, value in tag_meta_data.items():
        # Convert data from column names into tag key names
        if key in convert_keys:
            tag_data[convert_keys[key]] = value
        else:
            tag_data[key.split("tag.")[-1]] = value

    # Remove default keys
    for key in ("label", "applieswhole"):
        if key in tag_data:
            del tag_data[key]

    return tag_data


def get_frame_defaults(project_name):
    # Grab handle infos from SG
    filters = [
        [
            "name",
            "is",
            project_name,
        ],
    ]
    fields = [
        "sg_show_handles",
        "sg_default_start_frame",
    ]
    sg_project = SHOTGRID.find_one("Project", filters, fields)

    if not sg_project:
        return 1001, 8, 8

    frame_start_default = sg_project.get("sg_default_start_frame", 1001)
    handle_start_default = sg_project.get("sg_show_handles", 8)

    return (frame_start_default, handle_start_default, handle_start_default)


def get_entity_hierarchy(asset_doc, project_name):
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
    project_doc = get_project(project_name)
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


def get_hierarchy_data(asset_doc, project_name, track_name):
    hierarchy_data = get_entity_hierarchy(asset_doc, project_name)
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
    value = value if value == "0" else value.strip().lstrip("0")

    # Validate key value
    # No need to validate family as it's a prefilled combobox
    if key in ["frame_start", "handle_start", "handle_end"]:
        # Skip validation if user simply wants to create default tag
        if value:
            if not value.isdigit():
                log.info(
                    f"{self.parent().name()}.{self.name()}: "
                    f"{key} must be a valid number"
                )
                return

    convert_keys = {
        "frame_start": "workfileFrameStart",
        "handle_start": "handleStart",
        "handle_end": "handleEnd",
    }
    # Convert data from column names into OP instance names
    key = convert_keys.get(key, key)

    instance_tag = self.get_openpype_instance()
    track_item_name = self.name()
    track_name = self.parentTrack().name()
    # Check if asset has valid name
    # if not don't create instance
    if not is_valid_asset(self):
        if instance_tag:
            log.info(
                f"{self.parent().name()}.{self.name()}: "
                "Track item name no longer valid. Removing Openpype tag"
            )
            self.removeTag(instance_tag)
        else:
            log.info(
                f"{self.parent().name()}.{self.name()}: "
                "Track item name not found in DB!"
            )

        return
    else:
        project_name = get_current_project_name()
        asset_doc = get_asset_by_name(project_name, self.name())

    instance_data = {}
    if not instance_tag:
        # First fill default instance if no tag found and then update with
        # data parameter
        families = ["clip"]
        if "ref" in track_name:
            family = "reference"
        else:
            families.append("review")
            family = "plate"

        hierarchy_data = get_hierarchy_data(
            asset_doc, project_name, track_name
        )
        hierarchy_path = get_hierarchy_path(asset_doc)
        hierarchy_parents = get_hierarchy_parents(hierarchy_data)
        frame_start, handle_start, handle_end = get_frame_defaults(
            project_name
        )

        instance_data["hierarchyData"] = hierarchy_data
        instance_data["hierarchy"] = hierarchy_path
        instance_data["parents"] = hierarchy_parents
        instance_data["asset"] = track_item_name
        instance_data["subset"] = track_name
        instance_data["family"] = family
        instance_data["families"] = str(families)
        instance_data["workfileFrameStart"] = frame_start
        instance_data["handleStart"] = (
            handle_start if family == "plate" else "0"
        )
        instance_data["handleEnd"] = handle_end if family == "plate" else "0"

        # Constants
        instance_data["audio"] = "True"
        instance_data["heroTrack"] = "True"
        instance_data["id"] = "pyblish.avalon.instance"
        instance_data["publish"] = "True"
        instance_data["reviewTrack"] = "None"
        instance_data["sourceResolution"] = "False"
        instance_data["variant"] = "Main"
        instance_data["use_nuke"] = "False"
        instance_data["ingested_grade"] = "None"

    if value:
        # When family is changed the families need to adapt
        if key == "family":
            families = ["clip"]
            if value == "plate":
                families.append("review")

            instance_data.update({"families": families})

        instance_data.update({key: value})

    set_trackitem_openpype_tag(self, instance_data)

    self.sequence().editFinished()


def _set_ingest_effects(self, states):
    effect_tag = self.get_ingest_effects()

    if not effect_tag:
        effect_tag = hiero.core.Tag("Ingest Effects")
        effect_tag.setIcon("icons:TimelineToolSoftEffect.png")
        # Add default resize type
        self.sequence().editFinished()
        self.addTag(effect_tag)
        # Need this here because the correct tag on run is always the next one
        _set_ingest_effects(self, states)
    else:
        # Remove tag if all states are False
        if not [
            effect_tag for effect_tag in states if states[effect_tag] == True
        ]:
            self.removeTag(effect_tag)
            return

    effect_tag_meta = effect_tag.metadata()
    for key, value in states.items():
        # Meta will always have all SG tag states
        # Convert to string to match tag metadata needs
        value = "True" if value else "False"
        effect_tag_meta.setValue(f"tag.{key}", value)


def _set_sg_tags(self, states):
    sg_tag = self.get_sg_tags()

    if not sg_tag:
        sg_tag = hiero.core.Tag("SG Tags")
        sg_tag.setIcon("icons:EffectsTiny.png")
        # Add default resize type
        self.sequence().editFinished()
        self.addTag(sg_tag)
        # Need this here because the correct tag on run is always the next one
        _set_sg_tags(self, states)
    else:
        # Remove tag if all states are False
        if not [sg_tag for sg_tag in states if states[sg_tag] == True]:
            self.removeTag(sg_tag)
            return

    sg_tag_meta = sg_tag.metadata()
    for key, value in states.items():
        # Meta will always have all SG tag states
        # Convert to string to match tag metadata needs
        value = "True" if value else "False"
        sg_tag_meta.setValue(f"tag.{key}", value)


def _set_ingest_res(self, key, value):
    ingest_res_tag = self.get_ingest_res()

    if not ingest_res_tag:
        ingest_res_tag = hiero.core.Tag("Ingest Resolution")
        ingest_res_tag.setIcon("icons:PPResolution.png")
        ingest_res_data = {}
        ingest_res_data["resize"] = "width"

        if value:
            ingest_res_data.update({key: value})

        for key, value in ingest_res_data.items():
            if not isinstance(value, str):
                value = str(value)
            ingest_res_tag.metadata().setValue(f"tag.{key}", value)

        self.sequence().editFinished()
        self.addTag(ingest_res_tag)

        # Need this here because the correct tag on run is always the next one
        _set_ingest_res(self, key, value)

    ingest_res_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()


def _set_edit_note(self, note):
    edit_note_tag = self.get_edit_note()

    if not edit_note_tag:
        edit_note_tag = hiero.core.Tag("Edit Note")
        edit_note_tag.setIcon("icons:SyncMessage.png")

        self.sequence().editFinished()
        self.addTag(edit_note_tag)

        # Need this here because the correct tag on run is always the next one
        _set_edit_note(self, note)

    edit_note_tag.metadata().setValue(f"tag.note", note)

    self.sequence().editFinished()


def _update_op_instance_asset(event):
    # Always iter through all items since the user may never reselected the
    timeline = hiero.ui.activeSequence()
    track_items = []
    # Grab all track items
    if timeline:
        for video_track in timeline.videoTracks():
            for item in video_track.items():
                if isinstance(item, hiero.core.TrackItem):
                    track_items.append(item)

    for track_item in track_items:
        instance_tag = track_item.get_openpype_instance()
        if not instance_tag:
            continue

        track_item_name = track_item.name()
        track_name = track_item.parentTrack().name()
        project_name = get_current_project_name()
        asset_doc = get_asset_by_name(project_name, track_item_name)
        if not asset_doc:
            track_item.removeTag(instance_tag)
            continue

        hierarchy_data = get_hierarchy_data(
            asset_doc, project_name, track_name
        )
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
            log.info(
                f"{track_name}.{track_item_name}: "
                "OP Instance updated - data modified"
            )


class TrackRenameEvent:
    previous_track_item = ()

    def _update_op_instance_subset(self, event):
        video_track_change = False
        # Because video tracks and track items can not coexist within a single
        # selection it's known that if first index of selection is video track
        # previous change of selection was for video track
        if self.previous_track_item:
            if isinstance(self.previous_track_item[0], hiero.core.VideoTrack):
                video_track_change = True

        if not video_track_change:
            self.previous_track_item = event.sender.selection()
            return

        # Always iter through all items since the user may never reselected the
        timeline = event.sender
        track_items = []
        # Grab relevant track items
        if timeline:
            for video_track in self.previous_track_item:
                for item in video_track.items():
                    if isinstance(item, hiero.core.TrackItem):
                        track_items.append(item)

        for track_item in track_items:
            instance_tag = track_item.get_openpype_instance()
            if not instance_tag:
                continue

            track_item_name = track_item.name()
            track_name = track_item.parentTrack().name()
            project_name = get_current_project_name()
            asset_doc = get_asset_by_name(project_name, track_item_name)
            if not asset_doc:
                track_item.removeTag(instance_tag)
                continue

            hierarchy_data = get_hierarchy_data(
                asset_doc, project_name, track_name
            )

            instance_data = {}
            instance_data["hierarchyData"] = hierarchy_data
            instance_data["subset"] = track_name
            update = False
            for key, value in instance_data.items():
                current_value = instance_tag.metadata().value(f"tag.{key}")
                # Need to compare objects in true form
                if (
                    f"{current_value[0]}{current_value[-1]}"
                    in ["{}", "[]", "()"]
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
                log.info(
                    f"{track_name}.{track_item_name}: "
                    "OP Instance updated - data modified"
                )

        self.previous_track_item = event.sender.selection()


def _update_avalon_track_item(event):
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

    for track_item in track_items:
        track_item_env = get_track_item_env(track_item)
        track_item.hierarchy_env = track_item_env


# Attach tag setters, getters and tag data get into hiero.core.TrackItem
hiero.core.TrackItem.set_ingest_res = _set_ingest_res
hiero.core.TrackItem.get_ingest_res = lambda self: get_tag(
    self, "Ingest Resolution"
)
hiero.core.TrackItem.ingest_res_data = lambda self: get_tag_data(
    self, "Ingest Resolution"
)

hiero.core.TrackItem.set_ingest_effects = _set_ingest_effects
hiero.core.TrackItem.get_ingest_effects = lambda self: get_tag(
    self, "Ingest Effects"
)
hiero.core.TrackItem.ingest_effects_data = lambda self: get_tag_data(
    self, "Ingest Effects"
)

hiero.core.TrackItem.set_sg_tags = _set_sg_tags
hiero.core.TrackItem.get_sg_tags = lambda self: get_tag(self, "SG Tags")
hiero.core.TrackItem.sg_tags_data = lambda self: get_tag_data(self, "SG Tags")

hiero.core.TrackItem.set_edit_note = _set_edit_note
hiero.core.TrackItem.get_edit_note = lambda self: get_tag(self, "Edit Note")
hiero.core.TrackItem.edit_note_data = lambda self: get_tag_data(
    self, "Edit Note"
)

hiero.core.TrackItem.set_cut_info = _set_cut_info
hiero.core.TrackItem.get_cut_info = lambda self: get_tag(self, "Cut Info")
hiero.core.TrackItem.cut_info_data = lambda self: get_tag_data(
    self, "Cut Info"
)

hiero.core.TrackItem.set_openpype_instance = _set_openpype_instance
hiero.core.TrackItem.get_openpype_instance = lambda self: get_tag(
    self, OPENPYPE_TAG_NAME, contains=True
)
hiero.core.TrackItem.openpype_instance_data = lambda self: get_tag_data(
    self, OPENPYPE_TAG_NAME, contains=True
)


# Register openpype instance update event
hiero.core.events.registerInterest(
    "kSequenceEdited", _update_op_instance_asset
)

# SequenceEdited only capture Track Item renames and not Video Track renames
# Need class to keep track of previous selection to limit event runtime
track_rename = TrackRenameEvent()
hiero.core.events.registerInterest(
    "kSelectionChanged/kTimeline", track_rename._update_op_instance_subset
)

# Register validation query to avalon
hiero.core.events.registerInterest(
    "kSequenceEdited", _update_avalon_track_item
)

# Register our custom columns
hiero.ui.customColumn = CustomSpreadsheetColumns()
