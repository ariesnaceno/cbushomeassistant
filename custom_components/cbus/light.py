"""Light platform for the C-Bus (C-Gate) integration."""

from __future__ import annotations

import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cgate import CGateClient
from .const import CBUS_MAX_LEVEL, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_GROUPS = "groups"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up C-Bus lights from a config entry."""
    client: CGateClient = hass.data[DOMAIN][entry.entry_id]
    groups: dict[str, str] = entry.options.get(CONF_GROUPS, {})

    entities = [
        CBusLight(client, entry, int(group_id), name)
        for group_id, name in groups.items()
    ]

    # Pull the live level for every group before adding so the first state
    # Home Assistant shows already matches the bus.
    await client.async_refresh_all([int(g) for g in groups])

    async_add_entities(entities)


class CBusLight(LightEntity):
    """A single C-Bus lighting group exposed as a dimmable light."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(
        self,
        client: CGateClient,
        entry: ConfigEntry,
        group: int,
        name: str,
    ) -> None:
        """Initialise the light for a C-Bus group."""
        self._client = client
        self._group = group
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_group_{group}"
        self._unsub_update = None
        self._unsub_conn = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"C-Bus ({client.project})",
            manufacturer="Clipsal",
            model="C-Bus via C-Gate",
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
        """Return True only while C-Gate is connected."""
        return self._client.connected

    @property
    def is_on(self) -> bool:
        """Return True if the C-Bus group level is above zero."""
        return self._client.is_on(self._group)

    @property
    def brightness(self) -> int | None:
        """Return the brightness (0..255) mapped from the C-Bus level."""
        return self._client.get_level(self._group)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the group on, honouring brightness and transition."""
        level = kwargs.get(ATTR_BRIGHTNESS, CBUS_MAX_LEVEL)
        transition = kwargs.get(ATTR_TRANSITION)

        if transition is not None and transition > 0:
            await self._client.async_ramp(self._group, level, int(transition))
        else:
            await self._client.async_turn_on(self._group, level)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the group off, honouring transition."""
        transition = kwargs.get(ATTR_TRANSITION)
        if transition is not None and transition > 0:
            await self._client.async_ramp(self._group, 0, int(transition))
        else:
            await self._client.async_turn_off(self._group)
        self.async_write_ha_state()
