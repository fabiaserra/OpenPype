from openpype.modules.shotgrid.lib import credentials

from openpype.pipeline.context_tools import (get_current_project_name)

SG = credentials.get_shotgrid_session()


# class OutsourcePackage():
#     def __init__():
#         pass
#         # outsource_type


# class OutsourceItem():
#     """Items make up an outsource package

#     Items can have types and belong to entities

#     For instance if we have a Roto Item that belongs to shot ABC_101_100_010

#     Item is a single representation.
#     This could be an exr frame range, mov,
#     """
#     operations = []
#     def __init__(kind, link, path):
#         # all contents are of the same kind and have different representations
#         self.item_path = path

#         if kind == "roto":
#             self.operations.append["copy"]
#         elif kind == "track":
#             self.operations.append["copy", "review"]
#         elif kind == "paint":
#             self.operations.append["copy", "review"]

#     def process(self):
#         if "copy" in self.operations:



# each item can have multiple representations or versions to process


# Each outsource item

# # have package run through plugins like OP?



# Roto package
#  - shot bla
#     - roto matte green
#     - roto matte blue




def validate_path(path):
    sg_codes = [x['sg_code'] for x in SG.find('Project', [], ['sg_code']) if x.get("sg_code")]
    project_dirs

    server = AX_PROJS_ROOT + '/'
    msg = 'Enter path to package: '
    while True:
        print('')
        package_path = user_input(msg).replace('"', '').replace("'", '').replace('\\', '/').strip()
        # Account for when symlink is used
        if package_path.startswith('Projects/'):
            package_path = package_path.replace('Projects/', server)
        elif package_path.startswith('/Projects/'):
            package_path = package_path.replace('/Projects/', server)

        # Support cross platform paths
        package_path = package_path.replace("/mnt/ol03/Projects/", server).replace("/Volumes/ol03/Projects/", server)

        # If user accidently adds slash
        if package_path.endswith('/'):
            package_path = package_path[:-1]
        msg = '\nRe-enter Name/Path to Package: '
        print('')

        print('Input package path -> ' + package_path)
        if package_path:
            if not server in package_path:
                print(color.yellow('Package must be on server'))
                continue
            else:
                location = package_path.split(server)[-1]

            if len(location) > 1:
                if not location.split('/')[0] in project_dirs:
                    print(color.yellow('Package must be in a show directory'))
                    continue

            if len(location) > 2:
                if not location.split('/')[1] == '_editorial':
                    print(color.yellow('Package must be in _editorial directory'))
                    continue
            if len(location) > 3:
                if not location.split('/')[2] == '_incoming':
                    print(color.yellow('Package must be in _incoming directory'))
                    continue
            if len(location) > 4:
                if not location.split('/')[3].isdigit() and len(location.split('/')[3]) == 6:
                    print(color.yellow('Package must be in dated folder directory'))
                    continue
            if not os.path.isdir(package_path):
                print(color.yellow("Invalid folder"))
                continue
        else:
            print(color.red("Nothing entered"))
            continue

        return package_path


# def breakout_items(package_path):

def ingest_outsource():

    proj_root = pathlib.Path(os.getenv("AX_PROJ_ROOT"))
    while True:
        package_path = pathlib.Path(input("Enter outsource package path: "))

        if not path.is_relative_to(proj_root):
            print("Package must live in '{proj_root}'")
            continue
        break
        # if validate_path(path):
        #     break

    input("Enter outsource package type: ")


    project_code = package_path.parts[2]


    # package for now only contains one item?
    # need to make so that a package contains multiple items


    # breakout_items(package_path):


    # sg_shots = get_sg_shots(get_current_project_name)
