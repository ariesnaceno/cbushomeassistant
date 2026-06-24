"""Direct C-Bus CNI/PCI client for Home Assistant.

This talks straight to a C-Bus CNI (or PCI) over TCP, speaking the raw C-Bus
serial protocol — no C-Gate server required. It is built on top of the vendored
``cbus`` protocol library (see ``vendor/`` and ``vendor/NOTICE.md``).

Accuracy: the PCI is placed into SMART + MONITOR mode by the library's
``pci_reset()``. In this mode the CNI reports *every* lighting change on the
bus — whether it originated from Home Assistant, a wall switch, a scene, a PIR,
or any other unit — so Home Assistant's state always tracks the real bus state.

The client maintains a per-group level cache, fires callbacks on changes, and
automatically reconnects (re-running the init sequence) if the link drops.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Callable

from .const import CBUS_MAX_LEVEL

# The vendored ``cbus`` package is imported by putting ``vendor/`` on sys.path.
# ``cbus`` is not published on PyPI, so this keeps the integration self-contained
# and offline-installable. Files under vendor/ are unmodified (LGPL, attributed).
_VENDOR_DIR = os.path.join(os.path.dirname(__file__), "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

from cbus.protocol.pciprotocol import PCIProtocol  # noqa: E402

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY = 5.0  # seconds between reconnection attempts


class _HAProtocol(PCIProtocol):
    """PCIProtocol subclass that routes bus events into the HA client."""

    def __init__(self, client: "PCIClient", **kwargs) -> None:
        """Store a back-reference to the owning client."""
        self._client = client
        super().__init__(**kwargs)

    def on_lighting_group_on(self, source_addr: int, group_addr: int) -> None:
        self._client._apply_level(group_addr, CBUS_MAX_LEVEL)

    def on_lighting_group_off(self, source_addr: int, group_addr: int) -> None:
        self._client._apply_level(group_addr, 0)

    def on_lighting_group_ramp(
        self, source_addr: int, group_addr: int, duration: int, level: int
    ) -> None:
        self._client._apply_level(group_addr, level)

    def on_lighting_group_terminate_ramp(
        self, source_addr: int, group_addr: int
    ) -> None:
        # The group holds whatever level it had reached; nothing to change here.
        _LOGGER.debug("Group %s terminated ramp", group_addr)


class PCIClient:
    """Manage the TCP link to a CNI and the C-Bus group state cache."""

    def __init__(self, host: str, port: int) -> None:
        """Initialise the client. Call async_start() to connect."""
        self._host = host
        self._port = port
        self._protocol: _HAProtocol | None = None
        self._connect_task: asyncio.Task | None = None
        self._closing = False
        self._connected = False

        # group id -> last known level (0..255). Absent = unknown.
        self._levels: dict[int, int] = {}
        self._update_callbacks: list[Callable[[int, int], None]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []

    # ------------------------------------------------------------------
    # Public surface (mirrors the previous C-Gate client)
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        """Return whether the CNI link is currently up."""
        return self._connected

    @property
    def name(self) -> str:
        """Return a human-friendly label for the C-Bus connection/device."""
        return f"{self._host}:{self._port}"

    def register_update_callback(
        self, callback: Callable[[int, int], None]
    ) -> Callable[[], None]:
        """Register a callback for group level changes; returns an unsubscribe."""
        self._update_callbacks.append(callback)

        def _unsub() -> None:
            if callback in self._update_callbacks:
                self._update_callbacks.remove(callback)

        return _unsub

    def register_connection_callback(
        self, callback: Callable[[bool], None]
    ) -> Callable[[], None]:
        """Register a callback for connection-state changes."""
        self._connection_callbacks.append(callback)

        def _unsub() -> None:
            if callback in self._connection_callbacks:
                self._connection_callbacks.remove(callback)

        return _unsub

    def get_level(self, group: int) -> int | None:
        """Return the cached level for a group, or None if unknown."""
        return self._levels.get(group)

    def is_on(self, group: int) -> bool:
        """Return True if the group's cached level is greater than zero."""
        return self._levels.get(group, 0) > 0

    async def async_start(self) -> None:
        """Open the connection and keep it alive in the background."""
        loop = asyncio.get_running_loop()
        self._connect_task = loop.create_task(self._connect_loop())
        # Give the first connection attempt a moment so setup can fail fast
        # if the CNI is unreachable.
        await asyncio.sleep(0)

    async def async_stop(self) -> None:
        """Close the connection and stop reconnecting."""
        self._closing = True
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        self._teardown_protocol()

    async def async_turn_on(self, group: int, level: int = CBUS_MAX_LEVEL) -> None:
        """Turn a group on, optionally to a specific level (0..255)."""
        level = max(0, min(CBUS_MAX_LEVEL, int(level)))
        proto = self._require_protocol()
        if level >= CBUS_MAX_LEVEL:
            proto.lighting_group_on(group)
        else:
            # Instant ramp to the requested level.
            proto.lighting_group_ramp(group, 0, level)
        self._apply_level(group, level)

    async def async_turn_off(self, group: int) -> None:
        """Turn a group off."""
        self._require_protocol().lighting_group_off(group)
        self._apply_level(group, 0)

    async def async_ramp(self, group: int, level: int, seconds: int) -> None:
        """Ramp a group to a level over the given number of seconds."""
        level = max(0, min(CBUS_MAX_LEVEL, int(level)))
        self._require_protocol().lighting_group_ramp(group, int(seconds), level)
        self._apply_level(group, level)

    async def async_refresh_all(self, groups: list[int]) -> None:
        """No-op for direct CNI: state is learned from MONITOR-mode events.

        Unlike C-Gate there is no reliable level database to poll, so group
        state is unknown until the first monitored event (or HA command). Kept
        for interface compatibility with the platforms.
        """
        return None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    async def _connect_loop(self) -> None:
        """Connect, run the init sequence, and reconnect forever on drop."""
        loop = asyncio.get_running_loop()
        while not self._closing:
            lost_future: asyncio.Future = loop.create_future()
            try:
                _transport, protocol = await loop.create_connection(
                    lambda: _HAProtocol(
                        self,
                        timesync_frequency=0,  # don't drive the bus clock
                        handle_clock_requests=False,
                        connection_lost_future=lost_future,
                    ),
                    self._host,
                    self._port,
                )
            except OSError as err:
                _LOGGER.warning(
                    "Cannot connect to CNI %s:%s (%s); retrying in %ss",
                    self._host,
                    self._port,
                    err,
                    _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)
                continue

            self._protocol = protocol  # type: ignore[assignment]
            self._set_connected(True)
            _LOGGER.info(
                "Connected to C-Bus CNI %s:%s (SMART+MONITOR mode)",
                self._host,
                self._port,
            )

            try:
                await lost_future  # resolves when the connection drops
            except asyncio.CancelledError:
                raise
            finally:
                self._set_connected(False)
                self._protocol = None

            if not self._closing:
                _LOGGER.warning(
                    "C-Bus CNI link dropped; reconnecting in %ss", _RECONNECT_DELAY
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    def _teardown_protocol(self) -> None:
        """Best-effort close of the transport."""
        proto = self._protocol
        self._protocol = None
        if proto is not None and proto._transport is not None:  # noqa: SLF001
            try:
                proto._transport.close()  # noqa: SLF001
            except OSError:
                pass

    def _require_protocol(self) -> _HAProtocol:
        """Return the live protocol or raise if not connected."""
        if self._protocol is None or not self._connected:
            raise RuntimeError("C-Bus CNI is not connected")
        return self._protocol

    # ------------------------------------------------------------------
    # State cache + notifications
    # ------------------------------------------------------------------
    def _apply_level(self, group: int, level: int) -> None:
        """Store a new level and notify listeners if it changed."""
        level = max(0, min(CBUS_MAX_LEVEL, int(level)))
        if self._levels.get(group) == level:
            return
        self._levels[group] = level
        _LOGGER.debug("Group %s level -> %s", group, level)
        for callback in list(self._update_callbacks):
            callback(group, level)

    def _set_connected(self, connected: bool) -> None:
        """Update connection state and notify listeners on change."""
        if self._connected == connected:
            return
        self._connected = connected
        for callback in list(self._connection_callbacks):
            callback(connected)
