import os

from openpype.modules import OpenPypeModule, IHostAddon


MARI_HOST_DIR = os.path.dirname(os.path.abspath(__file__))


class MariAddon(OpenPypeModule, IHostAddon):
    name = "mari"
    host_name = "mari"

    def initialize(self, module_settings):
        self.enabled = True

    def add_implementation_envs(self, env, _app):
        # Add requirements to MARI_SCRIPT_PATH
        startup_path = os.path.join(MARI_HOST_DIR, "startup")
        env["MARI_SCRIPT_PATH"] = os.pathsep.join(startup_path)

    def get_launch_hook_paths(self, app):
        if app.host_name != self.host_name:
            return []
        return [
            os.path.join(MARI_HOST_DIR, "hooks")
        ]

    def get_workfile_extensions(self):
        return [".mra"]
