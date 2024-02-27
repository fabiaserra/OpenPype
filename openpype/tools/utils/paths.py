"""Util module for working with file paths"""
import re
import os
import glob
import clique

from openpype.lib import Logger


# Regular expression that allows us to replace the frame numbers of a file path
# with any string token
RE_FRAME_NUMBER = re.compile(
    r"(?P<prefix>\w+[\._])(?P<frame>(\*|%0?\d*d|\d)+)\.(?P<extension>\w+\.?(sc|gz)?$)"
)

# Regular expression that allows us to find the number of padding of a frame token
RE_FRAME_PADDING = re.compile(
    r"%0(\d+)d"
)

logger = Logger.get_logger(__name__)


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
        logger.warning(
            "Couldn't find a collection for file pattern '%s'.",
            file_pattern
        )
        return None, None, None, None

    if len(collections) > 1:
        logger.warning(
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
