import os
import pyblish.api

from openpype.lib import (
    get_oiio_tools_path,
    run_subprocess,
)
from openpype.pipeline import publish
from openpype.lib.applications import ApplicationManager


def nuke_transcode_template(
    output_ext,
    input_frame,
    first_frame,
    last_frame,
    read_path,
    write_path,
    src_colorspace,
    dst_colorspace,
):
    python_template = "/pipe/hiero/templates/nuke_transcode.py"
    nuke_template = "/pipe/hiero/templates/ingest_transcode.nk"
    app_manager = ApplicationManager()
    nuke_app_name = os.environ["AVALON_APP_NAME"].replace("hiero", "nuke")
    nuke_app = app_manager.applications.get(nuke_app_name)
    nuke_args = nuke_app.find_executable().as_args()
    cmd = nuke_args + [
        "-t",
        python_template,
        nuke_template,
        "{0}_{1}_{2}".format(int(first_frame), int(last_frame), int(input_frame)),
        output_ext,
        read_path,
        write_path,
        src_colorspace,
        dst_colorspace,
    ]

    # If non exist status is returned output will raise exception.
    # No need to handle since run_subprocess already formats and handles error
    run_subprocess(cmd)


class TranscodeFrames(publish.Extractor):
    """Transcode frames"""

    order = pyblish.api.ExtractorOrder - 0.1
    label = "Transcode Frames"
    hosts = ["hiero"]
    families = ["plate"]
    movie_extensions = {"mov", "mp4", "mxf"}
    output_ext = "exr"
    output_padding = "%04d"
    dst_colorspace = "scene_linear"

    def process(self, instance):
        """
        Plate - Transcodes to exr with color becoming linear
        Reference - For now does not get transcoded and stays same as source
        """
        oiio_tool_path = get_oiio_tools_path()

        track_item = instance.data["item"]
        media_source = track_item.source().mediaSource()

        # Define source path along with extension
        input_path = media_source.fileinfos()[0].filename()
        padding_length = media_source.filenamePadding()
        # Input padding is needed
        input_padding = f"%0{padding_length}d"
        source_ext = os.path.splitext(input_path)[1][1:]

        # Output variables
        staging_dir = self.staging_dir(instance)
        output_template = os.path.join(staging_dir, instance.data["name"])
        output_dir = os.path.dirname(output_template)

        # Determine color transformation
        src_colorspace = track_item.sourceMediaColourTransform()

        frame_range = instance.data["frameRange"]
        len_frames = len(frame_range)
        first_input_frame, first_output_frame = frame_range[0]
        last_input_frame, last_output_frame = frame_range[-1]

        self.log.info(
            f"Processing frames {first_output_frame} - {last_output_frame}")
        # If either source or output is a video format, transcode using Nuke
        if self.output_ext.lower() in self.movie_extensions or source_ext.lower() in self.movie_extensions:
            # No need to raise error as Nuke raises an error exit value if something went wrong
            output_path = f"{output_template}.{self.output_padding}.{self.output_ext}"
            nuke_transcode_template(
                self.output_ext,
                first_input_frame,
                first_output_frame,
                last_output_frame,
                input_path,
                output_path,
                src_colorspace,
                self.dst_colorspace,
            )

        else:
            # Else use OIIO instead of Nuke for faster transcoding
            args = [oiio_tool_path]

            # Input frame start
            args.extend(["--frames", f"{first_input_frame}-{last_input_frame}"])

            # Input path
            args.append(input_path)

            # Add colorspace conversion
            args.extend(["--colorconvert", src_colorspace, self.dst_colorspace])

            # Copy old metadata
            args.append("--pastemeta")

            # Add metadata
            # Ingest colorspace
            args.extend(["--sattrib", "alkemy/ingest/colorspace", src_colorspace])
            # Input Filename
            args.extend(["--sattrib", "input/filename", input_path])

            # Output path
            output_path = f"{output_template}.{input_padding}.{self.output_ext}"
            args.extend(["-o", output_path])

            output = run_subprocess(args)

            failed_output = "oiiotool produced no output."
            if failed_output in output:
                raise ValueError("oiiotool processing failed. Args: {}".format(args))

            # Do a batch rename if there is a frame offset
            frame_offset = first_output_frame - first_input_frame
            reverse = frame_offset > 0
            if frame_offset or input_padding != self.output_padding:
                self.log.info("Batch renaming")
                self.log.info(f"    Frame offset: {frame_offset}")
                self.log.info(f"    Updating padding: {input_padding} -> {self.output_padding}")

                # Reverse is used to make sure frames aren't overwritten
                # If frame offset is positive rename last to first
                if reverse:
                    frames = range(last_input_frame, first_input_frame + 1)
                # If frame offset is negative rename first to last
                else:
                    frames = range(first_input_frame, last_input_frame + 1)

                for frame in frames:
                    # Build frame path based on media source
                    # Maintain old frame padding length
                    old_frame_path = f"{output_path}".replace(input_padding, f"{str(frame).zfill(padding_length)}")
                    # New padding is forced to 4
                    new_frame_path = f"{output_path}".replace(input_padding, f"{str(frame + frame_offset).zfill(4)}")
                    if not os.path.isfile(old_frame_path):
                        raise OSError(f"Could not rename {old_frame_path}")

                    os.rename(old_frame_path, new_frame_path)

        # If process comes through without error we can assume what files were made
        files = [f"{output_template}.{frame[1]:04d}.{self.output_ext}" for frame in frame_range]

        ext_representations = [
            rep for rep in instance.data["representations"] if rep["ext"] == source_ext
        ]
        if ext_representations:
            self.log.info("Removing source representation and replacing with transcoded frames")
            instance.data["representations"].remove(ext_representations[0])
        else:
            self.log.info("No source ext to remove from representation")

        instance.data["representations"].append(
            {
                "name": self.output_ext,
                "ext": self.output_ext,
                "files": os.path.basename(files[0])
                if len(files) == 1
                else [os.path.basename(x) for x in files],
                "stagingDir": output_dir,
                # After EXRs are processed - review needs be added to the new
                # representation
                "tags": ["review", "shotgridreview"],
            }
        )
