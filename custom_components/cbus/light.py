"""Light platform for the C-Bus (direct CNI) integration.

Dimmable C-Bus lighting groups. State and availability track the live CNI
connection (SMART+MONITOR mode), so wall-switch changes are reflected too.
"""

from __future__ import annotations

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CBUS_MAX_LEVEL, CONF_GROUPS
from .helpers import CBusEntity, setup_group_platform
from .pci import PCIClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up C-Bus lights and keep them in sync with the options."""
    setup_group_platform(hass, entry, async_add_entities, CONF_GROUPS, CBusLight)


class CBusLight(CBusEntity, LightEntity):
    """A single C-Bus lighting group exposed as a dimmable light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(
        self, client: PCIClient, entry: ConfigEntry, group: int, name: str
    ) -> None:
        """Initialise the light for a C-Bus group."""
        # "group" suffix preserves unique_ids created by earlier versions.
        super().__init__(client, entry, group, name, "group")

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
