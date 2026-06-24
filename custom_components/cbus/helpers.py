"""Shared helpers for C-Bus group entities and dynamic platform setup."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, signal_options_updated
from .pci import PCIClient


class CBusEntity:
    """Common behaviour for all C-Bus group entities (light/switch/cover).

    This is a mixin: concrete classes also inherit the relevant Home Assistant
    entity type (LightEntity, SwitchEntity, CoverEntity).
    """

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self,
        client: PCIClient,
        entry: ConfigEntry,
        group: int,
        name: str,
        unique_suffix: str,
    ) -> None:
        """Initialise a C-Bus group entity."""
        self._client = client
        self._group = group
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}_{group}"
        self._unsub_update: Callable[[], None] | None = None
        self._unsub_conn: Callable[[], None] | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"C-Bus ({client.name})",
            manufacturer="Clipsal",
            model="C-Bus via CNI",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to real-time level and connection updates."""

        @callback
        def _on_group_update(group: int, _level: int) -> None:
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

    @callback
    def update_cbus_name(self, name: str) -> None:
        """Update the friendly name if it changed (no entity recreation)."""
        if name and name != self._attr_name:
            self._attr_name = name
            if self.hass is not None:
                self.async_write_ha_state()


@callback
def setup_group_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    option_key: str,
    factory: Callable[[PCIClient, ConfigEntry, int, str], CBusEntity],
) -> None:
    """Create entities for a platform and keep them in sync with the options.

    Adds/removes/renames entities when the group options change — *without*
    reloading the integration, so the CNI connection is never dropped.
    """
    client: PCIClient = hass.data[DOMAIN][entry.entry_id]
    known: dict[int, CBusEntity] = {}

    @callback
    def _reconcile() -> None:
        groups: dict[str, str] = entry.options.get(option_key, {})
        want = {int(addr): name for addr, name in groups.items()}

        to_add: list[CBusEntity] = []
        for addr, name in want.items():
            existing = known.get(addr)
            if existing is None:
                entity = factory(client, entry, addr, name)
                known[addr] = entity
                to_add.append(entity)
            else:
                existing.update_cbus_name(name)
        if to_add:
            async_add_entities(to_add)

        for addr in list(known):
            if addr not in want:
                entity = known.pop(addr)
                hass.async_create_task(entity.async_remove(force_remove=True))

    _reconcile()
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_options_updated(entry.entry_id), _reconcile
        )
    )
