# -*- coding: utf-8 -*-
"""Submit publishing job to farm."""
import os
import json
import re
from copy import deepcopy
import requests
import clique

import pyblish.api

from openpype import AYON_SERVER_ENABLED
from openpype.client import (
    get_last_version_by_subset_name,
)
from openpype.pipeline import publish, legacy_io
from openpype.lib import EnumDef, is_running_from_build
from openpype.tests.lib import is_in_tests
from openpype.pipeline.version_start import get_versioning_start

from openpype.pipeline.farm.pyblish_functions import (
    create_skeleton_instance,
    create_instances_for_aov,
    attach_instances_to_subset,
    prepare_representations,
    create_metadata_path
)


class ProcessSubmittedJobOnFarm(pyblish.api.InstancePlugin,
                                publish.OpenPypePyblishPluginMixin,
                                publish.ColormanagedPyblishPluginMixin):
    """Process Job submitted on farm.

    These jobs are dependent on a deadline job
    submission prior to this plug-in.

    It creates dependent job on farm publishing rendered image sequence.

    Options in instance.data:
        - deadlineSubmissionJob (dict, Required): The returned .json
          data from the job submission to deadline.

        - outputDir (str, Required): The output directory where the metadata
            file should be generated. It's assumed that this will also be
            final folder containing the output files.

        - ext (str, Optional): The extension (including `.`) that is required
            in the output filename to be picked up for image sequence
            publishing.

        - publishJobState (str, Optional): "Active" or "Suspended"
            This defaults to "Suspended"

        - expectedFiles (list or dict): explained below

    """

    label = "Submit publish job to Deadline"
    order = pyblish.api.IntegratorOrder + 0.2
    icon = "tractor"

    targets = ["local"]

    hosts = ["fusion", "max", "maya", "nuke", "houdini",
             "celaction", "aftereffects", "harmony", "traypublisher",
             "hiero", "blender"]

    ### Starts Alkemy-X Override ###
    # Add all the families from TrayPublisher with a `.farm` suffix so this
    # plugin only picks them up when the suffix is added
    families = [
        "arnold_rop",
        "camera.farm",
        "image.farm",
        "imagesequence",
        "karma_rop",
        "mantra_rop",
        "matchmove.farm",
        "maxrender",
        "model.farm",
        "plate.farm",
        "pointcache.farm",
        "prerender.frames_farm",
        "prerender.farm",
        "redshift_rop",
        "reference.farm",
        "render.frames_farm",
        "render.farm",
        "renderlayer",
        "rig.farm",
        "simpleUnrealTexture.farm",
        "vdb.farm",
        "vray_rop",
        "vrayscene",
        "workfile.farm",
    ]
    ### Ends Alkemy-X Override ###

    aov_filter = {
        "maya": [r".*([Bb]eauty).*"],
        "blender": [r".*([Bb]eauty).*"],
        "houdini": [r".*"],
        "aftereffects": [r".*"],  # for everything from AE
        "harmony": [r".*"],  # for everything from AE
        "celaction": [r".*"],
        "max": [r".*"],
        "nuke": [r".*"],
    }

    environ_job_filter = [
        "OPENPYPE_METADATA_FILE"
    ]

    environ_keys = [
        "FTRACK_API_USER",
        "FTRACK_API_KEY",
        "FTRACK_SERVER",
        "AVALON_APP_NAME",
        "OPENPYPE_USERNAME",
        "AYON_SG_USER" if AYON_SERVER_ENABLED else "OPENPYPE_SG_USER",
        "KITSU_LOGIN",
        "KITSU_PWD"
    ]

    # custom deadline attributes
    deadline_department = ""
    deadline_pool = ""
    deadline_pool_secondary = ""
    deadline_group = ""
    deadline_chunk_size = 1
    deadline_priority = None

    # regex for finding frame number in string
    R_FRAME_NUMBER = re.compile(r'.+\.(?P<frame>[0-9]+)\..+')

    # mapping of instance properties to be transferred to new instance
    #     for every specified family
    instance_transfer = {
        "slate": ["slateFrames", "slate"],
        "review": ["lutPath"],
        "render2d": ["bakingNukeScripts", "version"],
        "renderlayer": ["convertToScanline"],
        "plate.farm": ["main_plate", "cut_info_data", "sg_tags_data", "asset_working_format", "edit_note_data"],
    }

    # list of family names to transfer to new family if present
    families_transfer = [
        "render3d", "render2d", "ftrack", "slate", "client_review", "client_final",
    ]
    plugin_pype_version = "3.0"

    # script path for publish_filesequence.py
    publishing_script = None

    # poor man exclusion
    skip_integration_repre_list = []

    ### Starts Alkemy-X Override ###
    # Make 'jobs' argument a list so we can pass multiple dependency jobs
    def _submit_deadline_post_job(self, instance, jobs, instances):
    ### Ends Alkemy-X Override ###
        """Submit publish job to Deadline.

        Returns:
            (str): deadline_publish_job_id
        """
        data = instance.data.copy()
        subset = data["subset"]
        job_name = "Publish - {subset}".format(subset=subset)

        anatomy = instance.context.data['anatomy']

        # instance.data.get("subset") != instances[0]["subset"]
        # 'Main' vs 'renderMain'
        override_version = None
        instance_version = instance.data.get("version")  # take this if exists
        if instance_version != 1:
            override_version = instance_version

        ### Starts Alkemy-X Override ###
        # Collect task from instance if not set in the context
        # due to TrayPublisher not collecting it
        task_name = instance.context.data["task"] or instance.data.get("task", os.getenv("AVALON_TASK"))

        # Add 'asset' to publish job label for extra clarity
        job_name = "Publish {} - {}{} - {} - {} - {} ({})".format(
            instances[0]["family"],
            instances[0]["subset"],
            " v{0:03d}".format(override_version) if override_version else "",
            task_name,
            instance.data["asset"],
            instance.context.data["projectName"],
            os.getenv("SHOW")
        )
        ### Ends Alkemy-X Override ###

        output_dir = self._get_publish_folder(
            anatomy,
            deepcopy(instance.data["anatomyData"]),
            instance.data.get("asset"),
            instances[0]["subset"],
            instance.context,
            instances[0]["family"],
            override_version
        )

        # Transfer the environment from the original job to this dependent
        # job so they use the same environment
        metadata_path, rootless_metadata_path = \
            create_metadata_path(instance, anatomy, log=self.log)
        self.log.info("Metadata path: %s", metadata_path)

        environment = {
            "AVALON_DB": os.environ["AVALON_DB"],
            "AVALON_PROJECT": instance.context.data["projectName"],
            ### Starts Alkemy-X Override ###
            # Grab context from instance instead of context.data for the
            # cases where we use a generic context to publish to other contexts
            # (e.g. when ingesting in Hiero we use a high level context and we
            # need each individual publish to have the AVALON_ASSET pointing
            # to the final destination context)
            # "AVALON_ASSET": instance.context.data["asset"],
            "AVALON_ASSET": instance.data["asset"],
            "AVALON_TASK": task_name,
            ### Ends Alkemy-X Override ###
            "OPENPYPE_USERNAME": instance.context.data["user"],
            "OPENPYPE_LOG_NO_COLORS": "1",
            "IS_TEST": str(int(is_in_tests()))
        }

        if AYON_SERVER_ENABLED:
            environment["AYON_PUBLISH_JOB"] = "1"
            environment["AYON_RENDER_JOB"] = "0"
            environment["AYON_REMOTE_PUBLISH"] = "0"
            environment["AYON_BUNDLE_NAME"] = os.environ["AYON_BUNDLE_NAME"]
            deadline_plugin = "Ayon"
        else:
            environment["OPENPYPE_PUBLISH_JOB"] = "1"
            environment["OPENPYPE_RENDER_JOB"] = "0"
            environment["OPENPYPE_REMOTE_PUBLISH"] = "0"
            deadline_plugin = "OpenPype"
            # Add OpenPype version if we are running from build.
            if is_running_from_build():
                self.environ_keys.append("OPENPYPE_VERSION")

        # add environments from self.environ_keys
        for env_key in self.environ_keys:
            if os.getenv(env_key):
                environment[env_key] = os.environ[env_key]

        # pass environment keys from self.environ_job_filter
        ### Starts Alkemy-X Override ###
        # Make 'jobs' argument a list so we can pass multiple dependency jobs
        job_environ = {}
        for job in jobs:
            job_environ.update(job.get("Props", {}).get("Env", {}))
        ### Ends Alkemy-X Override ###

        for env_j_key in self.environ_job_filter:
            if job_environ.get(env_j_key):
                environment[env_j_key] = job_environ[env_j_key]

        # Add mongo url if it's enabled
        if instance.context.data.get("deadlinePassMongoUrl"):
            mongo_url = os.environ.get("OPENPYPE_MONGO")
            if mongo_url:
                environment["OPENPYPE_MONGO"] = mongo_url

        priority = self.deadline_priority or instance.data.get("priority", 50)

        instance_settings = self.get_attr_values_from_data(instance.data)
        initial_status = instance_settings.get("publishJobState", "Active")
        # TODO: Remove this backwards compatibility of `suspend_publish`
        if instance.data.get("suspend_publish"):
            initial_status = "Suspended"

        args = [
            "--headless",
            'publish',
            '"{}"'.format(rootless_metadata_path),
            "--targets", "deadline",
            "--targets", "farm"
        ]

        if is_in_tests():
            args.append("--automatic-tests")

        # Generate the payload for Deadline submission
        secondary_pool = (
            self.deadline_pool_secondary or instance.data.get("secondaryPool")
        )
        payload = {
            "JobInfo": {
                "Plugin": deadline_plugin,
                "BatchName": job["Props"]["Batch"],
                "Name": job_name,
                "UserName": job["Props"]["User"],
                "Comment": instance.context.data.get("comment", ""),

                "Department": self.deadline_department,
                "ChunkSize": self.deadline_chunk_size,
                "Priority": priority,
                "InitialStatus": initial_status,

                "Group": self.deadline_group,
                "Pool": self.deadline_pool or instance.data.get("primaryPool"),
                "SecondaryPool": secondary_pool,
                # ensure the outputdirectory with correct slashes
                "OutputDirectory0": output_dir.replace("\\", "/")
            },
            "PluginInfo": {
                "Version": self.plugin_pype_version,
                "Arguments": " ".join(args),
                "SingleFrameOnly": "True",
            },
            # Mandatory for Deadline, may be empty
            "AuxFiles": [],
        }

        # add assembly jobs as dependencies
        if instance.data.get("tileRendering"):
            self.log.info("Adding tile assembly jobs as dependencies...")
            job_index = 0
            for assembly_id in instance.data.get("assemblySubmissionJobs"):
                payload["JobInfo"]["JobDependency{}".format(
                    job_index)] = assembly_id  # noqa: E501
                job_index += 1
        elif instance.data.get("bakingSubmissionJobs"):
            self.log.info(
                "Adding baking submission jobs as dependencies..."
            )
            job_index = 0
            for assembly_id in instance.data["bakingSubmissionJobs"]:
                payload["JobInfo"]["JobDependency{}".format(
                    job_index)] = assembly_id  # noqa: E501
                job_index += 1
        ### Starts Alkemy-X Override ###
        # Make 'jobs' argument a list so we can pass multiple dependency jobs
        else:
            index = 0
            for job in jobs:
                if not job.get("_id"):
                    continue
                payload["JobInfo"][f"JobDependency{index}"] = job["_id"]
                # Hack to allow publish job to continue even if review task fails
                # because of this bug we have with monitored process in Nuke tasks
                # where the task shows as failed even though it completed
                payload["JobInfo"]["ResumeOnFailedDependencies"] = True
                index +=1
        ### Ends Alkemy-X Override ###

        for index, (key_, value_) in enumerate(environment.items()):
            payload["JobInfo"].update(
                {
                    "EnvironmentKeyValue%d"
                    % index: "{key}={value}".format(
                        key=key_, value=value_
                    )
                }
            )
        # remove secondary pool
        payload["JobInfo"].pop("SecondaryPool", None)

        self.log.debug("Submitting Deadline publish job ...")

        url = "{}/api/jobs".format(self.deadline_url)
        response = requests.post(url, json=payload, timeout=10)
        if not response.ok:
            raise Exception(response.text)

        deadline_publish_job_id = response.json()["_id"]

        return deadline_publish_job_id, metadata_path

    def process(self, instance):
        # type: (pyblish.api.Instance) -> None
        """Process plugin.

        Detect type of render farm submission and create and post dependent
        job in case of Deadline. It creates json file with metadata needed for
        publishing in directory of render.

        Args:
            instance (pyblish.api.Instance): Instance data.

        """
        if not instance.data.get("farm"):
            self.log.debug("Skipping local instance.")
            return

        anatomy = instance.context.data["anatomy"]

        instance_skeleton_data = create_skeleton_instance(
            instance, families_transfer=self.families_transfer,
            instance_transfer=self.instance_transfer, log=self.log)
        """
        if content of `expectedFiles` list are dictionaries, we will handle
        it as list of AOVs, creating instance for every one of them.

        Example:
        --------

        expectedFiles = [
            {
                "beauty": [
                    "foo_v01.0001.exr",
                    "foo_v01.0002.exr"
                ],

                "Z": [
                    "boo_v01.0001.exr",
                    "boo_v01.0002.exr"
                ]
            }
        ]

        This will create instances for `beauty` and `Z` subset
        adding those files to their respective representations.

        If we have only list of files, we collect all file sequences.
        More then one doesn't probably make sense, but we'll handle it
        like creating one instance with multiple representations.

        Example:
        --------

        expectedFiles = [
            "foo_v01.0001.exr",
            "foo_v01.0002.exr",
            "xxx_v01.0001.exr",
            "xxx_v01.0002.exr"
        ]

        This will result in one instance with two representations:
        `foo` and `xxx`
        """
        do_not_add_review = False
        if instance.data.get("review") is False:
            self.log.debug("Instance has review explicitly disabled.")
            do_not_add_review = True

        if isinstance(instance.data.get("expectedFiles")[0], dict):
            instances = create_instances_for_aov(
                instance, instance_skeleton_data,
                self.aov_filter, self.skip_integration_repre_list,
                do_not_add_review, self.log)
        else:
            representations = prepare_representations(
                instance_skeleton_data,
                instance.data.get("expectedFiles"),
                anatomy,
                self.aov_filter,
                self.skip_integration_repre_list,
                do_not_add_review,
                instance.context,
                self,
                self.log
            )

            if "representations" not in instance_skeleton_data.keys():
                instance_skeleton_data["representations"] = []

            # add representation
            instance_skeleton_data["representations"] += representations
            instances = [instance_skeleton_data]

        # attach instances to subset
        if instance.data.get("attachTo"):
            instances = attach_instances_to_subset(
                instance.data.get("attachTo"), instances
            )

        r''' SUBMiT PUBLiSH JOB 2 D34DLiN3
          ____
        '     '            .---.  .---. .--. .---. .--..--..--..--. .---.
        |     |   --= \   |  .  \/   _|/    \|  .  \  ||  ||   \  |/   _|
        | JOB |   --= /   |  |  ||  __|  ..  |  |  |  |;_ ||  \   ||  __|
        |     |           |____./ \.__|._||_.|___./|_____|||__|\__|\.___|
        ._____.

        '''
        ### Starts Alkemy-X Override ###
        # Make 'jobs' argument a list so we can pass multiple dependency jobs
        # If we have multiple submission jobs, we grab that key instead
        if "deadlineSubmissionJobs" in instance.data:
                render_jobs = instance.data.pop("deadlineSubmissionJobs", [])
        else:
                render_job = instance.data.pop("deadlineSubmissionJob", None)
                if render_job:
                    render_jobs = [render_job]

        if not render_jobs:
            import getpass

            render_job = {}
            self.log.debug("Faking job data ...")
            render_job["Props"] = {}
            # Render job doesn't exist because we do not have prior submission.
            # We still use data from it so lets fake it.
            #
            # Batch name reflect original scene name

            if instance.data.get("assemblySubmissionJobs"):
                render_job["Props"]["Batch"] = instance.data.get(
                    "jobBatchName")
            else:
                render_job["Props"]["Batch"] = os.path.basename(
                    instance.context.data.get("currentFile")
                )
            # User is deadline user
            render_job["Props"]["User"] = instance.context.data.get(
                "deadlineUser", getpass.getuser())

            render_job["Props"]["Env"] = {
                "FTRACK_API_USER": os.environ.get("FTRACK_API_USER"),
                "FTRACK_API_KEY": os.environ.get("FTRACK_API_KEY"),
                "FTRACK_SERVER": os.environ.get("FTRACK_SERVER"),
            }
            render_jobs = [render_job]
        ### Ends Alkemy-X Override ###

        # get default deadline webservice url from deadline module
        self.deadline_url = instance.context.data["defaultDeadline"]
        # if custom one is set in instance, use that
        if instance.data.get("deadlineUrl"):
            self.deadline_url = instance.data.get("deadlineUrl")
        assert self.deadline_url, "Requires Deadline Webservice URL"

        ### Starts Alkemy-X Override ###
        # Make 'jobs' argument a list so we can pass multiple dependency jobs
        deadline_publish_job_id, metadata_path = \
            self._submit_deadline_post_job(instance, render_jobs, instances)
        ### Ends Alkemy-X Override ###

        # Inject deadline url to instances.
        for inst in instances:
            inst["deadlineUrl"] = self.deadline_url

        # publish job file
        publish_job = {
            "asset": instance_skeleton_data["asset"],
            "frameStart": instance_skeleton_data["frameStart"],
            "frameEnd": instance_skeleton_data["frameEnd"],
            "fps": instance_skeleton_data["fps"],
            "source": instance_skeleton_data["source"],
            "user": instance.context.data["user"],
            "version": instance.context.data.get("version"),  # workfile version
            "intent": instance.context.data.get("intent"),
            "comment": instance.context.data.get("comment"),
            "job": render_jobs[0] or None,
            "session": legacy_io.Session.copy(),
            "instances": instances
        }

        if deadline_publish_job_id:
            publish_job["deadline_publish_job_id"] = deadline_publish_job_id

        # add audio to metadata file if available
        audio_file = instance.context.data.get("audioFile")
        if audio_file and os.path.isfile(audio_file):
            publish_job.update({"audio": audio_file})

        ### Starts Alkemy-X Override ###
        # Create metadata path only as we are adding a timestamp up to the
        # second and this caused it to have a missmatch in some cases
        # metadata_path, rootless_metadata_path = \
        #     create_metadata_path(instance, anatomy)
        ### Ends Alkemy-X Override ###

        with open(metadata_path, "w") as f:
            json.dump(publish_job, f, indent=4, sort_keys=True)

    def _get_publish_folder(self, anatomy, template_data,
                            asset, subset, context,
                            family, version=None):
        """
            Extracted logic to pre-calculate real publish folder, which is
            calculated in IntegrateNew inside of Deadline process.
            This should match logic in:
                'collect_anatomy_instance_data' - to
                    get correct anatomy, family, version for subset and
                'collect_resources_path'
                    get publish_path

        Args:
            anatomy (openpype.pipeline.anatomy.Anatomy):
            template_data (dict): pre-calculated collected data for process
            asset (string): asset name
            subset (string): subset name (actually group name of subset)
            family (string): for current deadline process it's always 'render'
                TODO - for generic use family needs to be dynamically
                    calculated like IntegrateNew does
            version (int): override version from instance if exists

        Returns:
            (string): publish folder where rendered and published files will
                be stored
                based on 'publish' template
        """

        project_name = context.data["projectName"]
        host_name = context.data["hostName"]
        if not version:
            version = get_last_version_by_subset_name(
                project_name,
                subset,
                asset_name=asset
            )
            if version:
                version = int(version["name"]) + 1
            else:
                version = get_versioning_start(
                    project_name,
                    host_name,
                    task_name=template_data["task"]["name"],
                    task_type=template_data["task"]["type"],
                    ### Starts Alkemy-X Override ###
                    # Remove hard-coded `render` family
                    family=family,
                    ### Ends Alkemy-X Override ###
                    subset=subset,
                    project_settings=context.data["project_settings"]
                )

        host_name = context.data["hostName"]
        task_info = template_data.get("task") or {}

        template_name = publish.get_publish_template_name(
            project_name,
            host_name,
            family,
            task_info.get("name"),
            task_info.get("type"),
        )

        template_data["subset"] = subset
        template_data["family"] = family
        template_data["version"] = version

        render_templates = anatomy.templates_obj[template_name]
        if "folder" in render_templates:
            publish_folder = render_templates["folder"].format_strict(
                template_data
            )
        else:
            # solve deprecated situation when `folder` key is not underneath
            # `publish` anatomy
            self.log.warning((
                "Deprecation warning: Anatomy does not have set `folder`"
                " key underneath `publish` (in global of for project `{}`)."
            ).format(project_name))

            file_path = render_templates["path"].format_strict(template_data)
            publish_folder = os.path.dirname(file_path)

        return publish_folder

    @classmethod
    def get_attribute_defs(cls):
        return [
            EnumDef("publishJobState",
                    label="Publish Job State",
                    items=["Active", "Suspended"],
                    default="Active")
        ]
