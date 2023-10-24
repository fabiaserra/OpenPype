import getpass
import re

from openpype.client.operations import OperationsSession
from openpype.lib import run_subprocess
from openpype.pipeline.context_tools import get_current_project_name
import pyblish.api


class IntegrateShotgridShotData(pyblish.api.InstancePlugin):
    """This plugin gathers various data from the ingest process and updates
    the corresponding Shotgrid Shot entity with this data.

    It performs updates on: cut information, shot tags, working
    resolution, and editing notes.
    """

    order = pyblish.api.IntegratorOrder + 0.4999
    label = "Integrate Shotgrid Shot Data"
    families = ["reference", "plate"]

    optional = True
    sg_tags = {
        "retime": {"id": 245, "name": "retime", "type": "Tag"},
        "repo": {"id": 424, "name": "Pushin/Repo", "type": "Tag"},
        "insert": {"id": 244, "name": "Screen Insert", "type": "Tag"},
        "split": {"id": 423, "name": "Split Screen", "type": "Tag"},
    }
    sg_batch = []

    def process(self, instance):
        if instance.data.get("farm"):
            self.log.info(
                "Instance is marked to be processed on farm. Skipping"
            )
            return

        context = instance.context
        self.sg = context.data.get("shotgridSession")
        shotgrid_version = instance.data.get("shotgridVersion")

        if not shotgrid_version:
            self.log.warning(
                "No Shotgrid version collected. Collected shot data could not be integrated into Shotgrid"
            )
            return

        sg_shot = shotgrid_version.get("entity")
        if not sg_shot:
            self.log.warning(
                "Entity doesn't exist on shotgridVersion. Collected shot data could not be integrated into Shotgrid"
            )
            return

        if instance.data.get("main_ref"):
            self.update_cut_info(instance, sg_shot)
        self.update_shot_tags(instance, sg_shot)
        self.update_working_resolution(instance, sg_shot)
        self.update_edit_note(instance, sg_shot)

        result = self.sg.batch(self.sg_batch)
        if not result:
            self.log.warning(
                "Failed to update data on Shotgrid Shot '%s'", sg_shot["name"]
            )
            return

        for batch in self.sg_batch:
            self.log.info(
                # Using format as there is a weird bug with %sd
                "{0}d data as {1} on Shot '{2}' : {3}".format(
                    batch["request_type"].capitalize(),
                    batch["entity_type"],
                    sg_shot["name"],
                    batch["data"],
                )
            )

    def update_cut_info(self, instance, sg_shot):
        # Check if track item had attached cut_info_data method
        cut_info = instance.data.get("cut_info_data")
        if not cut_info:
            return

        elif "None" in cut_info.values():
            self.log.warning(
                "None values found in cut info. Please fix - "
                "Skipping cut in update"
            )
            return

        cut_in = int(cut_info["cut_in"])
        cut_out = int(cut_info["cut_out"])
        head_in = cut_in - int(cut_info["head_handles"])
        tail_out = cut_out + int(cut_info["tail_handles"])

        shot_data = {
            "sg_cut_in": cut_in,
            "sg_cut_out": cut_out,
            "sg_head_in": head_in,
            "sg_tail_out": tail_out,
        }

        cut_info_batch = {
            "request_type": "update",
            "entity_type": "Shot",
            "entity_id": sg_shot["id"],
            "data": shot_data,
        }
        self.sg_batch.append(cut_info_batch)

    def update_shot_tags(self, instance, sg_shot):
        # Check if track item had attached sg_tags_data method
        sg_tag_data = instance.data.get("sg_tags_data")
        if not sg_tag_data:
            return

        tag_updates = []
        for key, tag in self.sg_tags.items():
            # Need to make sure the icons are sorted for easy readability
            if sg_tag_data[key] == "True":
                tag_updates.append(tag)

        # Compare tag_updates to current tags
        shot_tags = self.sg.find_one(
            "Shot", [["id", "is", sg_shot["id"]]], ["code", "tags"]
        ).get("tags")

        current_tag_ids = set(tag["id"] for tag in shot_tags)
        tag_update_ids = set(tag["id"] for tag in tag_updates)

        if not tag_updates:
            self.log.info("No shot tag updates needed")
            return

        if not current_tag_ids.difference(tag_update_ids):
            current_tag_names = ", ".join([tag["name"] for tag in shot_tags])
            self.log.info(
                "No shot tag updates needed. Current shot tags: %s",
                current_tag_names,
            )
            return

        sg_tag_batch = {
            "request_type": "update",
            "entity_type": "Shot",
            "entity_id": sg_shot["id"],
            "data": {"tags": tag_updates},
        }
        self.sg_batch.append(sg_tag_batch)

    def update_working_resolution(self, instance, sg_shot):
        self.log.info("Integrating Working Resolution")
        if not instance.data.get("main_plate"):
            self.log.info("Skipping working resolution integration. Not main plate")
            return

        representations = instance.data["representations"]
        if not "exr" in representations:
            self.log.info("No exr representation found")
            return

        representation = representations["exr"]
        transcoded_frame = representation["files"][-1]

        # Grab width, height, and pixel aspect from last frame with exrheader
        command_args = ["/sw/bin/exrheader", transcoded_frame]
        result = run_subprocess(command_args)

        oiio_xyrt = r"displayWindow.+?: \((?P<x>\d+) (?P<y>\d+)\) - \((?P<r>\d+) (?P<t>\d+)+\)"
        xyrt_match = re.search(oiio_xyrt, result)
        if not xyrt_match:
            self.log.info("Could not parse width/height from ingest exr: %s", transcoded_frame)
            return

        x, y, r, t = map(int, xyrt_match.groups())
        width = r - x + 1
        height = t - y + 1
        pixel_aspect = int(result.split("pixelAspectRatio")[-1].split("\n")[0].split(": ")[-1]) or 1

        # Update shot/asset doc with proper working res.
        asset_doc = instance.data["assetEntity"]
        asset_doc["data"].update(
            {
                "resolutionWidth": width,
                "resolutionHeight": height,
                "pixelAspect": pixel_aspect,
            }
        )

        project_name = get_current_project_name()
        op_session = OperationsSession()
        op_session.update_entity(
            project_name, asset_doc["type"], asset_doc["_id"], asset_doc
        )
        op_session.commit()

        # Also update Shotgrid shot fields
        working_res_batch = {
            "request_type": "update",
            "entity_type": "Shot",
            "entity_id": sg_shot["id"],
            "data": {
                "sg_resolution_width": width,
                "sg_resolution_height": height,
                "sg_pixel_aspect": float(pixel_aspect),
            },
        }
        self.sg_batch.append(working_res_batch)

    def update_edit_note(self, instance, sg_shot):
        # Check if track item had attached edit_note_data method
        edit_note_text = instance.data.get("edit_note_data", {}).get("Note")
        if not edit_note_text:
            return

        filters = [["note_links", "is", {"type": "Shot", "id": sg_shot["id"]}]]
        fields = ["id", "content", "user", "created_at"]
        notes = self.sg.find("Note", filters, fields)
        # Check to see if the note was already made. If so skip
        for note in notes:
            if note["content"] == edit_note_text:
                self.log.info(
                    f"No editorial note made. Note already exists: "
                    "{edit_note_text}"
                )
                return

        sg_user = self.sg.find_one(
            "HumanUser", [["name", "contains", getpass.getuser()]], ["name"]
        )
        sg_project_id = self.sg.find_one(
            "Shot", ["id", "is", sg_shot["id"]], ["project.Project.id"]
        ).get("project.Project.id")
        note_data = {
            "project": {"type": "Project", "id": sg_project_id},
            "note_links": [{"type": "Shot", "id": sg_shot["id"]}],
            "subject": "Editorial Note",
            "content": edit_note_text,
            "user": {"type": "HumanUser", "id": sg_user["id"]},
        }

        edit_note_batch = {
            "request_type": "create",
            "entity_type": "Note",
            "data": {"tags": note_data},
        }

        self.sg_batch.append(edit_note_batch)
