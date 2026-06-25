#!/usr/bin/env python3
"""Persistent TCP relay for a single-session C-Bus CNI.

Why this exists
---------------
A Clipsal CNI accepts only **one** TCP connection and — on at least some
firmware (e.g. CNI2 5.5.00) — does **not** release that session when the client
disconnects. So every time Home Assistant restarts, the CNI keeps the old
(dead) session as a zombie and rejects HA's new connection with
``*** Connection already in use``, forcing a manual CNI power-cycle.

This relay fixes that by keeping **one permanent connection to the CNI** and
letting Home Assistant connect/disconnect to the *relay* as often as it likes.
When HA restarts, only the relay<->HA hop drops; the relay<->CNI hop stays up,
so the CNI never sees a disconnect and never zombies. No power-cycle needed.

It is a transparent byte pipe — it does not parse the C-Bus protocol — so the
Home Assistant integration keeps doing all the protocol work end to end.

Reliability (v1.0.1)
--------------------
The relay only serves Home Assistant while the CNI link is actually up, and it
drops the HA connection whenever the CNI link drops. This forces HA to
reconnect and re-run its init sequence (SMART+MONITOR), so monitoring always
comes back cleanly after a CNI-side blip — instead of HA silently talking to a
freshly-reconnected, un-initialised CNI.

Surviving a host reboot (v1.1.0)
--------------------------------
When the whole Home Assistant *host* reboots, the relay restarts too and its
previous CNI link was never closed cleanly — so the CNI keeps that session as a
zombie and rejects the relay's fresh connection with ``*** Connection already
in use`` (the one case that previously still needed a CNI power-cycle). The
relay now (a) reconnects from a **fixed local source port** so the new SYN lands
on the CNI's existing 4-tuple and prompts it to drop the stale session, and
(b) actually detects the "already in use" banner during a short handshake so it
never serves Home Assistant a dead link, retrying until the session clears.

Configuration (environment variables)
-------------------------------------
- ``CNI_HOST``       CNI IP address (required, e.g. 192.168.101.200)
- ``CNI_PORT``       CNI TCP port (default 10001)
- ``LISTEN_HOST``    address to listen on (default 0.0.0.0)
- ``LISTEN_PORT``    port HA connects to (default 10010)
- ``CNI_LOCAL_PORT`` fixed local source port for the CNI link (default 10011;
                     set 0 to use an ephemeral port)
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

_LOG = logging.getLogger("cbus_relay")

CNI_HOST = os.environ.get("CNI_HOST", "")
CNI_PORT = int(os.environ.get("CNI_PORT", "10001"))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "10010"))
# Fixed local (source) port for the relay's CNI link. Reconnecting from the
# same source port means that after an *unclean* drop — e.g. a host reboot,
# where the relay's previous connection was never closed cleanly — our new SYN
# lands on the CNI's existing 4-tuple. A conformant TCP stack answers with a
# challenge ACK (RFC 5961), prompting a RST that clears the CNI's stale session,
# so the next attempt connects cleanly instead of being rejected with
# "*** Connection already in use" (which otherwise needs a CNI power-cycle).
# Best-effort: 0 (or a bind failure) falls back to an ephemeral port.
CNI_LOCAL_PORT = int(os.environ.get("CNI_LOCAL_PORT") or "10011")

_UPSTREAM_RECONNECT = 5.0  # seconds between CNI reconnect attempts
_IN_USE_RECONNECT = 30.0  # longer back-off while the CNI holds a stale session
# How long to watch a fresh CNI link for the single-session rejection banner
# before treating it as a genuine, usable connection.
_HANDSHAKE_GRACE = 2.0
# The CNI rejects a second client (or a still-zombied old session) with this.
_IN_USE_MARKER = b"already in use"
_BUF = 4096


async def _connect_cni() -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Open the CNI link, preferring a fixed local source port.

    Returns a (reader, writer) pair, or ``None`` on failure. Pinning the source
    port is best effort: if it can't be bound (e.g. a stale ``TIME_WAIT`` despite
    SO_REUSEADDR) we fall back to an ordinary ephemeral-port connection.
    """
    if CNI_LOCAL_PORT:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(
                CNI_HOST, CNI_PORT, type=socket.SOCK_STREAM
            )
            family, type_, proto, _canon, addr = infos[0]
            sock = socket.socket(family, type_, proto)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", CNI_LOCAL_PORT))
                sock.setblocking(False)
                await loop.sock_connect(sock, addr)
            except OSError as err:
                sock.close()
                _LOG.debug(
                    "Could not connect from source port %s (%s); "
                    "using an ephemeral port", CNI_LOCAL_PORT, err,
                )
            else:
                return await asyncio.open_connection(sock=sock)
        except OSError as err:
            _LOG.warning("CNI %s:%s unreachable (%s)", CNI_HOST, CNI_PORT, err)
            return None
    try:
        return await asyncio.open_connection(CNI_HOST, CNI_PORT)
    except OSError as err:
        _LOG.warning("CNI %s:%s unreachable (%s)", CNI_HOST, CNI_PORT, err)
        return None


