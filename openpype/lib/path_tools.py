import os
import re
import glob
import logging
import platform

import clique

log = logging.getLogger(__name__)


def format_file_size(file_size, suffix=None):
    """Returns formatted string with size in appropriate unit.

    Args:
        file_size (int): Size of file in bytes.
        suffix (str): Suffix for formatted size. Default is 'B' (as bytes).

    Returns:
        str: Formatted size using proper unit and passed suffix (e.g. 7 MiB).
    """

    if suffix is None:
        suffix = "B"

    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(file_size) < 1024.0:
            return "%3.1f%s%s" % (file_size, unit, suffix)
        file_size /= 1024.0
    return "%.1f%s%s" % (file_size, "Yi", suffix)


def create_hard_link(src_path, dst_path):
    """Create hardlink of file.

    Args:
        src_path(str): Full path to a file which is used as source for
            hardlink.
        dst_path(str): Full path to a file where a link of source will be
            added.
    """
    # Use `os.link` if is available
    #   - should be for all platforms with newer python versions
    if hasattr(os, "link"):
        os.link(src_path, dst_path)
        return

    # Windows implementation of hardlinks
    #   - used in Python 2
    if platform.system().lower() == "windows":
        import ctypes
        from ctypes.wintypes import BOOL
        CreateHardLink = ctypes.windll.kernel32.CreateHardLinkW
        CreateHardLink.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p
        ]
        CreateHardLink.restype = BOOL

        res = CreateHardLink(dst_path, src_path, None)
        if res == 0:
            raise ctypes.WinError()
        return
    # Raises not implemented error if gets here
    raise NotImplementedError(
        "Implementation of hardlink for current environment is missing."
    )


def collect_frames(files):
    """Returns dict of source path and its frame, if from sequence

    Uses clique as most precise solution, used when anatomy template that
    created files is not known.

    Assumption is that frames are separated by '.', negative frames are not
    allowed.

    Args:
        files(list) or (set with single value): list of source paths

    Returns:
        (dict): {'/asset/subset_v001.0001.png': '0001', ....}
    """

    patterns = [clique.PATTERNS["frames"]]
    collections, remainder = clique.assemble(
        files, minimum_items=1, patterns=patterns)

    sources_and_frames = {}
    if collections:
        for collection in collections:
            src_head = collection.head
            src_tail = collection.tail

            for index in collection.indexes:
                src_frame = collection.format("{padding}") % index
                src_file_name = "{}{}{}".format(
                    src_head, src_frame, src_tail)
                sources_and_frames[src_file_name] = src_frame
    else:
        sources_and_frames[remainder.pop()] = None

    return sources_and_frames


def _rreplace(s, a, b, n=1):
    """Replace a with b in string s from right side n times."""
    return b.join(s.rsplit(a, n))


def version_up(filepath):
    """Version up filepath to a new non-existing version.

    Parses for a version identifier like `_v001` or `.v001`
    When no version present _v001 is appended as suffix.

    Args:
        filepath (str): full url

    Returns:
        (str): filepath with increased version number

    """
    dirname = os.path.dirname(filepath)
    basename, ext = os.path.splitext(os.path.basename(filepath))

    regex = r"[._]v\d+"
    matches = re.findall(regex, str(basename), re.IGNORECASE)
    if not matches:
        log.info("Creating version...")
        new_label = "_v{version:03d}".format(version=1)
        new_basename = "{}{}".format(basename, new_label)
    else:
        label = matches[-1]
        version = re.search(r"\d+", label).group()
        padding = len(version)

        new_version = int(version) + 1
        new_version = '{version:0{padding}d}'.format(version=new_version,
                                                     padding=padding)
        new_label = label.replace(version, new_version, 1)
        new_basename = _rreplace(basename, label, new_label)
    new_filename = "{}{}".format(new_basename, ext)
    new_filename = os.path.join(dirname, new_filename)
    new_filename = os.path.normpath(new_filename)

    if new_filename == filepath:
        raise RuntimeError("Created path is the same as current file,"
                           "this is a bug")

    # We check for version clashes against the current file for any file
    # that matches completely in name up to the {version} label found. Thus
    # if source file was test_v001_test.txt we want to also check clashes
    # against test_v002.txt but do want to preserve the part after the version
    # label for our new filename
    clash_basename = new_basename
    if not clash_basename.endswith(new_label):
        index = (clash_basename.find(new_label))
        index += len(new_label)
        clash_basename = clash_basename[:index]

    for file in os.listdir(dirname):
        if file.endswith(ext) and file.startswith(clash_basename):
            log.info("Skipping existing version %s" % new_label)
            return version_up(new_filename)

    log.info("New version %s" % new_label)
    return new_filename


