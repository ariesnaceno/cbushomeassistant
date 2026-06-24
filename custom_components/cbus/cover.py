"""Cover platform for the C-Bus (direct CNI) integration.

Use this for C-Bus blind / shutter / awning groups that are controlled through
the lighting application, where the group *level* represents the open position:
level 255 (100%) = fully open, level 0 = fully closed.

Position changes are driven with C-Bus ramp commands, and the open/closed
position is kept accurate from the CNI's real-time MONITOR-mode events — so the
cover reflects movement triggered from wall switches or scenes too.
"""

from __future__ import annotations

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CBUS_MAX_LEVEL, CONF_COVER_GROUPS
from .helpers import CBusEntity, setup_group_platform
from .pci import PCIClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up C-Bus covers and keep them in sync with the options."""
    setup_group_platform(
        hass, entry, async_add_entities, CONF_COVER_GROUPS, CBusCover
    )


class CBusCover(CBusEntity, CoverEntity):
    """A single C-Bus blind/shutter group exposed as a positionable cover."""

    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self, client: PCIClient, entry: ConfigEntry, group: int, name: str
    ) -> None:
        """Initialise the cover for a C-Bus group."""
        super().__init__(client, entry, group, name, "cover")

    @property
    def current_cover_position(self) -> int | None:
        """Return the cover position 0..100 mapped from the C-Bus level."""
        level = self._client.get_level(self._group)
        if level is None:
            return None
        return round(level / CBUS_MAX_LEVEL * 100)

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is fully closed (level 0)."""
        level = self._client.get_level(self._group)
        if level is None:
            return None
        return level == 0

    async def async_open_cover(self, **kwargs) -> None:
        """Open the cover fully."""
        await self._client.async_turn_on(self._group, CBUS_MAX_LEVEL)
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs) -> None:
        """Close the cover fully."""
        await self._client.async_turn_off(self._group)
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move the cover to a specific position (0..100)."""
        position = int(kwargs[ATTR_POSITION])
        level = round(position / 100 * CBUS_MAX_LEVEL)
        await self._client.async_turn_on(self._group, level)
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs) -> None:
        """Stop a moving cover by re-issuing its current level.

        Many C-Bus blind relay units halt travel when they receive a fresh
        level command equal to the current position, which terminates the
        in-progress ramp.
        """
        level = self._client.get_level(self._group)
        if level is None:
            return
        await self._client.async_ramp(self._group, level, 0)
        self.async_write_ha_state()
