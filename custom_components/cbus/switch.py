"""Switch platform for the C-Bus (direct CNI) integration.

Use this for non-dimmable C-Bus lighting groups driven by relay output units
(e.g. fans, exhausts, pumps, or any on/off load). Like the light platform, it
reflects real-time MONITOR-mode events from the CNI, so the switch state always
matches the bus.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SWITCH_GROUPS
from .helpers import CBusEntity, setup_group_platform
from .pci import PCIClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up C-Bus switches and keep them in sync with the options."""
    setup_group_platform(
        hass, entry, async_add_entities, CONF_SWITCH_GROUPS, CBusSwitch
    )


class CBusSwitch(CBusEntity, SwitchEntity):
    """A single C-Bus group exposed as an on/off switch."""

    def __init__(
        self, client: PCIClient, entry: ConfigEntry, group: int, name: str
    ) -> None:
        """Initialise the switch for a C-Bus group."""
        super().__init__(client, entry, group, name, "switch")

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
