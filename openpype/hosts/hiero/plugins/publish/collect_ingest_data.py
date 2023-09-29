import pyblish.api


class CollectIngestData(pyblish.api.InstancePlugin):
    """Collect ingest information for plate or reference

    This plugin collects data related to ingest formatting for a given track item.
    It searches for information about ingest resolution and ingest effects.

    Returns: None

    Note:
        The collected data may include ingest resolution and ingest effects.
    """

    order = pyblish.api.CollectorOrder + .499
    label = "Collect Ingest Data"
    hosts = ["hiero"]
    families = ["plate", "reference"]

    def process(self, instance):
        context = instance.context

        track_item = instance.data["item"]

        cut_info_data = track_item.cut_info_data()
        instance.data["cut_info_data"] = cut_info_data
        if cut_info_data:
            self.log.info("Cut info found on instance track item: %s",
                          cut_info_data
            )
        else:
            self.log.info(
                "No cut info found on instance track item. Ignoring cut update"
            )

        sg_tags_data =  track_item.sg_tags_data()
        instance.data["sg_tags_data"] = sg_tags_data
        if sg_tags_data:
            self.log.info("Sg shot tags found on instance track item: %s",
                          sg_tags_data
            )
        else:
            self.log.info(
                "No sg shot tags found on instance track item. Ignoring sg"
                " shot tag update"
            )

        edit_note_data = track_item.edit_note_data()
        instance.data["edit_note_data"] = edit_note_data
        if edit_note_data:
            self.log.info("Edit note tag found on instance track item: %s",
                          edit_note_data
            )
        else:
            self.log.info(
                "No edit note tag found on instance track item. Ignoring edit"
                " note update"
            )

        self.collect_working_res(instance, track_item)
        self.collect_avalon_working_res(instance, track_item)
        self.collect_ingest_effect(instance, track_item)

    def collect_working_res(self, instance, track_item):
        """Hierarchial search for ingest resolution
        - Track Item Ingest Resolution Tag
        - Shotgrid Shot/Asset entity sg_ingest_resolution
        - Shotgrid Project entity sg_ingest_resolution
        - Track Item source resolution
        """
        track_item_format = track_item.source().format()

        ingest_resolution = {}
        if "ingest_res_data" in track_item.__dir__():
            ingest_resolution_data = track_item.ingest_res_data()
            if ingest_resolution_data:
                width, height = ingest_resolution_data["resolution"].split("x")
                ingest_resolution = {
                    "width": width,
                    "height": height,
                    "fr_width": track_item_format.width(),
                    "fr_height": track_item_format.height(),
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
            self.log.info(
                "No ingest resolution override applied for clip: '%s'",
                track_item.name()
            )


    def collect_avalon_working_res(self, instance, track_item):
        if instance.data["family"] == "reference":
            self.log.info(
                "Reference family set. Skipping working resolution "
                "integration."
            )
            return

        main_plate_track = None
        for track in track_item.sequence().videoTracks():
            if not "ref" in track.name():
                main_plate_track = track
                break

        if not main_plate_track:
            self.log.warning(
                "Could not determine main track in sequence. "
                "Ignoring working resolution collection"
            )
            return

        # Keep in mind that when track items are duplicated, sometimes, they
        # don't contain different object data. It's shared resulting in a
        # possibility that the real parent track might be impossible to know
        if not track_item.parentTrack() == main_plate_track:
            self.log.info(
                "Track Item track not determined to be main plate track. "
                "Ignoring working resolution collection"
            )
            return

        ingest_resolution = instance.data.get("ingest_resolution")
        if ingest_resolution:
            width = ingest_resolution["width"]
            height = ingest_resolution["height"]
            pixel_aspect = ingest_resolution["pixel_aspect"]
        else:
            track_item_format = track_item.source().format()
            width = track_item_format.width()
            height = track_item_format.height()
            pixel_aspect = track_item_format.pixelAspect()

        instance.data["asset_working_format"] = {
            "resolutionWidth": width,
            "resolutionHeight": height,
            "pixelAspect": pixel_aspect,
        }

        self.log.info("Shot/Asset working resolution found on track item: %s",
                      instance.data["asset_working_format"]
        )

    def collect_ingest_effect(self, instance, track_item):
        # Ingest effects has no plans to be controlled by a hierarchial search
        # Always comes from the ingest effects tag
        if "ingest_effects_data" in track_item.__dir__():
            ingest_effects_data = track_item.ingest_effects_data()
            if ingest_effects_data:
                instance.data["ingest_effects"] = ingest_effects_data
                self.log.info("Collected ingest effects: '%s' for '%s'", ingest_effects_data, track_item.name())
            else:
                self.log.info("No ingest effects override applied for clip: '%s'", track_item.name())
