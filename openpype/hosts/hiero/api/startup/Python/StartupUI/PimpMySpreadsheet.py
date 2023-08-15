import bisect
import glob
import hiero
import os
import PyOpenColorIO
from qtpy import QtWidgets *
from qtpy import QtCore *


from openpype.client import get_asset_by_name
from openpype.pipeline.context_tools import get_current_project_name


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
        return "--"
    else:

        return track_item_name


def get_track_item_episode(track_item_name):
    """Validate if shot exists in DB and if so return episode name"""
    episode = ""

    parents = get_asset_parents(track_item_name)
    if parents is None:
        return "--"

    # Parents will always start with shots or assets
    folder = parents[0]
    if folder == "shots":
        if len(parents) > 2:
            episode = parents[-2]

            return episode

    return "--"


def get_track_item_sequence(track_item_name):
    """
    Validate if shot exists in DB and if so return sequence name
    """
    sequence = ""
    parents = get_asset_parents(track_item_name)
    if parents is None:

        return "--"

    # Parents will always start with shots or assets
    folder = parents[0]
    if folder == "shots":
        if len(parents) > 1:
            sequence = parents[-1]

            return sequence

    return "--"


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
    # c implies QEditText
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
            "name": "Cut In",
            "cellType": "text"
        },
        {
            "name": "Head Handles",
            "cellType": "text"
        },
        {
            "name": "Tail Handles",
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
        print(column, 'column getData')
        print(item, 'item getData')
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

        if currentColumn["name"] == "Shot Status":
            status = item.status()
            if not status:
                status = "--"
            return str(status)

        if currentColumn["name"] == "MediaType":
            M = item.mediaType()
            return str(M).split("MediaType")[-1].replace(".k", "")

        # if currentColumn["name"] == "Thumbnail":
        #     return str(item.eventNumber())

        if currentColumn["name"] == "WidthxHeight":
            return f"{str(item.source().format().width())}x{str(item.source().format().height())}"

        if currentColumn["name"] == "Episode":
            return get_track_item_episode(item.name())

        if currentColumn["name"] == "Sequence":
            return get_track_item_sequence(item.name())

        if currentColumn["name"] == "Shot":
            return get_track_item_shot(item.name())

        # if currentColumn["name"] == "Height":
        #     return str(item.source().format().height())

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


        elif currentColumn["name"] == "Cut In":
            current_tag_text = item.cut_tag["cut_in"]
            if current_tag_text:
                return current_tag_text
            else:
                return "--"

        elif currentColumn["name"] == "Head Handles":
            current_text = item.cut_tag["head_handles"]
            if current_tag_text:
                return current_tag_text
            else:
                return "--"

        elif currentColumn["name"] == "Tail Handles":
            current_text = item.cut_tag["tail_handles"]
            if current_tag_text:
                return current_tag_text
            else:
                return "--"

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
        print(column, 'column')
        print(view, 'view')
        print(item, 'item')

        # Have option to set default widgeteditor option?

        if currentColumn["cellType"] == "readonly":
            # readonly is done by removing visiblity and useability of the
            # returned widget to the widget viewer
            edit_widget = QLabel()
            edit_widget.setEnabled(False)
            edit_widget.setVisible(False)

            return edit_widget

        elif currentColumn["name"] == "Colorspace":
            self.gColorSpaces
            ocio_config = get_active_ocio_config()
            edit_widget = Colorspace_Widget(ocio_config)
            edit_widget.root_menu.triggered.connect(self.colorspaceChanged)
            return edit_widget

        elif currentColumn["name"] == "Cut In":
            current_text = item.cut_tag["cut_in"]
            edit_widget = QEditText(current_text)
            edit_widget.setObjectName("cut_in")
            edit_widget.editFinished.connect(self.cut_info_changed)

            return edit_widget

        elif currentColumn["name"] == "Head Handles":
            current_text = item.cut_tag["head_handles"]
            edit_widget = QEditText(current_text)
            edit_widget.setObjectName("head_handles")
            edit_widget.editFinished.connect(self.cut_info_changed)

            return edit_widget

        elif currentColumn["name"] == "Tail Handles":
            current_text = item.cut_tag["tail_handles"]
            edit_widget = QEditText(current_text)
            edit_widget.setObjectName("tail_handles")
            edit_widget.editFinished.connect(self.cut_info_changed)

            return edit_widget

        # if currentColumn["name"] == "Colorspace":
        #     cb = QComboBox()
        #     for colorspace in self.gColorSpaces:
        #         cb.addItem(colorspace)
        #     cb.currentIndexChanged.connect(self.colorspaceChanged)
        #     return cb

        # if currentColumn["name"] == "Shot Status":
        #     cb = QComboBox()
        #     cb.addItem("")
        #     for key in gStatusTags.keys():
        #         cb.addItem(QIcon(gStatusTags[key]), key)
        #     cb.addItem("--")
        #     cb.currentIndexChanged.connect(self.statusChanged)

        #     return cb

        # if currentColumn["name"] == "Artist":
        #     cb = QComboBox()
        #     cb.addItem("")
        #     for artist in gArtistList:
        #         cb.addItem(artist["artistName"])
        #     cb.addItem("--")
        #     cb.currentIndexChanged.connect(self.artistNameChanged)
        #     return cb

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

    # def colorspaceChanged(self, index):
    #     """
    #   This method is called when Colorspace widget changes index.
    # """
    #     index = self.sender().currentIndex()
    #     colorspace = self.gColorSpaces[index]
    #     selection = self.currentView.selection()
    #     project = selection[0].project()
    #     with project.beginUndo("Set Colorspace"):
    #         items = [
    #             item for item in selection
    #             if (item.mediaType() == hiero.core.TrackItem.MediaType.kVideo)
    #         ]
    #         for trackItem in items:
    #             trackItem.setSourceMediaColourTransform(colorspace)

    def cut_info_changed(self, widget):
        widget.objectName()

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

def _set_cut_tag(self, key, value):
    # Cut tag can be set from a variety of columns
    # Need to logic for each case
    tags = self.tags()
    cut_tag = {}
    for tag in tags:
        if not tag.name() == "Cut Info":
            continue

        cut_tag = dict(tag.metadata())
        break

    if not cut_tag:
        cut_tag = hiero.core.Tag("Cut Info")
        # Have yet to find icon for cut info
        # cut_tag.setIcon(gcut_tags[status])
        # Do i need this duplicate code?
        # cut_tag.metadata().setValue(f"tag.{key}", value)
        self.addTag(cut_tag)

    # Have yet to find icon for cut info
    # cut_tag.setIcon(gcut_tags[status])
    cut_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()
    return


def _cut_tag(self):
    tags = self.tags()
    tag = {}
    for tag in tags:
        if not tag.name() == "cut_info":
            continue

        tag = dict(tag.metadata())
        break

    return tag


# Inject cut tag getter and setter methods into hiero.core.TrackItem
hiero.core.TrackItem.set_cut_tag = _set_cut_tag
hiero.core.TrackItem.cut_tag = _cut_tag



def _getArtistFromID(self, artistID):
    """getArtistFromID -> returns an artist dictionary, by their given ID"""
    global gArtistList
    artist = [
        element for element in gArtistList
        if element["artistID"] == int(artistID)
    ]
    if not artist:
        return None

    return artist[0]


def _getArtistFromName(self, artistName):
    """getArtistFromID -> returns an artist dictionary, by their given ID """
    global gArtistList
    artist = [
        element for element in gArtistList
        if element["artistName"] == artistName
    ]
    if not artist:
        return None
    return artist[0]


def _artist(self):
    """_artist -> Returns the artist dictionary assigned to this shot"""
    artist = None
    tags = self.tags()
    for tag in tags:
        if tag.metadata().hasKey("tag.artistID"):
            artistID = tag.metadata().value("tag.artistID")
            artist = self.getArtistFromID(artistID)
    return artist


def _updateArtistTag(self, artistDict):
    # A shot will only have one artist assigned. Check if one exists and set accordingly

    artistTag = None
    tags = self.tags()
    for tag in tags:
        if tag.metadata().hasKey("tag.artistID"):
            artistTag = tag
            break

    if not artistTag:
        artistTag = hiero.core.Tag("Artist")
        artistTag.setIcon(artistDict["artistIcon"])
        artistTag.metadata().setValue("tag.artistID",
                                      str(artistDict["artistID"]))
        artistTag.metadata().setValue("tag.artistName",
                                      str(artistDict["artistName"]))
        artistTag.metadata().setValue("tag.artistDepartment",
                                      str(artistDict["artistDepartment"]))
        self.sequence().editFinished()
        self.addTag(artistTag)
        self.sequence().editFinished()
        return

    artistTag.setIcon(artistDict["artistIcon"])
    artistTag.metadata().setValue("tag.artistID", str(artistDict["artistID"]))
    artistTag.metadata().setValue("tag.artistName",
                                  str(artistDict["artistName"]))
    artistTag.metadata().setValue("tag.artistDepartment",
                                  str(artistDict["artistDepartment"]))
    self.sequence().editFinished()
    return


def _setArtistByName(self, artistName):
    """setArtistByName(artistName) -> sets the artist tag on a TrackItem by a given artistName string"""
    global gArtistList

    artist = self.getArtistFromName(artistName)
    if not artist:
        print((
            "Artist name: {} was not found in "
            "the gArtistList.").format(artistName))
        return

    # Do the update.
    self.updateArtistTag(artist)


def _setArtistByID(self, artistID):
    """setArtistByID(artistID) -> sets the artist tag on a TrackItem by a given artistID integer"""
    global gArtistList

    artist = self.getArtistFromID(artistID)
    if not artist:
        print("Artist name: {} was not found in the gArtistList.".format(
            artistID))
        return

    # Do the update.
    self.updateArtistTag(artist)


# Inject status getter and setter methods into hiero.core.TrackItem
hiero.core.TrackItem.artist = _artist
hiero.core.TrackItem.setArtistByName = _setArtistByName
hiero.core.TrackItem.setArtistByID = _setArtistByID
hiero.core.TrackItem.getArtistFromName = _getArtistFromName
hiero.core.TrackItem.getArtistFromID = _getArtistFromID
hiero.core.TrackItem.updateArtistTag = _updateArtistTag


def _status(self):
    """status -> Returns the Shot status. None if no Status is set."""

    status = None
    tags = self.tags()
    for tag in tags:
        if tag.metadata().hasKey("tag.status"):
            status = tag.metadata().value("tag.status")
    return status


def _setStatus(self, status):
    """setShotStatus(status) -> Method to set the Status of a Shot.
  Adds a special kind of status Tag to a TrackItem
  Example: myTrackItem.setStatus("Final")

  @param status - a string, corresponding to the Status name
  """
    global gStatusTags

    # Get a valid Tag object from the Global list of statuses
    if not status in gStatusTags.keys():
        print("Status requested was not a valid Status string.")
        return

    # A shot should only have one status. Check if one exists and set accordingly
    statusTag = None
    tags = self.tags()
    for tag in tags:
        if tag.metadata().hasKey("tag.status"):
            statusTag = tag
            break

    if not statusTag:
        statusTag = hiero.core.Tag("Status")
        statusTag.setIcon(gStatusTags[status])
        statusTag.metadata().setValue("tag.status", status)
        self.addTag(statusTag)

    statusTag.setIcon(gStatusTags[status])
    statusTag.metadata().setValue("tag.status", status)

    self.sequence().editFinished()
    return


# Inject status getter and setter methods into hiero.core.TrackItem
hiero.core.TrackItem.setStatus = _setStatus
hiero.core.TrackItem.status = _status


# This is a convenience method for returning QActions with a triggered method based on the title string
def titleStringTriggeredAction(title, method, icon=None):
    action = QAction(title, None)
    action.setIcon(QIcon(icon))

    # We do this magic, so that the title string from the action is used to set the status
    def methodWrapper():
        method(title)

    action.triggered.connect(methodWrapper)
    return action


# Menu which adds a Set Status Menu to Timeline and Spreadsheet Views
class SetStatusMenu(QMenu):
    def __init__(self):
        QMenu.__init__(self, "Set Status", None)

        global gStatusTags
        self.statuses = gStatusTags
        self._statusActions = self.createStatusMenuActions()

        # Add the Actions to the Menu.
        for act in self.menuActions:
            self.addAction(act)

        hiero.core.events.registerInterest("kShowContextMenu/kTimeline",
                                           self.eventHandler)
        hiero.core.events.registerInterest("kShowContextMenu/kSpreadsheet",
                                           self.eventHandler)

    def createStatusMenuActions(self):
        self.menuActions = []
        for status in self.statuses:
            self.menuActions += [
                titleStringTriggeredAction(
                    status,
                    self.setStatusFromMenuSelection,
                    icon=gStatusTags[status])
            ]

    def setStatusFromMenuSelection(self, menuSelectionStatus):
        selectedShots = [
            item for item in self._selection
            if (isinstance(item, hiero.core.TrackItem))
        ]
        selectedTracks = [
            item for item in self._selection
            if (isinstance(item, (hiero.core.VideoTrack,
                                  hiero.core.AudioTrack)))
        ]

        # If we have a Track Header Selection, no shots could be selected, so create shotSelection list
        if len(selectedTracks) >= 1:
            for track in selectedTracks:
                selectedShots += [
                    item for item in track.items()
                    if (isinstance(item, hiero.core.TrackItem))
                ]

        # It's possible no shots exist on the Track, in which case nothing is required
        if len(selectedShots) == 0:
            return

        currentProject = selectedShots[0].project()

        with currentProject.beginUndo("Set Status"):
            # Shots selected
            for shot in selectedShots:
                shot.setStatus(menuSelectionStatus)

    # This handles events from the Project Bin View
    def eventHandler(self, event):
        if not hasattr(event.sender, "selection"):
            # Something has gone wrong, we should only be here if raised
            # by the Timeline/Spreadsheet view which gives a selection.
            return

        # Set the current selection
        self._selection = event.sender.selection()

        # Return if there's no Selection. We won't add the Menu.
        if len(self._selection) == 0:
            return

        event.menu.addMenu(self)


# Menu which adds a Set Status Menu to Timeline and Spreadsheet Views
class AssignArtistMenu(QMenu):
    def __init__(self):
        QMenu.__init__(self, "Assign Artist", None)

        global gArtistList
        self.artists = gArtistList
        self._artistsActions = self.createAssignArtistMenuActions()

        # Add the Actions to the Menu.
        for act in self.menuActions:
            self.addAction(act)

        hiero.core.events.registerInterest("kShowContextMenu/kTimeline",
                                           self.eventHandler)
        hiero.core.events.registerInterest("kShowContextMenu/kSpreadsheet",
                                           self.eventHandler)

    def createAssignArtistMenuActions(self):
        self.menuActions = []
        for artist in self.artists:
            self.menuActions += [
                titleStringTriggeredAction(
                    artist["artistName"],
                    self.setArtistFromMenuSelection,
                    icon=artist["artistIcon"])
            ]

    def setArtistFromMenuSelection(self, menuSelectionArtist):
        selectedShots = [
            item for item in self._selection
            if (isinstance(item, hiero.core.TrackItem))
        ]
        selectedTracks = [
            item for item in self._selection
            if (isinstance(item, (hiero.core.VideoTrack,
                                  hiero.core.AudioTrack)))
        ]

        # If we have a Track Header Selection, no shots could be selected, so create shotSelection list
        if len(selectedTracks) >= 1:
            for track in selectedTracks:
                selectedShots += [
                    item for item in track.items()
                    if (isinstance(item, hiero.core.TrackItem))
                ]

        # It's possible no shots exist on the Track, in which case nothing is required
        if len(selectedShots) == 0:
            return

        currentProject = selectedShots[0].project()

        with currentProject.beginUndo("Assign Artist"):
            # Shots selected
            for shot in selectedShots:
                shot.setArtistByName(menuSelectionArtist)

    # This handles events from the Project Bin View
    def eventHandler(self, event):
        if not hasattr(event.sender, "selection"):
            # Something has gone wrong, we should only be here if raised
            # by the Timeline/Spreadsheet view which gives a selection.
            return

        # Set the current selection
        self._selection = event.sender.selection()

        # Return if there's no Selection. We won't add the Menu.
        if len(self._selection) == 0:
            return

        event.menu.addMenu(self)


# Add the "Set Status" context menu to Timeline and Spreadsheet
# if kAddStatusMenu:
#     setStatusMenu = SetStatusMenu()

# if kAssignArtistMenu:
#     assignArtistMenu = AssignArtistMenu()

# Register our custom columns
hiero.ui.customColumn = CustomSpreadsheetColumns()