def _set_keepalive(writer: asyncio.StreamWriter) -> None:
    """Enable TCP keep-alive on a socket so a dead link is noticed quickly."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10),
                            ("TCP_KEEPCNT", 3)):
            opt = getattr(socket, name, None)
            if opt is not None:
                sock.setsockopt(socket.IPPROTO_TCP, opt, value)
    except OSError:
        pass


class Relay:
    """Holds one persistent CNI link and bridges it to the active HA client."""

    def __init__(self) -> None:
        self._up_writer: asyncio.StreamWriter | None = None
        self._down_writer: asyncio.StreamWriter | None = None

    @property
    def _upstream_ready(self) -> bool:
        return self._up_writer is not None and not self._up_writer.is_closing()

    async def run(self) -> None:
        """Start the upstream manager and the downstream server."""
        asyncio.ensure_future(self._upstream_manager())
        server = await asyncio.start_server(
            self._handle_downstream, LISTEN_HOST, LISTEN_PORT
        )
        _LOG.info(
            "Relay listening on %s:%s -> CNI %s:%s",
            LISTEN_HOST, LISTEN_PORT, CNI_HOST, CNI_PORT,
        )
        async with server:
            await server.serve_forever()

    def _close_downstream(self, reason: str) -> None:
        """Drop the current HA connection so it reconnects and re-initialises."""
        down = self._down_writer
        self._down_writer = None
        if down is not None and not down.is_closing():
            _LOG.info("Dropping Home Assistant connection (%s)", reason)
            down.close()

    # ------------------------------------------------------------------
    # Upstream: one permanent connection to the CNI
    # ------------------------------------------------------------------
    async def _upstream_manager(self) -> None:
        while True:
            conn = await _connect_cni()
            if conn is None:
                await asyncio.sleep(_UPSTREAM_RECONNECT)
                continue
            reader, writer = conn
            _set_keepalive(writer)

            # The CNI accepts the TCP socket even when it is about to reject the
            # session: it sends "*** Connection already in use" (a still-held
            # zombie session, or another client) and then closes. Watch the
            # fresh link for that banner before we mark it usable, so we never
            # serve Home Assistant a dead pipe or log a false "Connected".
            state, pending = await self._handshake(reader)
            if state == "in_use":
                writer.close()
                _LOG.warning(
                    "CNI reports its connection is already in use; retrying in "
                    "%ss (a stale session should clear as we reconnect from the "
                    "same source port)", _IN_USE_RECONNECT,
                )
                await asyncio.sleep(_IN_USE_RECONNECT)
                continue
            if state == "closed":
                writer.close()
                _LOG.warning(
                    "CNI closed during handshake; retrying in %ss",
                    _UPSTREAM_RECONNECT,
                )
                await asyncio.sleep(_UPSTREAM_RECONNECT)
                continue

            self._up_writer = writer
            _LOG.info("Connected to CNI %s:%s", CNI_HOST, CNI_PORT)
            try:
                if pending:
                    await self._to_downstream(pending)
                while True:
                    data = await reader.read(_BUF)
                    if not data:
                        break  # CNI closed the connection
                    await self._to_downstream(data)
            except OSError as err:
                _LOG.warning("CNI link error: %s", err)
            finally:
                self._up_writer = None
                writer.close()
                # The CNI link is gone — force HA to reconnect so it re-inits
                # against a fresh CNI session once we're back up.
                self._close_downstream("CNI link dropped")
                _LOG.warning(
                    "CNI link dropped; reconnecting in %ss", _UPSTREAM_RECONNECT
                )
            await asyncio.sleep(_UPSTREAM_RECONNECT)

    async def _handshake(self, reader: asyncio.StreamReader) -> tuple[str, bytes]:
        """Classify a fresh CNI link by watching its first bytes.

        Returns ``("in_use", b"")`` if the CNI rejected us, ``("closed", b"")``
        if it hung up, or ``("ok", data)`` for a usable link — where ``data`` is
        any bytes already read (e.g. an early bus event) to forward on.
        """
        try:
            data = await asyncio.wait_for(reader.read(_BUF), timeout=_HANDSHAKE_GRACE)
        except asyncio.TimeoutError:
            return "ok", b""  # silent, healthy connect
        if not data:
            return "closed", b""
        if _IN_USE_MARKER in data:
            return "in_use", b""
        return "ok", data

    async def _to_downstream(self, data: bytes) -> None:
        """Forward bytes from the CNI to the Home Assistant client, if any."""
        down = self._down_writer
        if down is not None and not down.is_closing():
            try:
                down.write(data)
                await down.drain()
            except OSError:
                pass  # HA went away mid-write; keep the CNI link

    # ------------------------------------------------------------------
    # Downstream: the Home Assistant client (only one at a time)
    # ------------------------------------------------------------------
    async def _handle_downstream(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")

        # Only serve HA when the CNI link is actually up. Otherwise close
        # immediately so HA keeps retrying until the CNI is reachable (and then
        # connects fresh and runs its init sequence).
        if not self._upstream_ready:
            _LOG.info(
                "Home Assistant %s connected but CNI link not ready; "
                "closing so it retries", peer,
            )
            writer.close()
            return

        # The CNI is single-client; if HA reconnects, drop the previous client.
        old = self._down_writer
        if old is not None and not old.is_closing():
            _LOG.info("New HA client %s; closing previous one", peer)
            old.close()
        self._down_writer = writer
        _LOG.info("Home Assistant connected: %s", peer)
        try:
            while True:
                data = await reader.read(_BUF)
                if not data:
                    break
                up = self._up_writer
                if up is not None and not up.is_closing():
                    try:
                        up.write(data)
                        await up.drain()
                    except OSError:
                        pass
        except OSError:
            pass
        finally:
            if self._down_writer is writer:
                self._down_writer = None
            writer.close()
            _LOG.info("Home Assistant disconnected: %s (CNI link kept alive)", peer)


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if not CNI_HOST:
        raise SystemExit("CNI_HOST is required")
    await Relay().run()


if __name__ == "__main__":
    asyncio.run(_main())
