import os

from openpype.pipeline import (
    load,
    get_representation_path,
    AVALON_CONTAINER_ID,
)
from openpype.hosts.houdini.api import lib, pipeline

import hou


def get_textures_avalon_container():
    """The Arnold Image nodes must be in a matnet network.

    So we maintain a single entry point within PRODUCTS,
    just for ease of use.

    """

    path = pipeline.PRODUCTS
    avalon_container = hou.node(path)
    if not avalon_container:
        # Let's create avalon container secretly
        # but make sure the pipeline still is built the
        # way we anticipate it was built, asserting it.
        assert path == "/obj/PRODUCTS"

        parent = hou.node("/obj")
        avalon_container = parent.createNode(
            "subnet", node_name="PRODUCTS"
        )

    matnet_node = hou.node(path + "/TEXTURES")
    if not matnet_node:
        matnet_node = avalon_container.createNode(
            "matnet", node_name="TEXTURES"
        )
        matnet_node.moveToGoodPosition()
        textures_container = matnet_node.createNode(
            "arnold_materialbuilder", node_name="TEXTURES"
        )

    return textures_container


class TextureLoader(load.LoaderPlugin):
    """Load textures through an Arnold Material Builder Image node

    TODO: Add support for other renderers and contexts
    """

    families = ["textures"]
    label = "Load Textures"
    representations = ["*"]
    order = -10

    icon = "code-fork"
    color = "orange"

    def load(self, context, name=None, namespace=None, data=None):

        # Format file name, Houdini only wants forward slashes
        file_path = self.filepath_from_context(context)
        file_path = os.path.normpath(file_path)
        file_path = file_path.replace("\\", "/")
        # TODO: add support for UDIMs replacing the udim token with "<UDIM>"

        # Get the root node
        parent = get_textures_avalon_container()

        # Define node name
        namespace = namespace if namespace else context["asset"]["name"]
        node_name = "{}_{}".format(namespace, name) if namespace else name

        node = parent.createNode("arnold::image", node_name=node_name)
        node.moveToGoodPosition()

        node.setParms({"filename": file_path})

        # Imprint it manually
        data = {
            "schema": "openpype:container-2.0",
            "id": AVALON_CONTAINER_ID,
            "name": node_name,
            "namespace": namespace,
            "loader": str(self.__class__.__name__),
            "representation": str(context["representation"]["_id"]),
        }

        # todo: add folder="Avalon"
        lib.imprint(node, data)

        return node

    def update(self, container, representation):

        node = container["node"]

        # Update the file path
        file_path = get_representation_path(representation)
        file_path = file_path.replace("\\", "/")
        # TODO: add support for UDIMs replacing the udim token with "<UDIM>"

        # Update attributes
        node.setParms(
            {
                "filename": file_path,
                "representation": str(representation["_id"]),
            }
        )

    def remove(self, container):

        node = container["node"]

        # Let's clean up the IMAGES COP2 network
        # if it ends up being empty and we deleted
        # the last file node. Store the parent
        # before we delete the node.
        parent = node.parent()

        node.destroy()

        if not parent.children():
            parent.destroy()

    def switch(self, container, representation):
        self.update(container, representation)