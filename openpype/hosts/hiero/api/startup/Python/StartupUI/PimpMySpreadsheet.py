import bisect
import glob
import hiero
import os
import PyOpenColorIO
import random
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy.QtGui import *


from openpype.client import get_asset_by_name
from openpype.pipeline.context_tools import get_current_project_name
from openpype.hosts.hiero.api.lib import set_trackitem_openpype_tag, \
                                        get_trackitem_openpype_tag


def get_active_ocio_config():
    """
    Grab the OCIO config from $OCIO or if not found fromt the current hiero session

    return OCIO config
    """
    env_ocio_path = os.getenv('OCIO')

    active_seq = hiero.ui.activeSequence()
    proj_ocio_config_path = active_seq.project().ocioConfigPath() if active_seq else ""
    proj_ocio_config_name = active_seq.project().ocioConfigName() if active_seq else ""

    if env_ocio_path:
        ocio_path = env_ocio_path
        ocio_config = PyOpenColorIO.Config.CreateFromFile(ocio_path)
        # Returning now. No need to search other places for config
        return ocio_config

    # If not OCIO found in envion then check project OCIO
    active_seq = hiero.ui.activeSequence()
    configs_path = __file__.split('plugins')[0] + "plugins/OCIOConfigs/configs"
    if active_seq:
        if project.ocioConfigPath():
            ocio_path = project.ocioConfigPath()
            # Use default config path from sw
        elif project.ocioConfigName():
            hiero_configs = glob.glob(configs_path+'/**/*.ocio', recursive=True)
            for config in hiero_configs:
                config_name = pathlib.Path(config).parent.name
                if project.ocioConfigName() == config_name:
                    ocio_path = config

    # Else statement is a catch for when the spreadsheet runs without sequence
    # loaded
    else:
        ocio_path = os.path.join(configs_path, 'nuke-default/config.ocio')

    # OCIO_path = '/mnt/ol03/Projects/evl_s4/_pipeline/ocio/config.ocio'
    ocio_config = PyOpenColorIO.Config.CreateFromFile(ocio_path)
    return ocio_config


class Colorspace_Widget(QMainWindow):
    def __init__(self, ocio_config, parent=None):
        super(Colorspace_Widget, self).__init__(parent)

        # Change how roles are added - add them to the base menu using the getRoles method
        self.colorspace_button = QPushButton("Colorspaces")
        # Menu must be stored on self. Button won't react properly without
        self.root_menu = QMenu("Main")

        menu_dict = {}
        color_roles = [f"{x[0]} ({x[1]})" for x in ocio_config.getRoles()]
        color_spaces = [(cs.getName(), cs.getFamily()) for cs in ocio_config.getColorSpaces()]
        for role in color_roles:
            role_action = QAction(role, self.root_menu)
            self.root_menu.addAction(role_action)

        # Create menu_dict which stores the hierarchy and associated colorspace
        for name, family in color_spaces:
            parts = family.split('/')
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
        # Function to sort menus alphabeitcally
        menu_actions = []
        normal_actions = []

        for action, is_menu in actions:
            if is_menu:
                menu_actions.append((action, is_menu))
            else:
                normal_actions.append((action, is_menu))

        if menu_actions:
            # Sort menus alphabetically
            index = bisect.bisect_left([x[0].text() for x in menu_actions], menu_text)
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
        # Function to sort actions alphabetically
        menu_actions = []
        normal_actions = []
        for action, is_menu in actions:
            if is_menu:
                menu_actions.append((action, is_menu))
            else:
                normal_actions.append((action, is_menu))

        if normal_actions:
            # Sort actions alphabetically
            index = bisect.bisect_left([x[0].text() for x in normal_actions], action_text)
            if index == len(normal_actions):

                return (None, None)
            else:
                action_index = actions.index(normal_actions[index])

                return (normal_actions[index][0], action_index)

        else:
            return (None, None)

    def build_menu(self, menu_data, family_name):
        menu = QMenu(family_name)
        # Can't rely on widget chrildren since the menu is built recursively
        prev_items = []
        for key, value in menu_data.items():
            if value is None:
                action = QAction(key, menu)
                target_action, insert_index = self.action_insert_target(prev_items, key)
                if target_action:
                    menu.insertAction(target_action, action)
                    prev_items.insert(insert_index, (action, False))
                else:
                    menu.addAction(action)
                    prev_items.append((action, False))
            else:
                # Since value is not None then this is a submenu
                # Need to place submenu at begginging of current submenu
                submenu = self.build_menu(value, key)
                target_submenu, insert_index = self.menu_insertion_target(prev_items, key)
                if target_submenu:
                    menu.insertMenu(target_submenu, submenu)
                    prev_items.insert(insert_index, (submenu.menuAction(), True))
                else:
                    menu.addMenu(submenu)
                    prev_items.append((submenu.menuAction(), True))

        return menu


