"""The C-Bus (C-Gate) integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .cgate import CGateClient, CGateError
from .const import (
    CONF_COMMAND_PORT,
    CONF_NETWORK,
    CONF_PROJECT,
    CONF_STATUS_PORT,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up C-Bus from a config entry."""
    client = CGateClient(
        host=entry.data[CONF_HOST],
        command_port=entry.data[CONF_COMMAND_PORT],
        status_port=entry.data[CONF_STATUS_PORT],
        project=entry.data[CONF_PROJECT],
        network=entry.data[CONF_NETWORK],
    )

    try:
        await client.async_start()
    except CGateError as err:
        _LOGGER.error("Failed to connect to C-Gate: %s", err)
        from homeassistant.exceptions import ConfigEntryNotReady

        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client: CGateClient = hass.data[DOMAIN].pop(entry.entry_id)
        await client.async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
