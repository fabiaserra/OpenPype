import getpass
import os
from urllib.parse import urlparse

import shotgun_api3
from shotgun_api3.shotgun import AuthenticationFault

from openpype.lib import Logger
from openpype.lib import OpenPypeSecureRegistry, OpenPypeSettingsRegistry
from openpype.modules.shotgrid.lib.record import Credentials
from openpype.modules.shotgrid.lib.settings import get_shotgrid_servers

logger = Logger.get_logger(__name__)


def _get_shotgrid_secure_key(hostname, key):
    """Secure item key for entered hostname."""
    return f"shotgrid/{hostname}/{key}"


def _get_secure_value_and_registry(
    hostname,
    name,
):
    key = _get_shotgrid_secure_key(hostname, name)
    registry = OpenPypeSecureRegistry(key)
    return registry.get_item(name, None), registry


def get_shotgrid_hostname(shotgrid_url):

    if not shotgrid_url:
        raise Exception("Shotgrid url cannot be a null")
    valid_shotgrid_url = (
        f"//{shotgrid_url}" if "//" not in shotgrid_url else shotgrid_url
    )
    return urlparse(valid_shotgrid_url).hostname


# Credentials storing function (using keyring)


def get_credentials(shotgrid_url):
    hostname = get_shotgrid_hostname(shotgrid_url)
    if not hostname:
        return None
    login_value, _ = _get_secure_value_and_registry(
        hostname,
        Credentials.login_key_prefix(),
    )
    password_value, _ = _get_secure_value_and_registry(
        hostname,
        Credentials.password_key_prefix(),
    )
    return Credentials(login_value, password_value)


def save_credentials(login, password, shotgrid_url):
    hostname = get_shotgrid_hostname(shotgrid_url)
    _, login_registry = _get_secure_value_and_registry(
        hostname,
        Credentials.login_key_prefix(),
    )
    _, password_registry = _get_secure_value_and_registry(
        hostname,
        Credentials.password_key_prefix(),
    )
    clear_credentials(shotgrid_url)
    login_registry.set_item(Credentials.login_key_prefix(), login)
    password_registry.set_item(Credentials.password_key_prefix(), password)


def clear_credentials(shotgrid_url):
    hostname = get_shotgrid_hostname(shotgrid_url)
    login_value, login_registry = _get_secure_value_and_registry(
        hostname,
        Credentials.login_key_prefix(),
    )
    password_value, password_registry = _get_secure_value_and_registry(
        hostname,
        Credentials.password_key_prefix(),
    )

    if login_value is not None:
        login_registry.delete_item(Credentials.login_key_prefix())

    if password_value is not None:
        password_registry.delete_item(Credentials.password_key_prefix())


# Login storing function (using json)


def get_local_login():
    reg = OpenPypeSettingsRegistry()
    try:
        return str(reg.get_item("shotgrid_login"))
    except Exception:
        return None


def save_local_login(login):
    reg = OpenPypeSettingsRegistry()
    reg.set_item("shotgrid_login", login)


def clear_local_login():
    reg = OpenPypeSettingsRegistry()
    reg.delete_item("shotgrid_login")


def check_credentials(
    login,
    password,
    shotgrid_url,
):

    if not shotgrid_url or not login or not password:
        return False
    try:
        proxy = os.environ.get("HTTPS_PROXY", "").lstrip("https://")
        session = shotgun_api3.Shotgun(
            shotgrid_url,
            login=login,
            password=password,
            http_proxy=proxy,
        )
        session.preferences_read()
        session.close()
    except AuthenticationFault:
        return False
    return True


### Starts Alkemy-X Override ###
def get_shotgrid_session():
    """Return a Shotgun API session object for the configured ShotGrid server.

    The function reads the ShotGrid server settings from the OpenPype
    configuration file and uses them to create a Shotgun API session object.

    Returns:
        A Shotgun API session object.
    """
    shotgrid_servers_settings = get_shotgrid_servers()

    shotgrid_server_setting = shotgrid_servers_settings.get("alkemyx", {})
    shotgrid_url = shotgrid_server_setting.get("shotgrid_url", "")

    shotgrid_script_name = shotgrid_server_setting.get("shotgrid_script_name", "")
    shotgrid_script_key = shotgrid_server_setting.get("shotgrid_script_key", "")
    if not shotgrid_script_name and not shotgrid_script_key:
        logger.error(
            "No Shotgrid API credential found, please enter "
            "script name and script key in OpenPype settings"
        )

    proxy = os.environ.get("HTTPS_PROXY", "").lstrip("https://")
    try:
        sg = shotgun_api3.Shotgun(
            shotgrid_url,
            script_name=shotgrid_script_name,
            api_key=shotgrid_script_key,
            http_proxy=proxy,
            sudo_as_login=getpass.getuser()
        )
        # Authentication test to proc error if bad
        sg.find("Project", [], [])
        return sg

    except shotgun_api3.shotgun.AuthenticationFault:
        return shotgun_api3.Shotgun(
            shotgrid_url,
            script_name=shotgrid_script_name,
            api_key=shotgrid_script_key,
            http_proxy=proxy,
            sudo_as_login=f"{getpass.getuser()}@alkemy-x.com"
        )
### Ends Alkemy-X Override ###
