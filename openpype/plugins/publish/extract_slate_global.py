import os
import re
import logging
import tempfile

import pyblish.api
try:
    from html2image import Html2Image
except ImportError:
    Html2Image = None

from openpype.lib import (
    get_oiio_tool_args,
    get_ffprobe_streams,
    get_chrome_tool_path,
    run_subprocess,
)
from openpype.pipeline import publish


# Could use opentimelineio.otio.opentime util functions but otio is not always
# available on all DCCs and it's tricky to install it due to its
# binary dependencies so because we only use it for timecode conversion it's
# easier to just implement it here.
def timecode_to_frames(timecode, framerate):
    def _seconds(value):
        if isinstance(value, str):
            _zip_ft = zip((3600, 60, 1, 1 / framerate), value.split(':'))
            return sum(f * float(t) for f, t in _zip_ft)
        elif isinstance(value, (int, float)):
            return value / framerate
        return 0

    def _frames(seconds):
        return seconds * framerate

    def tc_to_frames(_timecode, start=None):
        return _frames(_seconds(_timecode) - _seconds(start))

    if '+' in timecode:
        timecode = timecode.replace('+', ':')
    elif '#' in timecode:
        timecode = timecode.replace('#', ':')

    frames = int(round(tc_to_frames(timecode, start='00:00:00:00')))

    return frames


def frames_to_timecode(frames, framerate):
    return '{0:02d}:{1:02d}:{2:02d}:{3:02d}'.format(
        int(frames / (3600 * framerate)),
        int(frames / (60 * framerate) % 60),
        int(frames / framerate % 60),
        int(frames % framerate))


