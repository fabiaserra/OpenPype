import os
import re
import json
import getpass
import requests
import pyblish.api
import PyOpenColorIO

import hiero

from openpype.lib import is_running_from_build
from openpype.pipeline import publish, legacy_io
from openpype.hosts.hiero.api import work_root


class TranscodeFrames(publish.Extractor):
    """Transcode Hiero media to the right colorspace using OIIO or Nuke"""

    order = pyblish.api.ExtractorOrder - 0.1
    label = "Extract Transcode Frames"
    hosts = ["hiero"]
    families = ["plate"]
    movie_extensions = {"mov", "mp4", "mxf"}
    nuke_specific_extensions = {"braw"}
    output_ext = "exr"
    dst_media_color_transform = "scene_linear"

    # TODO: Replace these with published Templates workflow
    nuke_transcode_py = "/pipe/hiero/templates/nuke_transcode.py"
    nuke_transcode_script = "/pipe/hiero/templates/ingest_transcode.nk"

    # WARNING: Need to be very careful about the length of the overall command
    # Anything around 490-505 will cause ffmpeg to through an error
    # OIIO args we want to run to convert colorspaces
    oiio_args = [
        "--frames",
        "<STARTFRAME>-<ENDFRAME>",
        '"{input_path}"',  # Escape input path in case there's whitespaces
        "--eraseattrib",
        '"Exif:ImageHistory"', # Image history is too long and not needed
        "-v",
        "--compression",
        "zips",
        "-d",
        "half",
        "--scanline",
        # "--sattrib",  # Can't add this meta until farm OIIO supports it
        # "original_meta",
        # '"{{TOP.META}}"', # Add meta from current input for pass through
        "--attrib:subimages=1",
        "framesPerSecond",
        '"{fps}"',
        "--colorconfig", # Add color config as an arg so that it can be traced
        '"{ocio_path}"',
        "--colorconvert",
        '"{src_media_color_transform}"',
        '"{dst_media_color_transform}"',
        "--sansattrib", # Remove attrib/sattrib from command in software/exif
        "-o",
        "{output_path}",
    ]

    # presets
    priority = 50
    chunk_size = 9999
    concurrent_tasks = 1
    group = "nuke-cpu-epyc"
    department = "Editorial"
    limit_groups = {}
    env_allowed_keys = []
    env_search_replace_values = {}

    def process(self, instance):
        """Submit a job to the farm to transcode the video frames"""
        instance.data["toBeRenderedOn"] = "deadline"

        context = instance.context

        # get default deadline webservice url from deadline module
        deadline_url = context.data["defaultDeadline"]
        # if custom one is set in instance, use that
        if instance.data.get("deadlineUrl"):
            deadline_url = instance.data.get("deadlineUrl")
        assert deadline_url, "Requires Deadline Webservice URL"

        self.deadline_url = "{}/api/jobs".format(deadline_url)
        self._comment = context.data.get("comment", "")
        self._ver = "{}.{}".format(
            hiero.core.env["VersionMajor"], hiero.core.env["VersionMinor"]
        )
        self._deadline_user = context.data.get(
            "deadlineUser", getpass.getuser()
        )

        track_item = instance.data["item"]
        media_source = track_item.source().mediaSource()

        # Define source path along with extension
        input_path = media_source.fileinfos()[0].filename()
        source_ext = os.path.splitext(input_path)[1][1:]

        # Output variables
        staging_dir = os.path.join(work_root(legacy_io.Session), "temp_transcode")

        # Create staging dir if it doesn't exist
        try:
            if not os.path.isdir(staging_dir):
                os.makedirs(staging_dir, exist_ok=True)
        except OSError:
            # directory is not available
            self.log.warning("Path is unreachable: `{}`".format(staging_dir))

        instance.data["stagingDir"] = staging_dir

        output_template = os.path.join(staging_dir, instance.data["name"])
        output_dir = os.path.dirname(output_template)

        # Determine color transformation
        src_media_color_transform = track_item.sourceMediaColourTransform()
        # Define extra metadata variables
        ocio_path = os.getenv("OCIO")

        # TODO: skip transcoding if source colorspace matches destination
        # if src_media_color_transform == self.dst_media_color_transform:
        src_frame_start, src_frame_end = instance.data["srcFrameRange"]
        out_frame_start, out_frame_end = instance.data["outFrameRange"]
        self.log.info(
            f"Processing frames {out_frame_start} - {out_frame_end}"
        )

        anatomy = instance.context.data["anatomy"]
        padding = anatomy.templates.get("frame_padding", 4)
        output_path = (
            f"{output_template}.%0{padding}d.{self.output_ext}"
        )

        self.log.info("Output path: %s", output_path)
        self.log.info("Output ext: %s", self.output_ext)
        self.log.info("Source ext: %s", source_ext.lower())
        # If either source or output is a video format, transcode using Nuke
        if (self.output_ext.lower() in self.movie_extensions or
                source_ext.lower() in self.movie_extensions or
                source_ext.lower() in self.nuke_specific_extensions) or \
                instance.data.get("use_nuke", False):
            # No need to raise error as Nuke raises an error exit value if something went wrong
            self.log.info("Submitting Nuke transcode")

            # Add environment variables required to run Nuke script
            extra_env = {}
            extra_env["_AX_TRANSCODE_NUKESCRIPT"] = self.nuke_transcode_script
            extra_env["_AX_TRANSCODE_FRAMES"] = "{0}_{1}_{2}".format(
                int(out_frame_start), int(out_frame_end), int(src_frame_start)
            )
            extra_env["_AX_TRANSCODE_READTYPE"] = self.output_ext.lower()
            extra_env["_AX_TRANSCODE_READPATH"] = input_path
            extra_env["_AX_TRANSCODE_WRITEPATH"] = output_path
            extra_env["_AX_TRANSCODE_READCOLORSPACE"] = src_media_color_transform
            extra_env["_AX_TRANSCODE_TARGETCOLORSPACE"] = self.dst_media_color_transform

            response = self.payload_submit(
                instance,
                output_path,
                (out_frame_start, out_frame_end),
                plugin="Nuke",
                extra_env=extra_env,
            )
        else:
            self.log.info("Submitting OIIO transcode")
            oiio_args = " ".join(self.oiio_args).format(
                input_path=input_path,
                src_media_color_transform=src_media_color_transform,
                dst_media_color_transform=self.dst_media_color_transform,
                output_path=output_path,
                fps=round(instance.data["fps"], 2),
                ocio_path=ocio_path,
            )

            # NOTE: We use src frame start/end because oiiotool doesn't support
            # writing out a different frame range than input
            response = self.payload_submit(
                instance,
                output_path,
                (src_frame_start, src_frame_end),
                plugin="CommandLine",
                args=oiio_args,
                executable="/usr/openpype/3.16/vendor/bin/oiio/linux/bin/oiiotool",
            )

        # Store output dir for unified publisher (filesequence)
        instance.data["deadlineSubmissionJob"] = response.json()
        instance.data["outputDir"] = output_dir
        instance.data["publishJobState"] = "Suspended"

        # Remove source representation as its replaced by the transcoded frames
        ext_representations = [
            rep
            for rep in instance.data["representations"]
            if rep["ext"] == source_ext
        ]
        if ext_representations:
            self.log.info(
                "Removing source representation and replacing with transcoded frames"
            )
            instance.data["representations"].remove(ext_representations[0])
        else:
            self.log.info("No source ext to remove from representation")

    def payload_submit(
        self,
        instance,
        render_path,
        out_framerange,
        plugin,
        executable=None,
        args=None,
        extra_env=None,
        response_data=None,
    ):
        render_dir = os.path.normpath(os.path.dirname(render_path))
        jobname = "%s - %s" % (render_dir, instance.name)

        output_filename_0 = self.preview_fname(render_path)

        if not response_data:
            response_data = {}

        try:
            # Ensure render folder exists
            os.makedirs(render_dir)
        except OSError:
            pass

        payload = {
            "JobInfo": {
                # Top-level group name
                "BatchName": render_dir,
                # Job name, as seen in Monitor
                "Name": jobname,
                # Arbitrary username, for visualisation in Monitor
                "UserName": self._deadline_user,
                "Priority": self.priority,
                "ChunkSize": self.chunk_size,
                "ConcurrentTasks": self.concurrent_tasks,
                "Department": self.department,
                "Pool": instance.data.get("primaryPool"),
                "SecondaryPool": instance.data.get("secondaryPool"),
                "Group": self.group,
                "Plugin": plugin,
                "Frames": f"{out_framerange[0]}-{out_framerange[1]}",
                "Comment": self._comment,
                # Optional, enable double-click to preview rendered
                # frames from Deadline Monitor
                "OutputFilename0": output_filename_0.replace("\\", "/"),
            },
            "PluginInfo": {
                # Output directory and filename
                "OutputFilePath": render_dir.replace("\\", "/"),
                # Resolve relative references
                "AWSAssetFile0": render_path,
            },
            # Mandatory for Deadline, may be empty
            "AuxFiles": [],
        }

        plugin_overrides = {}
        if plugin == "Nuke":
            plugin_overrides = {
                "ScriptJob": True,
                "ScriptFilename": self.nuke_transcode_py,
                "SceneFile": self.nuke_transcode_py,
                "Version": self._ver,
                "UseGpu": False,
            }

        elif plugin == "CommandLine":
            plugin_overrides = {
                "Executable": executable,
                "Arguments": args,
                "UseGpu": False,
                "WorkingDirectory": render_dir,
            }

        # Update plugin info with overrides
        payload["PluginInfo"].update(plugin_overrides)

        if response_data.get("_id"):
            payload["JobInfo"].update(
                {
                    "JobType": "Normal",
                    "BatchName": response_data["Props"]["Batch"],
                    "JobDependency0": response_data["_id"],
                    "ChunkSize": 99999999,
                }
            )

        # Include critical environment variables with submission
        keys = [
            "AVALON_APP_NAME",
            "AVALON_ASSET",
            "AVALON_PROJECT",
            "AVALON_TASK",
            "FOUNDRY_LICENSE",
            "FTRACK_API_KEY",
            "FTRACK_API_USER",
            "FTRACK_SERVER",
            "NUKE_PATH",
            "OPENPYPE_SG_USER",
            "PATH",
            "PYBLISHPLUGINPATH",
            "PYTHONPATH",
            "TOOL_ENV",
            "OCIO",
        ]

        # Add OpenPype version if we are running from build.
        if is_running_from_build():
            keys.append("OPENPYPE_VERSION")

        # Add mongo url if it's enabled
        if instance.context.data.get("deadlinePassMongoUrl"):
            keys.append("OPENPYPE_MONGO")

        # add allowed keys from preset if any
        if self.env_allowed_keys:
            keys += self.env_allowed_keys

        environment = dict(
            {key: os.environ[key] for key in keys if key in os.environ},
            **legacy_io.Session,
        )

        for _path in os.environ:
            if _path.lower().startswith("openpype_"):
                environment[_path] = os.environ[_path]

        if extra_env:
            environment.update(extra_env)

        # to recognize job from PYPE for turning Event On/Off
        environment["OPENPYPE_RENDER_JOB"] = "1"

        # finally search replace in values of any key
        if self.env_search_replace_values:
            for key, value in environment.items():
                for _k, _v in self.env_search_replace_values.items():
                    environment[key] = value.replace(_k, _v)

        payload["JobInfo"].update(
            {
                "EnvironmentKeyValue%d"
                % index: "{key}={value}".format(
                    key=key, value=environment[key]
                )
                for index, key in enumerate(environment)
            }
        )

        plugin = payload["JobInfo"]["Plugin"]
        self.log.info("using render plugin : {}".format(plugin))

        self.log.info("Submitting..")
        self.log.info(json.dumps(payload, indent=4, sort_keys=True))

        # adding expected files to instance.data
        self.expected_files(instance, render_path, out_framerange[0], out_framerange[1])

        self.log.debug(
            "__ expectedFiles: `{}`".format(instance.data["expectedFiles"])
        )
        response = requests.post(self.deadline_url, json=payload, timeout=10)

        if not response.ok:
            raise Exception(response.text)

        return response

    def expected_files(
        self,
        instance,
        path,
        out_frame_start,
        out_frame_end
    ):
        """Create expected files in instance data"""
        if not instance.data.get("expectedFiles"):
            instance.data["expectedFiles"] = []

        dirname = os.path.dirname(path)
        filename = os.path.basename(path)

        if "#" in filename:
            pparts = filename.split("#")
            padding = "%0{}d".format(len(pparts) - 1)
            filename = pparts[0] + padding + pparts[-1]

        if "%" not in filename:
            instance.data["expectedFiles"].append(path)
            return

        for i in range(out_frame_start, (out_frame_end + 1)):
            instance.data["expectedFiles"].append(
                os.path.join(dirname, (filename % i)).replace("\\", "/"))

        # Set frame start/end handles as it's used in integrate to map
        # the frames to the correct frame range
        instance.data["frameStartHandle"] = out_frame_start
        instance.data["frameEndHandle"] = out_frame_end

    def preview_fname(self, path):
        """Return output file path with #### for padding.

        Deadline requires the path to be formatted with # in place of numbers.
        For example `/path/to/render.####.png`

        Args:
            path (str): path to rendered images

        Returns:
            str

        """
        self.log.debug("_ path: `{}`".format(path))
        if "%" in path:
            hashes_path = re.sub(r"%(\d*)d", lambda m: "#" * int(m.group(1)) if m.group(1) else "#", path)
            return hashes_path

        if "#" in path:
            self.log.debug("_ path: `{}`".format(path))

        return path
