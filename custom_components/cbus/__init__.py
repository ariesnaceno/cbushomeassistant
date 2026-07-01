"""The C-Bus (direct CNI/PCI) integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PORT,
    CONF_RECOVERY_SWITCH,
    DOMAIN,
    PLATFORMS,
    RECOVERY_COOLDOWN_SECONDS,
    RECOVERY_OFF_SECONDS,
    RECOVERY_STALL_SECONDS,
    signal_options_updated,
)
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
    entry.async_on_unload(_setup_cni_recovery(hass, entry, client))

    # Close the CNI connection cleanly when Home Assistant shuts down or
    # restarts. A config entry is not always unloaded on restart, so without
    # this the socket would be abandoned and the CNI would hold the old session
    # as a zombie — forcing a CNI power-cycle before HA could reconnect. The
    # abortive close (see pci.py) makes the CNI release the session immediately.
    async def _async_on_ha_stop(_event: Event) -> None:
        await client.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_ha_stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Always drop the client reference, and never let a failure while
        # closing the socket propagate: if async_unload_entry raised, HA would
        # leave the entry stuck in the non-recoverable FAILED_UNLOAD state
        # (only a Core restart clears it). Closing is best-effort.
        client: PCIClient = hass.data[DOMAIN].pop(entry.entry_id)
        try:
            await client.async_stop()
        except Exception:  # noqa: BLE001 - unload must not fail on cleanup
            _LOGGER.exception("Error stopping C-Bus client during unload")
    return unload_ok


@callback
def _setup_cni_recovery(hass: HomeAssistant, entry: ConfigEntry, client: PCIClient):
    """Power-cycle a configured switch when the CNI is stuck.

    Some CNIs hold their single TCP session across a client disconnect, so after
    a reboot/power event the relay can't reconnect until the CNI is
    power-cycled. If the user has picked a recovery switch (e.g. a smart plug
    powering the CNI), we toggle it off/on once the connection has been down for
    RECOVERY_STALL_SECONDS, then let the relay reconnect. A cooldown prevents
    rapid cycling, and normal HA restarts (which recover in seconds) never reach
    the stall threshold.

    Returns an unsubscribe callable for entry.async_on_unload.
    """
    state = {"down_since": None, "last_cycle": None, "busy": False}

    @callback
    def _on_connection(connected: bool) -> None:
        state["down_since"] = None if connected else (
            state["down_since"] or dt_util.utcnow()
        )

    if not client.connected:
        state["down_since"] = dt_util.utcnow()
    unsub_conn = client.register_connection_callback(_on_connection)

    async def _maybe_recover(now) -> None:
        # Read the switch live so enabling it via Options takes effect without
        # a reload.
        switch = entry.options.get(CONF_RECOVERY_SWITCH)
        if not switch or state["busy"] or state["down_since"] is None:
            return
        if (now - state["down_since"]).total_seconds() < RECOVERY_STALL_SECONDS:
            return
        if state["last_cycle"] and (
            now - state["last_cycle"]
        ).total_seconds() < RECOVERY_COOLDOWN_SECONDS:
            return

        state["busy"] = True
        state["last_cycle"] = now
        _LOGGER.warning(
            "C-Bus CNI unreachable for >%ss; power-cycling recovery switch %s",
            RECOVERY_STALL_SECONDS,
            switch,
        )
        try:
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": switch}, blocking=True
            )
            await asyncio.sleep(RECOVERY_OFF_SECONDS)
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": switch}, blocking=True
            )
        except Exception:  # noqa: BLE001 - recovery must never crash the loop
            _LOGGER.exception("C-Bus CNI recovery power-cycle failed")
        finally:
            state["busy"] = False

    unsub_timer = async_track_time_interval(
        hass, _maybe_recover, timedelta(seconds=30)
    )

    @callback
    def _unsub() -> None:
        unsub_conn()
        unsub_timer()

    return _unsub


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option (group) changes without reloading.

    A full reload would tear down and re-open the CNI connection, which a CNI
    can briefly reject ("already in use") while it releases the old session.
    Instead we signal the platforms to reconcile their entities in place, so
    editing groups never disturbs the live connection.
    """
    async_dispatcher_send(hass, signal_options_updated(entry.entry_id))
