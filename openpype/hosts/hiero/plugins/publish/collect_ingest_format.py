import pyblish.api


class CollectIngestFormat(pyblish.api.InstancePlugin):
    """Collect ingest formatting information for plate or reference

    This plugin collects data related to ingest formatting for a given track item.
    It searches for information about ingest resolution and ingest effects.

    Returns: None

    Note:
        The collected data may include ingest resolution and ingest effects.
    """

    order = pyblish.api.CollectorOrder
    label = "Collect Ingest Format"
    hosts = ["hiero"]
    families = ["plate", "reference"]

    def process(self, instance):
        context = instance.context

        # Hierarchial search for ingest resolution
        #   Track Item Ingest Resolution Tag
        #   Shotgrid Shot/Asset entity sg_ingest_resolution
        #   Shotgrid Project entity sg_ingest_resolution
        #   Track Item source resolution

        track_item = instance.data["item"]
        track_item_format = track_item.source().format()

        ingest_resolution = {}
        if "ingest_res_data" in track_item.__dir__():
            ingest_resolution_data = track_item.ingest_res_data()
            if ingest_resolution_data:
                width, height = ingest_resolution_data["resolution"].split("x")
                ingest_resolution = {
                    "width": width,
                    "height": height,
                    "pixel_aspect": track_item_format.pixelAspect(),
                    "resize_type": ingest_resolution_data["resize"],
                }
                instance.data["ingest_resolution"] = ingest_resolution

        # Skipping SG Project and Shot resolution for now. It's not setup
        # properly. Only has width and height, but needs pixel aspect,
        # resize type, and crop type
        # sg = context.data.get("shotgridSession")

        if ingest_resolution:
            self.log.info("Collected ingest resolution: '%s'", ingest_resolution)
        else:
            # Use source resolution and disregard ingest_resolution
            self.log.info("No ingest resolution override applied for clip: '%s'", track_item.name())

        # Ingest effects has no plans to be controlled by a hierarchial search
        # Always comes from the ingest effects tag
        if "ingest_effects_data" in track_item.__dir__():
            ingest_effects_data = track_item.ingest_effects_data()
            if ingest_effects_data:
                instance.data["ingest_effects"] = ingest_effects_data
                self.log.info("Collected ingest effects: '%s' for '%s'", ingest_effects_data, track_item.name())
            else:
                self.log.info("No ingest effects override applied for clip: '%s'", track_item.name())
