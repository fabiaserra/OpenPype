import datetime


_debug = True

# Global Directories
PROJECTS_DIR = "/proj"
ARCHIVE_DIR = "/for_archive/ready_to_send"
EXPORT_DIR = "/pipe/data/automation/thanos"
SPACE_EXPORT = (
    f"{EXPORT_DIR}/new_archive/{datetime.date.today().strftime('%Y%m%d')}.json"
)

# Monitored Directories
FOLDER_ARCHIVE = "/for_archive/"
FOLDER_PROJECTS = "/proj/*"
FOLDER_REELS = "/reels/*"
FOLDER_ELEMENTS = "/assets/Element_Library/*"

FOLDER_TARGETS = [
    FOLDER_ARCHIVE,
    FOLDER_PROJECTS,
    FOLDER_REELS,
    FOLDER_ELEMENTS,
]
