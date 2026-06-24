"""Constants for the C-Bus (C-Gate) integration."""

from __future__ import annotations

DOMAIN = "cbus"

# Config entry keys
CONF_HOST = "host"
CONF_COMMAND_PORT = "command_port"
CONF_EVENT_PORT = "event_port"
CONF_STATUS_PORT = "status_port"
CONF_PROJECT = "project"
CONF_NETWORK = "network"

# C-Gate default TCP ports
DEFAULT_COMMAND_PORT = 20023  # send commands / query levels
DEFAULT_EVENT_PORT = 20024    # general C-Gate events
DEFAULT_STATUS_PORT = 20025   # real-time status change events
DEFAULT_NETWORK = 254

# C-Bus lighting application address (decimal 56 / hex 38)
LIGHTING_APPLICATION = 56

# C-Bus group level range
CBUS_MIN_LEVEL = 0
CBUS_MAX_LEVEL = 255

# Dispatcher signal: fired when a group's level changes.
SIGNAL_GROUP_UPDATE = f"{DOMAIN}_group_update"
# Dispatcher signal: fired when connection state changes.
SIGNAL_CONNECTION_UPDATE = f"{DOMAIN}_connection_update"

PLATFORMS = ["light"]
