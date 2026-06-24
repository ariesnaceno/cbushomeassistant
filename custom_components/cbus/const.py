"""Constants for the C-Bus (direct CNI/PCI) integration."""

from __future__ import annotations

DOMAIN = "cbus"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"

# Option keys (per-platform group maps: {str(group_id): friendly_name})
CONF_GROUPS = "groups"
CONF_SWITCH_GROUPS = "switch_groups"
CONF_COVER_GROUPS = "cover_groups"

# A CNI's raw PCI serial-over-TCP port. Default for Clipsal CNI/CNI2.
DEFAULT_PORT = 10001

# C-Bus lighting application (decimal 56 / hex 0x38).
LIGHTING_APPLICATION = 0x38

# C-Bus group level range == Home Assistant brightness range (1:1).
CBUS_MIN_LEVEL = 0
CBUS_MAX_LEVEL = 255

# Dispatcher-style signals (used via the client's own callback registry).
SIGNAL_GROUP_UPDATE = f"{DOMAIN}_group_update"
SIGNAL_CONNECTION_UPDATE = f"{DOMAIN}_connection_update"

PLATFORMS = ["light", "switch", "cover"]
