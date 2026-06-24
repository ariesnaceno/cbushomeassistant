"""The C-Bus (direct CNI/PCI) integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_PORT, DOMAIN, PLATFORMS, signal_options_updated
from .pci import PCIClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up C-Bus from a config entry."""
    client = PCIClient(host=entry.data[CONF_HOST], port=entry.data[CONF_PORT])

    try:
        await client.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(f"Cannot reach CNI: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client: PCIClient = hass.data[DOMAIN].pop(entry.entry_id)
        await client.async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option (group) changes without reloading.

    A full reload would tear down and re-open the CNI connection, which a CNI
    can briefly reject ("already in use") while it releases the old session.
    Instead we signal the platforms to reconcile their entities in place, so
    editing groups never disturbs the live connection.
    """
    async_dispatcher_send(hass, signal_options_updated(entry.entry_id))
