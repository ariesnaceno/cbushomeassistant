"""Switch platform for the C-Bus (direct CNI) integration.

Use this for non-dimmable C-Bus lighting groups driven by relay output units
(e.g. fans, exhausts, pumps, or any on/off load). Like the light platform, it
reflects real-time MONITOR-mode events from the CNI, so the switch state always
matches the bus.
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .pci import PCIClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_SWITCH_GROUPS = "switch_groups"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up C-Bus switches from a config entry."""
    client: PCIClient = hass.data[DOMAIN][entry.entry_id]
    groups: dict[str, str] = entry.options.get(CONF_SWITCH_GROUPS, {})

    entities = [
        CBusSwitch(client, entry, int(group_id), name)
        for group_id, name in groups.items()
    ]

    await client.async_refresh_all([int(g) for g in groups])
    async_add_entities(entities)


class CBusSwitch(SwitchEntity):
    """A single C-Bus group exposed as an on/off switch."""

    _attr_should_poll = False

    def __init__(
        self,
        client: PCIClient,
        entry: ConfigEntry,
        group: int,
        name: str,
    ) -> None:
        """Initialise the switch for a C-Bus group."""
        self._client = client
        self._group = group
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_switch_{group}"
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
    def is_on(self) -> bool:
        """Return True if the C-Bus group level is above zero."""
        return self._client.is_on(self._group)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the group on."""
        await self._client.async_turn_on(self._group)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the group off."""
        await self._client.async_turn_off(self._group)
        self.async_write_ha_state()