def get_version_from_path(file):
    """Find version number in file path string.

    Args:
        file (str): file path

    Returns:
        str: version number in string ('001')
    """

    pattern = re.compile(r"[\._]v([0-9]+)", re.IGNORECASE)
    try:
        return pattern.findall(file)[-1]
    except IndexError:
        log.error(
            "templates:get_version_from_workfile:"
            "`{}` missing version string."
            "Example `v004`".format(file)
        )


def get_last_version_from_path(path_dir, filter):
    """Find last version of given directory content.

    Args:
        path_dir (str): directory path
        filter (list): list of strings used as file name filter

    Returns:
        str: file name with last version

    Example:
        last_version_file = get_last_version_from_path(
            "/project/shots/shot01/work", ["shot01", "compositing", "nk"])
    """

    assert os.path.isdir(path_dir), "`path_dir` argument needs to be directory"
    assert isinstance(filter, list) and (
        len(filter) != 0), "`filter` argument needs to be list and not empty"

    filtred_files = list()

    # form regex for filtering
    pattern = r".*".join(filter)

    for file in os.listdir(path_dir):
        if not re.findall(pattern, file):
            continue
        filtred_files.append(file)

    if filtred_files:
        sorted(filtred_files)
        return filtred_files[-1]

    return None


#### Starts Alkemy-X Override ####

# Regular expression that allows us to replace the frame numbers of a file path
# with any string token
RE_FRAME_NUMBER = re.compile(
    r"(?P<prefix>\w+[\._])(?P<frame>(\*|%0?\d*d|\d|#)+)\.(?P<extension>\w+\.?(sc|gz)?$)"
)

# Regular expression that allows us to find the number of padding of a frame token
RE_FRAME_PADDING = re.compile(
    r"%0(\d+)d"
)


def get_padding_from_frame(frame_token):
    """Get number of padding given a frame token

    Examples:
        "1001" -> returns 4
        "001001" -> returns 6
        "%04d" -> returns 4
        "%08d" -> returns 8
    """
    padding_length = None
    try:
        _ = int(frame_token)
        padding_length = len(frame_token)
    except ValueError:
        # frame_token isn't an integer so it can't be converted
        # with int()
        match = RE_FRAME_PADDING.match(frame_token)
        if match:
            padding_length = int(match.group(1))

    return padding_length


def replace_frame_number_with_token(path, token, padding=False):
    """Replace the frame number of a file path with a token"""
    root, filename = os.path.split(path)
    if padding:
        frame_match = RE_FRAME_NUMBER.search(filename)
        if frame_match:
            frame_token = frame_match.group("frame")
            padding_length = get_padding_from_frame(frame_token)
            if padding_length:
                token = token * padding_length

    filename = RE_FRAME_NUMBER.sub(
        r"\g<prefix>{}.\g<extension>".format(token), filename
    )
    return os.path.join(root, filename)


def convert_to_sequence(file_path):
    """Convert file path to a sequence and return the sequence and frame range.
    """
    # Convert file path so it can be used with glob and find all the
    # frames for the sequence
    file_pattern = replace_frame_number_with_token(file_path, "*")

    representation_files = glob.glob(file_pattern)
    collections, remainder = clique.assemble(representation_files)

    ext = None
    frame_start = None
    frame_end = None

    # If file path is in remainder it means it was a single file
    if file_path in remainder:
        collections = [remainder]
        filename = os.path.basename(file_path)
        frame_match = RE_FRAME_NUMBER.match(filename)
        if frame_match:
            ext = frame_match.group("extension")
            frame = frame_match.group("frame")
            frame_start = frame
            frame_end = frame
        else:
            frame_start = 1
            frame_end = 1
            ext = os.path.splitext(file_path)[1][1:]

    elif not collections:
        log.warning(
            "Couldn't find a collection for file pattern '%s'.",
            file_pattern
        )
        return None, None, None, None

    if len(collections) > 1:
        log.warning(
            "More than one sequence found for the file pattern '%s'."
            " Using only first one: %s",
            file_pattern,
            collections,
        )
    collection = collections[0]

    if not ext:
        ext = collection.tail.lstrip(".")

    if not frame_start or not frame_end:
        frame_start = min(collection.indexes)
        frame_end = max(collection.indexes)

    return list(collection), ext, frame_start, frame_end

#### Ends Alkemy-X Override ####
