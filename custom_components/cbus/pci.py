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
import socket
import struct
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


def _enable_tcp_keepalive(transport) -> None:
    """Turn on TCP keep-alive so a dead/half-open CNI link is detected fast.

    Without this, a connection silently dropped by the CNI or the network can
    linger as a half-open socket: Home Assistant keeps thinking it is connected
    while the CNI has already freed (or zombied) the session. Keep-alive probes
    surface the break within ~1 minute and close our side cleanly, which lets us
    reconnect promptly and helps the CNI release the old session.
    """
    sock = transport.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Linux-only fine-tuning (Home Assistant OS): start probing after 30s
        # idle, probe every 10s, drop after 3 failed probes (~60s to detect).
        for name, value in (
            ("TCP_KEEPIDLE", 30),
            ("TCP_KEEPINTVL", 10),
            ("TCP_KEEPCNT", 3),
        ):
            opt = getattr(socket, name, None)
            if opt is not None:
                sock.setsockopt(socket.IPPROTO_TCP, opt, value)
    except OSError as err:
        _LOGGER.debug("Could not set TCP keep-alive: %s", err)


def _abort_transport(transport) -> None:
    """Close a transport with a TCP RST so the CNI frees its session at once.

    A normal close (FIN) can leave the CNI holding the old session as a zombie,
    which then rejects the next connection with "already in use" — the reason a
    CNI power-cycle was previously needed after a Home Assistant restart. Setting
    SO_LINGER to 0 makes close() send a RST, which the CNI acts on immediately.
    """
    if transport is None:
        return
    sock = transport.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
        except OSError as err:
            _LOGGER.debug("Could not set SO_LINGER: %s", err)
    # abort() drops the connection immediately; with SO_LINGER=0 that's a RST.
    try:
        transport.abort()
    except Exception:  # noqa: BLE001 - best effort during shutdown
        try:
            transport.close()
        except OSError:
            pass
    _LOGGER.debug("Aborted CNI transport (RST) to release the session")


_RECONNECT_DELAY = 5.0  # seconds between reconnection attempts
_IN_USE_DELAY = 30.0  # longer back-off when the CNI is held by another client
# How long a new connection must stay up before we treat it as genuinely
# connected. The CNI rejects extra clients (and closes) within ~1s, so this
# distinguishes a real session from an "already in use" rejection.
_HANDSHAKE_GRACE = 3.0
# A CNI allows only one TCP client; it rejects extras with this banner.
_IN_USE_MARKER = b"already in use"


class _HAProtocol(PCIProtocol):
    """PCIProtocol subclass that routes bus events into the HA client."""

    def __init__(self, client: "PCIClient", **kwargs) -> None:
        """Store a back-reference to the owning client."""
        self._client = client
        super().__init__(**kwargs)

    def data_received(self, data: bytes) -> None:
        """Intercept the CNI's single-session rejection before decoding."""
        if _IN_USE_MARKER in data:
            self._client._note_in_use()
            return  # don't feed the plain-text banner to the packet decoder
        super().data_received(data)

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
        self._transport = None
        self._connect_task: asyncio.Task | None = None
        self._closing = False
        self._connected = False
        self._in_use = False  # set when the CNI rejects us as already-in-use
        self._fail_count = 0  # consecutive connect failures (for log throttling)

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
        """Close the connection (RST) and stop reconnecting.

        Abort the live transport *before* cancelling the connect loop: the
        loop's own cleanup nulls the transport reference, so if we cancelled
        first we'd have nothing left to abort and the socket would be abandoned
        (leaving the CNI holding a zombie session).
        """
        self._closing = True
        transport, self._transport, self._protocol = self._transport, None, None
        _abort_transport(transport)
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass

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
            self._in_use = False
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
                self._log_retry(f"cannot reach CNI ({err})", _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)
                continue

            self._protocol = protocol  # type: ignore[assignment]
            self._transport = _transport
            _enable_tcp_keepalive(_transport)

            # The TCP connection succeeds even when the CNI is going to reject
            # us: it accepts the socket, sends "*** Connection already in use",
            # then closes within ~1s. Wait a short grace period to tell a real
            # connection apart from an immediate rejection, so we never report
            # "connected" for a session we didn't actually get.
            try:
                await asyncio.wait_for(
                    asyncio.shield(lost_future), timeout=_HANDSHAKE_GRACE
                )
                handshake_ok = False  # dropped during grace -> rejected/failed
            except asyncio.TimeoutError:
                handshake_ok = True  # survived the grace -> genuinely connected

            if not handshake_ok:
                self._protocol = None
                if self._closing:
                    break
                if self._in_use:
                    self._log_retry(
                        "CNI reports its single connection is already in use by "
                        "another client (e.g. Toolkit, C-Gate, or a "
                        "cbus2mqtt/cmqttd add-on) — stop that client to free the "
                        "CNI",
                        _IN_USE_DELAY,
                    )
                    await asyncio.sleep(_IN_USE_DELAY)
                else:
                    self._log_retry("CNI dropped during handshake", _RECONNECT_DELAY)
                    await asyncio.sleep(_RECONNECT_DELAY)
                continue

            self._set_connected(True)
            self._fail_count = 0
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
                self._transport = None

            if self._closing:
                break

            self._log_retry("CNI link dropped", _RECONNECT_DELAY)
            await asyncio.sleep(_RECONNECT_DELAY)

    def _log_retry(self, reason: str, delay: float) -> None:
        """Log a reconnect reason, throttled so it doesn't flood the log."""
        self._fail_count += 1
        message = "C-Bus: %s; retrying in %ss (attempt %d)"
        # Log the first few attempts at WARNING, then drop to DEBUG so a
        # persistent problem leaves one clear note instead of flooding.
        if self._fail_count <= 3:
            _LOGGER.warning(message, reason, delay, self._fail_count)
        else:
            _LOGGER.debug(message, reason, delay, self._fail_count)

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

    def _note_in_use(self) -> None:
        """Flag that the CNI rejected this connection as already in use."""
        self._in_use = True

    def _set_connected(self, connected: bool) -> None:
        """Update connection state and notify listeners on change."""
        if self._connected == connected:
            return
        self._connected = connected
        for callback in list(self._connection_callbacks):
            callback(connected)
