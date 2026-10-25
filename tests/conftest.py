import sys

import pytest
import pytest_socket

if sys.platform == "win32":
    # Windows' asyncio event loop needs a localhost socketpair, and the real YouTube test needs
    # the network — the HA test plugin blocks both by default. (HA itself only runs on Linux;
    # this is only so the suite can also run on a Windows dev machine.)
    pytest_socket.disable_socket = lambda *args, **kwargs: None
    pytest_socket.socket_allow_hosts = lambda *args, **kwargs: None
    import pytest_homeassistant_custom_component.plugins as _ha_plugins

    for _name in ("disable_socket", "socket_allow_hosts"):
        if hasattr(_ha_plugins, _name):
            setattr(_ha_plugins, _name, lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
