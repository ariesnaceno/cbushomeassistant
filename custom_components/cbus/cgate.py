"""Async C-Gate client for the C-Bus integration.

This module owns two persistent TCP connections to a running C-Gate server:

* The **command** connection (default port 20023) is used to send lighting
  commands (on/off/ramp) and to query current group levels.
* The **status-change** connection (default port 20025) is a read-only stream
  that C-Gate pushes real-time group level changes to. Listening to this stream
  is what makes Home Assistant's state *accurate*: any change to a C-Bus group —
  whether triggered from Home Assistant, a physical wall switch, a scene, a PIR
  sensor, or a scheduled bus event — is reflected back to Home Assistant.

The client reconnects automatically and re-syncs all known group levels on
every (re)connection so state can never silently drift.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable

from .const import (
    CBUS_MAX_LEVEL,
    LIGHTING_APPLICATION,
)

_LOGGER = logging.getLogger(__name__)

# How long to wait for a reply to a synchronous command/query.
_REPLY_TIMEOUT = 10.0
# Delay between reconnection attempts (seconds).
_RECONNECT_DELAY = 5.0

# Matches the path "//PROJECT/NETWORK/APP/GROUP" used throughout C-Gate output.
_PATH_RE = re.compile(r"//(?P<project>[^/]+)/(?P<net>\d+)/(?P<app>\d+)/(?P<group>\d+)")
# A "level=NNN" or "level NNN" fragment in a status/response line.
_LEVEL_RE = re.compile(r"level[=\s](?P<level>\d+)", re.IGNORECASE)
# C-Gate "get ... level" response, e.g. "300 //HOME/254/56/4: level=128"
_GET_LEVEL_RE = re.compile(
    r"//[^/]+/\d+/\d+/(?P<group>\d+):\s*level[=\s](?P<level>\d+)", re.IGNORECASE
)


def _child_text(element, name: str) -> str | None:
    """Return the text of the first direct child whose tag ends with ``name``.

    Tolerant of XML namespaces (matches on the local tag name).
    """
    for child in element:
        if child.tag.endswith(name) and child.text is not None:
            return child.text.strip()
    return None


class CGateError(Exception):
    """Raised when a C-Gate command fails or the server is unreachable."""


class CGateClient:
    """Manage the connection to a C-Gate server and the C-Bus state cache."""

    def __init__(
        self,
        host: str,
        command_port: int,
        status_port: int,
        project: str,
        network: int,
    ) -> None:
        """Initialise the client. Call async_start() to connect."""
        self._host = host
        self._command_port = command_port
        self._status_port = status_port
        self._project = project
        self._network = network

        self._cmd_reader: asyncio.StreamReader | None = None
        self._cmd_writer: asyncio.StreamWriter | None = None
        self._cmd_lock = asyncio.Lock()

        self._status_task: asyncio.Task | None = None
        self._closing = False

        # group id -> last known level (0..255)
        self._levels: dict[int, int] = {}
        # Callbacks invoked with (group, level) on any change.
        self._update_callbacks: list[Callable[[int, int], None]] = []
        # Callbacks invoked with (connected: bool) on connection changes.
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._connected = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        """Return whether the command channel is currently connected."""
        return self._connected

    @property
    def project(self) -> str:
        """Return the configured C-Gate project name."""
        return self._project

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
        """Register a callback for connection state changes."""
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
        """Open both connections and begin processing the status stream."""
        await self._connect_command()
        self._status_task = asyncio.ensure_future(self._status_loop())

    async def async_stop(self) -> None:
        """Close all connections and stop background tasks."""
        self._closing = True
        if self._status_task:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass
        await self._close_command()

    async def async_turn_on(self, group: int, level: int = CBUS_MAX_LEVEL) -> None:
        """Turn a group on, optionally ramping to a specific level (0..255)."""
        level = max(0, min(CBUS_MAX_LEVEL, int(level)))
        if level >= CBUS_MAX_LEVEL:
            await self._command(f"on {self._path(group)}")
        else:
            # ramp <path> <level> [seconds] — instantaneous when time omitted.
            await self._command(f"ramp {self._path(group)} {level}")
        self._apply_level(group, level)

    async def async_turn_off(self, group: int) -> None:
        """Turn a group off."""
        await self._command(f"off {self._path(group)}")
        self._apply_level(group, 0)

    async def async_ramp(self, group: int, level: int, seconds: int) -> None:
        """Ramp a group to a level over the given number of seconds."""
        level = max(0, min(CBUS_MAX_LEVEL, int(level)))
        await self._command(f"ramp {self._path(group)} {level} {seconds}")
        self._apply_level(group, level)

    async def async_refresh_group(self, group: int) -> int | None:
        """Query C-Gate for a group's current level and update the cache."""
        try:
            lines = await self._command(f"get {self._path(group)} level")
        except CGateError:
            return None
        for line in lines:
            match = _GET_LEVEL_RE.search(line)
            if match and int(match.group("group")) == group:
                level = int(match.group("level"))
                self._apply_level(group, level)
                return level
        return None

    async def async_refresh_all(self, groups: list[int]) -> None:
        """Re-sync every known group so cached state matches the live bus."""
        for group in groups:
            await self.async_refresh_group(group)

    # ------------------------------------------------------------------
    # Command channel
    # ------------------------------------------------------------------
    def _path(self, group: int) -> str:
        """Build the C-Gate object path for a lighting group."""
        return (
            f"//{self._project}/{self._network}/{LIGHTING_APPLICATION}/{group}"
        )

    async def _connect_command(self) -> None:
        """Open the command connection and read the C-Gate banner."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._command_port),
                timeout=_REPLY_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise CGateError(
                f"Cannot connect to C-Gate at {self._host}:{self._command_port}: {err}"
            ) from err

        self._cmd_reader = reader
        self._cmd_writer = writer
        # Consume the greeting line (e.g. "201 Service ready ...").
        try:
            await asyncio.wait_for(reader.readline(), timeout=_REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        self._set_connected(True)
        _LOGGER.info("Connected to C-Gate command port %s:%s", self._host, self._command_port)

    async def _close_command(self) -> None:
        """Tear down the command connection."""
        self._set_connected(False)
        if self._cmd_writer is not None:
            try:
                self._cmd_writer.close()
                await self._cmd_writer.wait_closed()
            except OSError:
                pass
        self._cmd_reader = None
        self._cmd_writer = None

    async def _command(self, command: str) -> list[str]:
        """Send a command and return the response line(s) from C-Gate."""
        async with self._cmd_lock:
            if self._cmd_writer is None or self._cmd_reader is None:
                await self._connect_command()
            assert self._cmd_writer is not None and self._cmd_reader is not None

            _LOGGER.debug("C-Gate >> %s", command)
            try:
                self._cmd_writer.write((command + "\r\n").encode("latin-1"))
                await self._cmd_writer.drain()
                line = await asyncio.wait_for(
                    self._cmd_reader.readline(), timeout=_REPLY_TIMEOUT
                )
            except (OSError, asyncio.TimeoutError) as err:
                await self._close_command()
                raise CGateError(f"C-Gate command failed: {err}") from err

            if not line:
                await self._close_command()
                raise CGateError("C-Gate closed the connection")

            text = line.decode("latin-1", errors="replace").strip()
            _LOGGER.debug("C-Gate << %s", text)
            code = text[:3]
            if code.isdigit() and code.startswith(("4", "5")):
                raise CGateError(f"C-Gate error: {text}")
            return [text]

    async def _command_multiline(self, command: str) -> list[str]:
        """Send a command that returns a multi-line C-Gate response.

        C-Gate marks continuation lines with ``NNN-...`` (dash after the
        3-digit status code) and the final line with ``NNN ...`` (space).
        Returns the payload of every line with the status code stripped.
        """
        async with self._cmd_lock:
            if self._cmd_writer is None or self._cmd_reader is None:
                await self._connect_command()
            assert self._cmd_writer is not None and self._cmd_reader is not None

            _LOGGER.debug("C-Gate >> %s", command)
            try:
                self._cmd_writer.write((command + "\r\n").encode("latin-1"))
                await self._cmd_writer.drain()
            except OSError as err:
                await self._close_command()
                raise CGateError(f"C-Gate command failed: {err}") from err

            lines: list[str] = []
            while True:
                try:
                    raw = await asyncio.wait_for(
                        self._cmd_reader.readline(), timeout=_REPLY_TIMEOUT
                    )
                except (OSError, asyncio.TimeoutError) as err:
                    await self._close_command()
                    raise CGateError(f"C-Gate command failed: {err}") from err
                if not raw:
                    await self._close_command()
                    raise CGateError("C-Gate closed the connection")

                text = raw.decode("latin-1", errors="replace").rstrip("\r\n")
                code = text[:3]
                sep = text[3:4]
                payload = text[4:] if (code.isdigit() and sep in "- ") else text
                if code.isdigit() and code.startswith(("4", "5")):
                    raise CGateError(f"C-Gate error: {text}")
                lines.append(payload)
                # A space (not a dash) after the status code marks the last line.
                if not (code.isdigit() and sep == "-"):
                    break
            _LOGGER.debug("C-Gate << %d line(s)", len(lines))
            return lines

    async def async_discover_lighting_groups(self) -> dict[int, str]:
        """Discover lighting groups defined in the C-Bus Toolkit project.

        Reads the C-Gate project database (the same DB that C-Bus Toolkit
        wrote) and returns ``{group_id: tag_name}`` for every group on the
        lighting application of the configured network. Falls back to the
        live network tree if the XML database is unavailable.
        """
        groups = await self._discover_from_xml()
        if groups:
            return groups
        return await self._discover_from_tree()

    async def _discover_from_xml(self) -> dict[int, str]:
        """Parse ``dbgetxml`` output for lighting-group tag names."""
        try:
            lines = await self._command_multiline(f"dbgetxml //{self._project}")
        except CGateError as err:
            _LOGGER.debug("dbgetxml discovery failed: %s", err)
            return {}

        xml = "\n".join(lines)
        groups: dict[int, str] = {}
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)  # noqa: S314 - trusted local C-Gate data
        except Exception as err:  # noqa: BLE001 - tolerate any malformed XML
            _LOGGER.debug("Could not parse C-Gate project XML: %s", err)
            return self._scrape_groups_from_text(xml)

        # Walk every Application node; keep those addressed to lighting (56).
        for app in root.iter():
            if not app.tag.endswith("Application"):
                continue
            app_addr = _child_text(app, "Address")
            if app_addr is None or int(app_addr) != LIGHTING_APPLICATION:
                continue
            for grp in app.iter():
                if not grp.tag.endswith("Group"):
                    continue
                addr = _child_text(grp, "Address")
                tag = _child_text(grp, "TagName") or _child_text(grp, "Tag")
                if addr is not None and addr.isdigit():
                    gid = int(addr)
                    groups[gid] = (tag or f"C-Bus Group {gid}").strip()
        return groups

    @staticmethod
    def _scrape_groups_from_text(xml: str) -> dict[int, str]:
        """Best-effort regex fallback when the XML can't be tree-parsed."""
        groups: dict[int, str] = {}
        block_re = re.compile(
            r"<Group>.*?<Address>(\d+)</Address>.*?"
            r"<TagName>(.*?)</TagName>.*?</Group>",
            re.IGNORECASE | re.DOTALL,
        )
        for addr, tag in block_re.findall(xml):
            groups[int(addr)] = (tag or f"C-Bus Group {addr}").strip()
        return groups

    async def _discover_from_tree(self) -> dict[int, str]:
        """Fallback: enumerate groups seen on the live network tree."""
        try:
            lines = await self._command_multiline(
                f"tree //{self._project}/{self._network}"
            )
        except CGateError as err:
            _LOGGER.debug("tree discovery failed: %s", err)
            return {}

        groups: dict[int, str] = {}
        for line in lines:
            match = _PATH_RE.search(line)
            if not match:
                continue
            if int(match.group("app")) != LIGHTING_APPLICATION:
                continue
            gid = int(match.group("group"))
            groups.setdefault(gid, f"C-Bus Group {gid}")
        return groups

    # ------------------------------------------------------------------
    # Status-change channel (real-time, push)
    # ------------------------------------------------------------------
    async def _status_loop(self) -> None:
        """Continuously read the status-change stream and apply updates."""
        while not self._closing:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._status_port),
                    timeout=_REPLY_TIMEOUT,
                )
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.warning(
                    "C-Gate status port %s:%s unavailable (%s); retrying in %ss",
                    self._host,
                    self._status_port,
                    err,
                    _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)
                continue

            _LOGGER.info(
                "Listening for real-time C-Bus status changes on %s:%s",
                self._host,
                self._status_port,
            )
            try:
                while not self._closing:
                    raw = await reader.readline()
                    if not raw:
                        break  # connection closed by C-Gate
                    line = raw.decode("latin-1", errors="replace").strip()
                    if line:
                        self._handle_status_line(line)
            except OSError as err:
                _LOGGER.warning("C-Gate status stream error: %s", err)
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except OSError:
                    pass

            if not self._closing:
                _LOGGER.warning(
                    "C-Gate status stream dropped; reconnecting in %ss",
                    _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    def _handle_status_line(self, line: str) -> None:
        """Parse one status-change line and update the matching group.

        Typical lines from the status-change port look like::

            lighting on //HOME/254/56/4  #sourceunit=8 OID=...
            lighting off //HOME/254/56/4 #sourceunit=8 OID=...
            lighting ramp //HOME/254/56/4 128 #sourceunit=8 OID=...
        """
        path_match = _PATH_RE.search(line)
        if not path_match:
            return
        # Only act on lighting application events for our network.
        if int(path_match.group("app")) != LIGHTING_APPLICATION:
            return
        if int(path_match.group("net")) != self._network:
            return

        group = int(path_match.group("group"))
        lowered = line.lower()

        if "lighting on" in lowered or " on " in lowered:
            self._apply_level(group, CBUS_MAX_LEVEL)
        elif "lighting off" in lowered or " off " in lowered:
            self._apply_level(group, 0)
        elif "ramp" in lowered:
            level_match = _LEVEL_RE.search(line)
            if level_match:
                self._apply_level(group, int(level_match.group("level")))
            else:
                # "ramp //path 128 ..." — grab the first integer after the path.
                tail = line[path_match.end():].strip().split()
                if tail and tail[0].isdigit():
                    self._apply_level(group, int(tail[0]))

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