def get_asset_parents(track_item_name):
    """Convience function from hiero instance creator
    Return parents from asset stored on the Avalon database.
    """

    project_name = get_current_project_name()
    asset_name = track_item_name
    asset_item = get_asset_by_name(project_name, asset_name)
    if asset_item:
        parents = asset_item["data"]["parents"]
    else:
        return None

    return parents


def get_track_item_shot(track_item_name):
    """Validate if shot exists in DB and if so return shot name"""
    parents = get_asset_parents(track_item_name)
    if parents is None:
        return None
    else:

        return track_item_name


def get_track_item_episode(track_item_name):
    """Validate if shot exists in DB and if so return episode name"""
    episode = ""

    parents = get_asset_parents(track_item_name)
    if parents is None:
        return None

    # Parents will always start with shots or assets
    folder = parents[0]
    if folder == "shots":
        if len(parents) > 2:
            episode = parents[-2]

            return episode

    return None


def get_track_item_sequence(track_item_name):
    """
    Validate if shot exists in DB and if so return sequence name
    """
    sequence = ""
    parents = get_asset_parents(track_item_name)
    if parents is None:

        return None

    # Parents will always start with shots or assets
    folder = parents[0]
    if folder == "shots":
        if len(parents) > 1:
            sequence = parents[-1]

            return sequence

    return None


# Set to True, if you wat "Set Status" right-click menu, False if not
# kAddStatusMenu = True

# Set to True, if you wat "Assign Artist" right-click menu, False if not
# kAssignArtistMenu = True

# Global list of Artist Name Dictionaries
# Note: Override this to add different names, icons, department, IDs.
# gArtistList = [{
#     "artistName": "John Smith",
#     "artistIcon": "icons:TagActor.png",
#     "artistDepartment": "3D",
#     "artistID": 0
# }, {
#     "artistName": "Savlvador Dali",
#     "artistIcon": "icons:TagActor.png",
#     "artistDepartment": "Roto",
#     "artistID": 1
# }, {
#     "artistName": "Leonardo Da Vinci",
#     "artistIcon": "icons:TagActor.png",
#     "artistDepartment": "Paint",
#     "artistID": 2
# }, {
#     "artistName": "Claude Monet",
#     "artistIcon": "icons:TagActor.png",
#     "artistDepartment": "Comp",
#     "artistID": 3
# }, {
#     "artistName": "Pablo Picasso",
#     "artistIcon": "icons:TagActor.png",
#     "artistDepartment": "Animation",
#     "artistID": 4
# }]

# Global Dictionary of Status Tags.
# Note: This can be overwritten if you want to add a new status cellType or custom icon
# Override the gStatusTags dictionary by adding your own "Status":"Icon.png" key-value pairs.
# Add new custom keys like so: gStatusTags["For Client"] = "forClient.png"
# gStatusTags = {
#     "Approved": "icons:status/TagApproved.png",
#     "Unapproved": "icons:status/TagUnapproved.png",
#     "Ready To Start": "icons:status/TagReadyToStart.png",
#     "Blocked": "icons:status/TagBlocked.png",
#     "On Hold": "icons:status/TagOnHold.png",
#     "In Progress": "icons:status/TagInProgress.png",
#     "Awaiting Approval": "icons:status/TagAwaitingApproval.png",
#     "Omitted": "icons:status/TagOmitted.png",
#     "Final": "icons:status/TagFinal.png"
# }