class SlateCreator:
    """Class that formats and renders an html template to use as a slate.

    Html renders as sRGB (or Rec.709 if you want a little gamma boost), so
    rendering will happen in .png as intermediate file, while then converted
    to dest cspace using oiio --colorconvert attributes syntax.

    """

    def __init__(
        self,
        template_path,
        resources_path="",
        staging_dir="",
        staging_subfolder="",
        data=None,
    ):
        self.data = data.copy() if data else {}
        self.log = logging.getLogger(self.__class__.__name__)
        self.set_template_paths(template_path, resources_path=resources_path)
        self.set_staging_dir(staging_dir, subfolder=staging_subfolder)
        self.read_template()
        self.task_filter = []

    def set_template_paths(self, template_path, resources_path=""):
        """Set path of HTML slate template file and CSS resources."""

        self.template_path = os.path.normpath(template_path)
        self.log.debug(
            "Using Slate template path: '%s'", self.template_path
        )

        if resources_path:
            self.resources_path = os.path.normpath(resources_path)
            self.log.debug(
                "Using Slate resources path: '%s'", self.resources_path
            )
        else:
            basedir = os.path.basename(self.template_path)
            self.resources_path = os.path.normpath(basedir)
            self.log.debug(
                "No Slate resources path specified, using: '%s'",
                self.resources_path
            )

    def set_resolution(self, width, height):
        self.data["resolution_width"] = width
        self.data["resolution_height"] = height
        self.log.debug("Slate resolution set to: '%sx%s'", width, height)

    def set_staging_dir(self, path="", subfolder=""):
        if path:
            staging_dir = os.path.normpath(path)
        else:
            staging_dir = tempfile.gettempdir()

        if subfolder:
            staging_dir = os.path.join(staging_dir, subfolder)

        if not os.path.exists(staging_dir):
            os.makedirs(staging_dir)

        self.staging_dir = staging_dir
        self.log.debug("Staging dir set to: '%s'", staging_dir)

    def read_template(self, template_path=None, resources_path=None):
        """Read HTML template from file and normalize any relative paths.

        The paths get expanded with the resources directory as base directory.

        Args:
            template_path (str, optional): Path to the HTML template file. If
                not provided, the default template path will be used. Defaults
                to None.
            resources_path (str, optional): Path to the resources directory. If
                not provided, the default resources path will be used. Defaults
                to None.

        Raises:
            ValueError: If template_path or resources_path is not specified.

        """
        template_path = template_path or self.template_path
        if not template_path:
            raise ValueError("Please Specify a template path!")

        resources_path = resources_path or self.resources_path
        if not resources_path:
            raise ValueError("Please Specify a resources path!")

        with open(template_path, "r") as template_file:
            html_template = template_file.read()

        # Regular expression that finds all href/src paths that don't start
        # with '{' as those will get expanded later on the `compute_template`
        # function
        src_pattern = r'(href|src)=(["\'])(?!{)(.*?)(["\'])'

        def _normalize_and_replace_path(match):
            new_path = os.path.join(
                resources_path,
                os.path.basename(match.group(3))
            )
            normalized_path = os.path.normpath(new_path)
            new_tag = "{}={}{}{}".format(
                match.group(1), match.group(2), normalized_path, match.group(4)
            )
            return new_tag

        # Replace all found paths with the resources path using regex
        normalized_template = re.sub(
            src_pattern,
            _normalize_and_replace_path,
            html_template
        )

        self._template_string = normalized_template
        self.log.debug("Template string: '{}'".format(normalized_template))

    def compute_template(self, process_optionals=True):
        """Compute HTML template by substituting template strings.

        Raises:
            ValueError: If the template data is empty.

        Returns:
            str: The computed HTML template.

        Notes:
            - The keys in the template must be at the root of the data
                dictionary.
            - Nested dictionaries are not supported, only lists.
            - Keys in the template with "_optional" suffix will be substituted
                with "display:None" in the style property, allowing selective
                hiding of corresponding blocks if the key is empty.
        """

        if not self._template_string:
            raise ValueError(
                "Slate template HTML data is empty, please reread template or "
                "check source file."
            )

        if process_optionals:
            optional_matches = re.findall(
                r"{(.*?)_optional}", self._template_string)
            hidden_string = 'style="display:None;"'
            for match in optional_matches:
                self.data["{}_optional".format(match)] = ""
                if not self.data[match]:
                    self.data["{}_optional".format(match)] = hidden_string

        try:
            template_string_computed = self._template_string.format(**self.data)
            self.log.debug("Computed Template string: '%s'",
                           template_string_computed)
            self.log.debug("Data: %s", self.data)
            return template_string_computed

        except KeyError as err:
            self.log.error(
                "Missing %s key in instance data.\nTemplate formatting cannot "
                "be completed successfully!", err
            )
            raise

    def render_slate(self, slate_path="", slate_specifier="", resolution=None):
        """Render the slate image by replacing the tokens from HTML template.

        Slates are rendered using screen color space (usually Rec709 or sRGB)
        so any HTML that needs to respect color needs to take that into
        account.

        Args:
            slate_path (str, optional): Path or filename where we want the
                slate file to be saved as. If a path is provided we replace the
                staging dir with the base directory, otherwise we reuse the
                existing staging dir and simply set the name of the file. If
                arg not provided, defaults to default name on the staging
                directory.
            slate_specifier (str): String specifier to identify different
                generated slates.
            resolution (tuple): Tuple of (width, height) representing the
                resolution of the generated slate.

        Returns:
            str: Path to the rendered slate image.
        """
        if slate_path:
            base_dir, filename = os.path.split(slate_path)
            if base_dir:
                self.set_staging_dir(base_dir)
            slate_name = filename
        else:
            slate_name = "slate_staged{}.png".format(slate_specifier)

        if resolution:
            self.data["resolution_width"] = resolution[0]
            self.data["resolution_height"] = resolution[1]
        else:
            data_width = self.data["resolution_width"] or 3840
            data_height = self.data["resolution_height"] or 2160
            resolution = (data_width, data_height)

        # Replace tokens from HTML template with instance data
        html_template = self.compute_template()

        # Use Html2Image python module to read HTML template with headless
        # Chrome and take a screenshot and generate an image of it
        chrome_path = get_chrome_tool_path()
        self.log.info(
            "Render slate using html2image at '%s' with resolution '%s'.",
            self.staging_dir, resolution
        )
        hti = Html2Image(
            browser_executable=chrome_path,
            output_path=self.staging_dir,
            custom_flags=[
                "--no-sandbox",
                "--headless",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-dev-shm-usage"
            ]
        )
        slate_rendered_paths = hti.screenshot(
            html_str=html_template,
            save_as=slate_name,
            size=resolution,
        )

        return slate_rendered_paths[0]

    def render_image_oiio(
            self, input, output, in_args=None, out_args=None, ocio_config_path=None
        ):
        """Call oiiotool to convert one image to another."""
        name = os.path.basename(input)

        cmd = get_oiio_tool_args("oiiotool")
        if ocio_config_path:
            cmd.extend(["--colorconfig", ocio_config_path])

        if in_args:
            cmd.extend(in_args)
        cmd.extend(["-i", input])

        if out_args:
            cmd.extend(out_args)

        cmd.extend(["-o", output])

        try:
            run_subprocess(cmd, logger=self.log)
        except TypeError as error:
            raise TypeError(
                "%s: Error creating '%s' due to: %s", name, output, error
            )

    def _get_video_metadata(self, streams):
        input_timecode = ""
        input_width = None
        input_height = None
        input_frame_rate = None
        for stream in streams:
            if stream.get("codec_type") != "video":
                continue
            self.log.debug("FFprobe Video: {}".format(stream))

            if "width" not in stream or "height" not in stream:
                continue
            width = int(stream["width"])
            height = int(stream["height"])
            if not width or not height:
                continue

            # Make sure that width and height are captured even if frame rate
            #    is not available
            input_width = width
            input_height = height

            tags = stream.get("tags") or {}
            input_timecode = tags.get("timecode") or ""

            input_frame_rate = stream.get("r_frame_rate")
            if input_frame_rate is not None:
                break
        return (
            input_width,
            input_height,
            input_timecode,
            input_frame_rate
        )

    def get_metadata_ffprobe(self, input):
        """Find input resolution and timecode using ffprobe."""
        try:
            streams = get_ffprobe_streams(input, self.log)
        except Exception as exc:
            raise AssertionError(
                "FFprobe couldn't read information about input file: '{}'.\n{}"
                .format(input, str(exc))
            )

        # Get video metadata
        (
            input_width,
            input_height,
            input_timecode,
            input_frame_rate
        ) = self._get_video_metadata(streams)

        # Raise exception of any stream didn't define input resolution
        if input_width is None:
            raise AssertionError(
                "FFprobe couldn't read resolution from input file: '{}'."
                .format(input)
            )

        if input_frame_rate:
            items = input_frame_rate.split("/")
            if len(items) == 1:
                input_frame_rate = float(items[0])
            elif len(items) == 2:
                input_frame_rate = float(items[0]) / float(items[1])

        if not input_frame_rate:
            input_frame_rate = self.data["fps"]

        return (input_width, input_height, input_timecode, input_frame_rate)


