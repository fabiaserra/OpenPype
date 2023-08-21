import pyblish.api


class IntegrateShotgridCutInfo(pyblish.api.InstancePlugin):
    """Gathers cut info from Cut Info tag data. That data is then updated on
    the shot entity in Shotgrid
    """

    order = pyblish.api.IntegratorOrder + 0.4999
    label = "Integrate Shotgrid Cut Info"
    hosts = ["hiero"]
    families = ["reference", "plate"]

    optional = True

    def process(self, instance):

        if instance.data.get("farm"):
            self.log.info(
                "Instance is marked to be processed on farm. Skipping")
            return

        context = instance.context
        self.sg = context.data.get("shotgridSession")
        shotgrid_version = instance.data.get("shotgridVersion")

        if not shotgrid_version:
            self.log.warning(
                "No Shotgrid version collect. Cut Info could not be integrated into shot"
            )
            return

        track_item = instance.data["item"]

        if not "cut_info_tag" in track_item.__dir__():
            return

        cut_info = track_item.cut_info()

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
        self.log.info(
            "Setting cut info on shot '{0}' - {1}".format(
                shotgrid_version["entity"]["name"], shot_data
            )
        )

        result = self.sg.update(
            "Shot",
            shotgrid_version["entity"]["id"],
            shot_data,
        )
        if not result:
            self.log.warning(
                "Failed to update shot cut information. Most likely SG connection was severed"
            )