# The Custom Spreadsheet Columns
class CustomSpreadsheetColumns(QObject):
    """A class defining custom columns for Hiero's spreadsheet view. This has a
    similar, but slightly simplified, interface to the QAbstractItemModel and
    QItemDelegate classes.
    """
    global gStatusTags
    global gArtistList

    # Ideally, we'd set this list on a Per Item basis, but this is expensive for a large mixed selection
    standardColorSpaces = [
        "linear", "sRGB", "rec709", "Cineon", "Gamma1.8", "Gamma2.2",
        "Panalog", "REDLog", "ViperLog"
    ]
    arriColorSpaces = [
        "Video - Rec709", "LogC - Camera Native", "Video - P3", "ACES",
        "LogC - Film", "LogC - Wide Gamut"
    ]
    r3dColorSpaces = [
        "Linear", "Rec709", "REDspace", "REDlog", "PDlog685", "PDlog985",
        "CustomPDlog", "REDgamma", "SRGB", "REDlogFilm", "REDgamma2",
        "REDgamma3"
    ]
    gColorSpaces = standardColorSpaces + arriColorSpaces + r3dColorSpaces

    currentView = hiero.ui.activeView()

    # This is the list of Columns available
    # readonly implies QLabel
    # dropdown implies QCombo
    # c implies QTextEdit
    # These are namely used for generic column items that don't need much configeration
    gCustomColumnList = [
        {
            "name": "Tags",
            "cellType": "readonly"
        },
        {
            "name": "Colorspace",
            "cellType": "dropdown"
        },
        {
            "name": "Notes",
            "cellType": "readonly"
        },
        {
            "name": "FileType",
            "cellType": "readonly"
        },
        # {
        #     "name": "Shot Status",
        #     "cellType": "dropdown"
        # },
        # {
        #     "name": "Thumbnail",
        #     "cellType": "readonly"
        # },
        # {
        #     "name": "MediaType",
        #     "cellType": "readonly"
        # },
        {
            "name": "WidthxHeight",
            "cellType": "readonly"
        },
        # {
        #     "name": "Height",
        #     "cellType": "readonly"
        # },
        {
            "name": "Pixel Aspect",
            "cellType": "readonly"
        },
        # {
        #     "name": "Artist",
        #     "cellType": "dropdown"
        # },
        # {
        #     "name": "Department",
        #     "cellType": "readonly"
        # },
        {
            "name": "Episode",
            "cellType": "readonly"
        },
        {
            "name": "Sequence",
            "cellType": "readonly"
        },
        {
            "name": "Shot",
            "cellType": "readonly"
        },
        {
            "name": "cut_in",
            "cellType": "text"
        },
        {
            "name": "head_handles",
            "cellType": "text"
        },
        {
            "name": "tail_handles",
            "cellType": "text"
        },
        {
            "name": "op_frame_start",
            "cellType": "text"
        },
        {
            "name": "op_family",
            "cellType": "text"
        },
        {
            "name": "op_handle_end",
            "cellType": "text"
        },
        {
            "name": "op_handle_start",
            "cellType": "text"
        },
    ]

    def numColumns(self):
        """Return the number of custom columns in the spreadsheet view"""

        return len(self.gCustomColumnList)

    def columnName(self, column):
        """Return the name of a custom column"""

        return self.gCustomColumnList[column]["name"]

    def getTagsString(self, item):
        """Convenience method for returning all the Notes in a Tag as a string"""
        tagNames = []
        tags = item.tags()
        for tag in tags:
            tagNames += [tag.name()]
        tagNameString = ','.join(tagNames)

        return tagNameString

    def getNotes(self, item):
        """Convenience method for returning all the Notes in a Tag as a string"""
        notes = ""
        tags = item.tags()
        for tag in tags:
            # Remove OpenPype Note from note field
            if not "openpypeData" in tag.name():
                note = tag.note()
                if len(note) > 0:
                    notes += tag.note() + ', '

        return notes[:-2]

    def getData(self, row, column, item):
        """Return the data in a cell"""
        currentColumn = self.gCustomColumnList[column]
        if currentColumn["name"] == "Tags":
            return self.getTagsString(item)

        if currentColumn["name"] == "Colorspace":
            colTransform = item.sourceMediaColourTransform()
            try:
                colTransform = item.sourceMediaColourTransform()
            except:
                colTransform = "--"
            return colTransform

        if currentColumn["name"] == "Notes":
            try:
                note = self.getNotes(item)
            except:
                note = ""
            return note

        if currentColumn["name"] == "FileType":
            fileType = "--"
            M = item.source().mediaSource().metadata()
            if M.hasKey("foundry.source.type"):
                fileType = M.value("foundry.source.type")
            elif M.hasKey("media.input.filereader"):
                fileType = M.value("media.input.filereader")
            return fileType

        if currentColumn["name"] == "MediaType":
            M = item.mediaType()
            return str(M).split("MediaType")[-1].replace(".k", "")

        if currentColumn["name"] == "WidthxHeight":
            return f"{str(item.source().format().width())}x{str(item.source().format().height())}"

        if currentColumn["name"] == "Episode":
            return get_track_item_episode(item.name()) or "--"

        if currentColumn["name"] == "Sequence":
            return get_track_item_sequence(item.name()) or "--"

        if currentColumn["name"] == "Shot":
            return get_track_item_shot(item.name()) or "--"

        if currentColumn["name"] == "Pixel Aspect":
            return str(item.source().format().pixelAspect())

        if currentColumn["name"] == "Artist":
            if item.artist():
                name = item.artist()["artistName"]
                return name
            else:
                return "--"

        if currentColumn["name"] == "Department":
            if item.artist():
                dep = item.artist()["artistDepartment"]
                return dep
            else:
                return "--"

        elif currentColumn["name"] in ["cut_in", "head_handles", "tail_handles"]:
            tag_key = currentColumn["name"]
            current_tag_text = item.cut_tag().get(f"tag.{tag_key}", "--")

            return current_tag_text

        elif currentColumn["name"] in [
                "op_frame_start",
                "op_family",
                "op_handle_end",
                "op_handle_start"
            ]:
            instance_key = currentColumn["name"]
            current_tag_text = item.openpype_instance().get(f"{instance_key.split('op_')[-1]}", '--')

            return current_tag_text

        return ""

    def setData(self, row, column, item, data):
        """Set the data in a cell - unused in this example"""

        return None

    def getTooltip(self, row, column, item):
        """Return the tooltip for a cell"""
        currentColumn = self.gCustomColumnList[column]
        if currentColumn["name"] == "Tags":
            return str([item.name() for item in item.tags()])

        if currentColumn["name"] == "Notes":
            return str(self.getNotes(item))
        return ""

    def getFont(self, row, column, item):
        """Return the tooltip for a cell"""
        return None

    def getBackground(self, row, column, item):
        """Return the background color for a cell"""
        if not item.source().mediaSource().isMediaPresent():
            return QColor(80, 20, 20)
        return None

    def getForeground(self, row, column, item):
        """Return the text color for a cell"""
        #if column == 1:
        #  return QColor(255, 64, 64)
        return None

    def getIcon(self, row, column, item):
        """Return the icon for a cell"""
        currentColumn = self.gCustomColumnList[column]
        if currentColumn["name"] == "Colorspace":
            return QIcon("icons:LUT.png")

        if currentColumn["name"] == "Shot Status":
            status = item.status()
            if status:
                return QIcon(gStatusTags[status])

        if currentColumn["name"] == "MediaType":
            mediaType = item.mediaType()
            if mediaType == hiero.core.TrackItem.kVideo:
                return QIcon("icons:VideoOnly.png")
            elif mediaType == hiero.core.TrackItem.kAudio:
                return QIcon("icons:AudioOnly.png")

        if currentColumn["name"] == "Artist":
            try:
                return QIcon(item.artist()["artistIcon"])
            except:
                return None
        return None

    def getSizeHint(self, row, column, item):
        """Return the size hint for a cell"""
        currentColumnName = self.gCustomColumnList[column]["name"]

        # if currentColumnName == "Thumbnail":
        #     return QSize(90, 50)

        # return QSize(50, 50)
        return None

    def paintCell(self, row, column, item, painter, option):
        """Paint a custom cell. Return True if the cell was painted, or False
        to continue with the default cell painting.
        """
        # Probably will have no need for paintCell.
        currentColumn = self.gCustomColumnList[column]
        if currentColumn["name"] == "Tags":
            if option.state & QStyle.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())
            iconSize = 20
            r = QRect(option.rect.x(),
                      option.rect.y() + (option.rect.height() - iconSize) / 2,
                      iconSize, iconSize)
            tags = item.tags()
            if len(tags) > 0:
                painter.save()
                painter.setClipRect(option.rect)
                for tag in item.tags():
                    M = tag.metadata()
                    if not (M.hasKey("tag.status")
                            or M.hasKey("tag.artistID")):
                        QIcon(tag.icon()).paint(painter, r, Qt.AlignLeft)
                        r.translate(r.width() + 2, 0)
                painter.restore()
                return True

        # if currentColumn["name"] == "Thumbnail":
        #     imageView = None
        #     pen = QPen()
        #     r = QRect(option.rect.x() + 2, (option.rect.y() +
        #                                     (option.rect.height() - 46) / 2),
        #               85, 46)
        #     if not item.source().mediaSource().isMediaPresent():
        #         imageView = QImage("icons:Offline.png")
        #         pen.setColor(QColor(Qt.red))

        #     if item.mediaType() == hiero.core.TrackItem.MediaType.kAudio:
        #         imageView = QImage("icons:AudioOnly.png")
        #         #pen.setColor(QColor(Qt.green))
        #         painter.fillRect(r, QColor(45, 59, 45))

        #     if option.state & QStyle.State_Selected:
        #         painter.fillRect(option.rect, option.palette.highlight())

        #     tags = item.tags()
        #     painter.save()
        #     painter.setClipRect(option.rect)

        #     if not imageView:
        #         try:
        #             imageView = item.thumbnail(item.sourceIn())
        #             pen.setColor(QColor(20, 20, 20))
        #         # If we're here, we probably have a TC error, no thumbnail, so get it from the source Clip...
        #         except:
        #             pen.setColor(QColor(Qt.red))

        #     if not imageView:
        #         try:
        #             imageView = item.source().thumbnail()
        #             pen.setColor(QColor(Qt.yellow))
        #         except:
        #             imageView = QImage("icons:Offline.png")
        #             pen.setColor(QColor(Qt.red))

        #     QIcon(QPixmap.fromImage(imageView)).paint(painter, r,
        #                                               Qt.AlignCenter)
        #     painter.setPen(pen)
        #     painter.drawRoundedRect(r, 1, 1)
        #     painter.restore()
        #     return True

        return False

    def createEditor(self, row, column, item, view):
        """Create an editing widget for a custom cell"""
        self.currentView = view
        currentColumn = self.gCustomColumnList[column]

        if currentColumn["cellType"] == "readonly":
            # readonly is done by removing visiblity and useability of the
            # returned widget to the widget viewer
            edit_widget = QLabel()
            edit_widget.setEnabled(False)
            edit_widget.setVisible(False)

            return edit_widget

        elif currentColumn["name"] == "Colorspace":
            ocio_config = get_active_ocio_config()
            edit_widget = Colorspace_Widget(ocio_config)
            edit_widget.root_menu.triggered.connect(self.colorspaceChanged)
            return edit_widget

        elif currentColumn["name"] in ["cut_in", "head_handles", "tail_handles"]:
            tag_key = currentColumn["name"]
            current_text = item.cut_tag().get(f"tag.{tag_key}")
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(tag_key)
            edit_widget.returnPressed.connect(self.cut_info_changed)

            return edit_widget

        elif currentColumn["name"] in [
                "op_frame_start",
                "op_family",
                "op_handle_end",
                "op_handle_start"
            ]:

            instance_key = currentColumn["name"].split('op_')[-1]
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
                item for item in selection
                if (item.mediaType() == hiero.core.TrackItem.MediaType.kVideo)
            ]
            for trackItem in items:
                trackItem.setSourceMediaColourTransform(colorspace)

    def cut_info_changed(self):
        sender = self.sender()
        key = sender.objectName()
        value = sender.text()

        # Only pass on edit if user unintentially erased value from column
        if value not in ["--", ""] and not value.isdigit():
            return
        else:
            # Remove preceding zeros
            value = value.strip().lstrip("0")

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
                track_itemTags = track_item.tags()
                for tag in track_itemTags:
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
        value = sender.text()

        view = hiero.ui.activeView()
        selection = view.selection()
        if value.strip() == "":
            return
        else:
            for track_item in selection:
                track_item.set_openpype_instance({key:value})



    def statusChanged(self, arg):
        """This method is called when Shot Status widget changes index."""
        view = hiero.ui.activeView()
        selection = view.selection()
        status = self.sender().currentText()
        project = selection[0].project()
        with project.beginUndo("Set Status"):
            # A string of "--" characters denotes clear the status
            if status != "--":
                for trackItem in selection:
                    trackItem.setStatus(status)
            else:
                for trackItem in selection:
                    tTags = trackItem.tags()
                    for tag in tTags:
                        if tag.metadata().hasKey("tag.status"):
                            trackItem.removeTag(tag)
                            break

    def artistNameChanged(self, arg):
        """This method is called when Artist widget changes index."""
        view = hiero.ui.activeView()
        selection = view.selection()
        name = self.sender().currentText()
        project = selection[0].project()
        with project.beginUndo("Assign Artist"):
            # A string of "--" denotes clear the assignee...
            if name != "--":
                for trackItem in selection:
                    trackItem.setArtistByName(name)
            else:
                for trackItem in selection:
                    tTags = trackItem.tags()
                    for tag in tTags:
                        if tag.metadata().hasKey("tag.artistID"):
                            trackItem.removeTag(tag)
                            break