class ExtractSlateGlobal(publish.Extractor):
    """Extractor that creates slate frames from an HTML template using OIIO.

    Slate frames are based on an html template that gets rendered
    using headless chrome/chromium. They then get converted using oiio,
    with an ffmpeg fallback (still not implemented).

    It will work only on represenations having `slateGlobal = True` and
    `tags` including `slate`
    """

    label = "Extract Slate Global"
    order = pyblish.api.ExtractorOrder + 0.0305
    families = ["slate"]
    hosts = ["*"]

    _slate_data_name = "slateGlobal"

    def process(self, instance):
        
        if not Html2Image:
            self.log.warning(
                "Html2Image couldn't be loaded in environment, skipping slate "
                "extraction..."
            )
            return
        
        if self._slate_data_name not in instance.data:
            self.log.warning(
                "Slate Global workflow is not active, skipping slate "
                "extraction..."
            )
            return

        if "representations" not in instance.data:
            self.log.warning(
                "Instance doesn't have any representations, skipping slate."
            )
            return

        repre_ignore_list = ["thumbnail", "passing"]

        slate_data = instance.data[self._slate_data_name]

        # get pyblish comment and intent
        common_data = slate_data["slate_common_data"]
        common_data["comment"] = instance.context.data.get("comment") or "-"
        intent = instance.context.data.get("intent")
        if not isinstance(intent, dict):
            intent = {
                "label": intent,
                "value": intent
            }
        common_data["intent"] = intent

        # init SlateCreator Object that creates the slate frames
        slate_creator = SlateCreator(
            template_path=slate_data["slate_template_path"],
            resources_path=slate_data["slate_resources_path"],
            data=common_data,
        )

        # loop through representations to find thumbnail path
        repre_thumbnail_path = ""
        for thumb_repre in instance.data["representations"]:
            if thumb_repre["name"] == "thumbnail":
                repre_thumbnail_path = os.path.join(
                    thumb_repre["stagingDir"], thumb_repre["files"]
                )

        repre_match = None

        # loop through representations and generate a slate frame for each
        for repre in instance.data["representations"]:
            self.log.debug(
                "Generating slate for representation '{}', with files '{}'.".format(
                repre["name"], repre["files"])
            )
            if repre["name"] in repre_ignore_list:
                self.log.debug(
                    "Representation '{}' is ignored.".format(repre["name"])
                )
                continue

            # check if repre is a sequence
            is_sequence = isinstance(repre["files"], list)
            check_file = repre["files"][0] if is_sequence else repre["files"]

            file_path = os.path.normpath(
                os.path.join(repre["stagingDir"], check_file))

            # if representation is a sequence render out a slate before first
            # frame
            frame_start = int(repre.get("frameStart", instance.data.get("frameStart")))
            slate_frame_start = frame_start
            if is_sequence:
                filename, _frame, ext = check_file.split(".")
                slate_frame_start = frame_start - 1
                frame_end = len(repre["files"]) + frame_start
                output_name = "{}.{}.{}".format(
                    filename,
                    str(slate_frame_start).zfill(len(_frame)),
                    ext,
                )
                thumbnail_path = os.path.join(
                    repre["stagingDir"],
                    "{}_slate_thumb.png".format(repre["name"]),
                ).replace("\\", "/")
                slate_creator.render_image_oiio(
                    file_path.replace("\\", "/"), thumbnail_path
                )
                repre_match = instance.data["family"]

            else:  # else find matching tags and transfer
                frame_end = int(repre.get("frameEnd", instance.data.get("frameEnd")))
                thumbnail_path = repre_thumbnail_path
                output_name = "{}_slate_temp.png".format(repre["name"])
                for tag in repre["tags"]:
                    for profile in slate_data["slate_profiles"]:
                        if tag in profile["families"]:
                            repre_match = tag

            # extract metadata from input
            width, height, in_timecode, in_fps = slate_creator.get_metadata_ffprobe(
                file_path
            )

            # add timecode from source to the slate, substract one frame
            offset_timecode = None
            if in_timecode:
                # Offset timecode to account for slate frame
                timecode_frames = timecode_to_frames(
                    in_timecode, in_fps
                )
                # Subtracts one frame to account for the slate frame
                timecode_frames -= 1
                timecode = frames_to_timecode(
                    timecode_frames, in_fps
                )
                self.log.debug("%s: Timecode for slate set to: %s", input, timecode)
                self.log.debug("Slate Timecode: `{}`".format(
                    offset_timecode
                ))
            else:
                self.log.warning("Input timecode not found at '%s'", file_path)
                offset_timecode = frames_to_timecode(frame_start - 1, in_fps)

            oiio_profile = {
                "families": [],
                "hosts": [],
                "oiio_args": {"input": [], "output": []},
            }
            for profile in slate_data["slate_profiles"]:
                if repre_match in profile["families"]:
                    # use profile matching defaults
                    oiio_profile = profile
                    break

            # add timecode to oiio output args
            oiio_profile["oiio_args"]["output"].extend(
                [
                    "--attrib:type=timecode",
                    "smpte:TimeCode",
                    '"{}"'.format(offset_timecode),
                ]
            )
            slate_creator.data.update(oiio_profile)

            # data Layout and preparation in instance
            slate_repre_data = {
                "family_match": repre_match or "",
                "frameStart": frame_start,
                "frameEnd": frame_end,
                "frameStartHandle": instance.data.get(
                    "frameStartHandle", None
                ),
                "frameEndHandle": instance.data.get("frameEndHandle", None),
                "real_frameStart": slate_frame_start,
                "resolution_width": width,
                "resolution_height": height,
                "stagingDir": repre["stagingDir"],
                "slate_file": output_name,
                "thumbnail": thumbnail_path,
                "timecode": offset_timecode,
                "fps": in_fps,
            }
            slate_data["slate_repre_data"][repre["name"]] = slate_repre_data
            slate_creator.data.update(slate_repre_data)

            # set properties for rendering
            slate_creator.set_resolution(
                slate_creator.data["resolution_width"],
                slate_creator.data["resolution_height"]
            )
            slate_creator.set_staging_dir(slate_creator.data["stagingDir"])

            self.log.info("Slate data: %s", slate_creator.data)

            # render slate
            temp_slate = slate_creator.render_slate(
                slate_path="{}_slate.png".format(repre["name"])
            )

            slate_final_path = os.path.normpath(
                os.path.join(
                    slate_creator.data["stagingDir"],
                    slate_creator.data["slate_file"]
                )
            )

            # Extract colorspace config path from representation
            colorspace_data = repre.get("colorspaceData", {})
            config_path = colorspace_data.get("config", {}).get("path")

            slate_creator.render_image_oiio(
                temp_slate,
                slate_final_path,
                in_args=oiio_profile["oiio_args"].get("input") or [],
                out_args=oiio_profile["oiio_args"].get("output") or [],
                ocio_config_path=config_path,
            )

            # update representations and instance
            if is_sequence:
                repre["files"].insert(0, slate_creator.data["slate_file"])
                self.log.debug(
                    "Added '{}' to '{}' representation file list.".format(
                        slate_creator.data["slate_file"], repre["name"]
                    )
                )
                repre["frameStart"] = slate_creator.data["real_frameStart"]
                self.log.debug("Updated 'frameStart' to '%s'.", repre["frameStart"])
            else:
                if not instance.data.get("slateFrames"):
                    instance.data["slateFrames"] = {"*": slate_final_path}
                else:
                    instance.data["slateFrames"].update(
                        {repre["name"]: slate_final_path}
                    )
                instance.data["slateFrame"] = slate_final_path
                self.log.debug(
                    "SlateFrames: {}".format(instance.data["slateFrames"])
                )
