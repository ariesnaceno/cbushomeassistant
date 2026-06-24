"""Cover platform for the C-Bus (direct CNI) integration.

Use this for C-Bus blind / shutter / awning groups that are controlled through
the lighting application, where the group *level* represents the open position:
level 255 (100%) = fully open, level 0 = fully closed.

Position changes are driven with C-Bus ramp commands, and the open/closed
position is kept accurate from the CNI's real-time MONITOR-mode events — so the
cover reflects movement triggered from wall switches or scenes too.
"""

from __future__ import annotations

import logging

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .pci import PCIClient
from .const import CBUS_MAX_LEVEL, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_COVER_GROUPS = "cover_groups"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up C-Bus covers from a config entry."""
    client: PCIClient = hass.data[DOMAIN][entry.entry_id]
    groups: dict[str, str] = entry.options.get(CONF_COVER_GROUPS, {})

    entities = [
        CBusCover(client, entry, int(group_id), name)
        for group_id, name in groups.items()
    ]

    await client.async_refresh_all([int(g) for g in groups])
    async_add_entities(entities)


class CBusCover(CoverEntity):
    """A single C-Bus blind/shutter group exposed as a positionable cover."""

    _attr_should_poll = False
    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        client: PCIClient,
        entry: ConfigEntry,
        group: int,
        name: str,
    ) -> None:
        """Initialise the cover for a C-Bus group."""
        self._client = client
        self._group = group
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_cover_{group}"
        self._unsub_update = None
        self._unsub_conn = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"C-Bus ({client.name})",
            manufacturer="Clipsal",
            model="C-Bus via CNI",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to real-time level and connection updates."""

        @callback
        def _on_group_update(group: int, level: int) -> None:
            if group == self._group:
                self.async_write_ha_state()

        @callback
        def _on_connection(_connected: bool) -> None:
            self.async_write_ha_state()

        self._unsub_update = self._client.register_update_callback(_on_group_update)
        self._unsub_conn = self._client.register_connection_callback(_on_connection)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from updates."""
        if self._unsub_update:
            self._unsub_update()
        if self._unsub_conn:
            self._unsub_conn()

    @property
    def available(self) -> bool:
        """Return True only while the CNI link is up."""
        return self._client.connected

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