def create_unique_tag(tag_name):
    """hiero.core.Tag object will load metadata from previously created tag if
    the string arg is the same as a previously created tag. In order to ensure
    that the metadata is empty on creation this random number is added and then
    removed after creation

    Double check the stability of this approach. May cause Hiero crashes
    """
    unique_tag_number = random.randint(99999999, 1000000000)
    tag = hiero.core.Tag(f"{tag_name} {unique_tag_number}")
    tag.metadata().setValue('tag.label', tag_name)
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
        cut_tag.metadata().setValue('tag.label', "Cut Info")
        cut_tag.setName("Cut Info")

        # Have yet to find icon for cut info
        cut_tag.setIcon("icons:TagKeylight.png")
        # Do i need this duplicate code?
        cut_tag.metadata().setValue(f"tag.{key}", value)
        self.sequence().editFinished()
        self.addTag(cut_tag)
        self.sequence().editFinished()
        return

    # Have yet to find icon for cut info
    # cut_tag.setIcon(gcut_tags[status])
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


# Inject cut tag getter and setter methods into hiero.core.TrackItem
hiero.core.TrackItem.set_cut_tag = _set_cut_tag
hiero.core.TrackItem.cut_tag = _cut_tag


def default_handle_length():
    return 10


def get_shot_entities(shot_name):
    episode = ""
    sequence = ""
    parents = get_asset_parents(shot_name)
    # Parents will always start with shots or assets
    folder = parents[0]
    shot = shot_name
    if folder == "shots":
        if len(parents) > 1:
            sequence = parents[-1]
        if len(parents) > 2:
            episode = parents[-2]
        if len(parents) == 4:
            season = parents[-3]

    shot_entities = {"folder": folder, "episode": episode, "sequence": sequence, "shot": shot}

    return shot_entities


def get_hierarchy_data(shot_name, track_name):
    hierarchy_data = get_shot_entities(shot_name)
    hierarchy_data["track"] = track_name

    return hierarchy_data


def get_hierarchy_path(hierarchy_data):
    # Don't change ui_inputs - this is needed for next iterations
    if hierarchy_data.get("season"):
        new_subpath = "{folder}/{season}/{episode}/{sequence}"
    elif hierarchy_data.get("episode"):
        new_subpath = "{folder}/{episode}/{sequence}"
    elif hierarchy_data.get("sequence"):
        new_subpath = "{folder}/{sequence}"
    else:
        new_subpath = "{folder}"

    hierarchy_path = new_subpath.format(**hierarchy_data)

    return hierarchy_path


def get_hierarchy_parents(hierarchy_data):
    parents = []
    parents_types = ["folder", "episode", "sequence"]
    for key, value in hierarchy_data.items():
        if key in parents_types:
            entity = {"entity_type" : key, "entity_name" : value}
            parents.append(entity)

    return parents


def _set_openpype_instance(self, new_data):
    """
    """

    if not new_data:
        return

    frame_start = new_data.get("frame_start", "0")
    handle_start = new_data.get("handle_start", "0")
    handle_end = new_data.get("handle_end", "0")

    # Validate values
    if new_data.get("family", "plate") not in ["plate", "reference"]:
        print('retuened family')
        return

    if not (frame_start.isdigit() and handle_start.isdigit() and handle_end.isdigit()):
        print('returned isdigit')
        return

    convert_keys = {
        "frame_start" : "workfileFrameStart",
        "handle_start" : "handleStart",
        "handle_end" : "handleEnd",
    }
    # Convert data from column names into OP instance names
    converted_new_data = {}
    for key, value in new_data.items():
        if key in convert_keys:
            converted_new_data[convert_keys[key]] = value
            # new_data[convert_keys[key]] = value
        else:
            converted_new_data[key] = value

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
    asset_name = get_track_item_shot(track_item_name)
    # Check if asset has valid name
    # if not don't create instance
    if not asset_name:
        if instance_tag:
            print("Tag tack name no longer valid. Removing Openpype tag")
            self.removeTag(instance_tag)
        else:
            print("Track item name not found in DB!")

        return

    instance_data = {}
    if not instance_tag:
        # First fill default instance if no tag found and then update with data perameter
        if "ref" in track_name:
            family = "reference"
        else:
            family = "plate"

        handle = default_handle_length()

        instance_data["family"] = family
        instance_data["handleEnd"] = handle
        instance_data["handleStart"] = handle
        # instance_data["label"] = openpypeData_d88a8ae8

        # Constents
        instance_data["audio"] = "False"
        instance_data["heroTrack"] = "True"
        instance_data["families"] = "['clip']"
        instance_data["id"] = "pyblish.avalon.instance"
        # instance_data["note"] = "OpenPype data container"
        instance_data["publish"] = "True"
        instance_data["reviewTrack"] = "None"
        instance_data["sourceResolution"] = "False"
        instance_data["variant"] = "Main"
        instance_data["workfileFrameStart"] = "1001"

    # This force update on change is not working. need to register as columns
    # Always update for name changes and such
    hierarchy_data = get_hierarchy_data(track_item_name, track_name)
    hierarchy_path = get_hierarchy_path(hierarchy_data)
    hierarchy_parents = get_hierarchy_parents(hierarchy_data)

    instance_data["hierarchyData"] = hierarchy_data
    instance_data["hierarchy"] = hierarchy_path
    instance_data["parents"] = hierarchy_parents
    instance_data["asset"] = get_track_item_shot(track_item_name)
    hierarchy_data["track"] = track_name
    instance_data["subset"] = track_name

    instance_data.update(converted_new_data)

    set_trackitem_openpype_tag(self, instance_data)


def _openpype_instance(self):
    instance_tag = get_trackitem_openpype_tag(self)
    instance_data = {}
    if instance_tag:
        tag_data = instance_tag.metadata().dict()
        # Convert data from column names into OP instance names
        convert_keys = {
            "tag.workfileFrameStart" : "frame_start",
            "tag.handleStart" : "handle_start",
            "tag.handleEnd" : "handle_end",
        }
        for key, value in tag_data.items():

            if key in convert_keys:
                instance_data[convert_keys[key]] = value
            else:
                instance_data[key.split("tag.")[-1]] = value

    return instance_data


hiero.core.TrackItem.set_openpype_instance = _set_openpype_instance
hiero.core.TrackItem.openpype_instance = _openpype_instance


# Register our custom columns
hiero.ui.customColumn = CustomSpreadsheetColumns()
