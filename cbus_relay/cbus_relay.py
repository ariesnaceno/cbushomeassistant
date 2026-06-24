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

Configuration (environment variables)
-------------------------------------
- ``CNI_HOST``      CNI IP address (required, e.g. 192.168.101.200)
- ``CNI_PORT``      CNI TCP port (default 10001)
- ``LISTEN_HOST``   address to listen on (default 0.0.0.0)
- ``LISTEN_PORT``   port HA connects to (default 10010)
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct

_LOG = logging.getLogger("cbus_relay")

CNI_HOST = os.environ.get("CNI_HOST", "")
CNI_PORT = int(os.environ.get("CNI_PORT", "10001"))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "10010"))

_UPSTREAM_RECONNECT = 5.0  # seconds between CNI reconnect attempts
_BUF = 4096


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

    # ------------------------------------------------------------------
    # Upstream: one permanent connection to the CNI
    # ------------------------------------------------------------------
    async def _upstream_manager(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection(CNI_HOST, CNI_PORT)
            except OSError as err:
                _LOG.warning(
                    "CNI %s:%s unreachable (%s); retrying in %ss",
                    CNI_HOST, CNI_PORT, err, _UPSTREAM_RECONNECT,
                )
                await asyncio.sleep(_UPSTREAM_RECONNECT)
                continue

            _set_keepalive(writer)
            self._up_writer = writer
            _LOG.info("Connected to CNI %s:%s", CNI_HOST, CNI_PORT)
            try:
                while True:
                    data = await reader.read(_BUF)
                    if not data:
                        break  # CNI closed the connection
                    down = self._down_writer
                    if down is not None and not down.is_closing():
                        try:
                            down.write(data)
                            await down.drain()
                        except OSError:
                            pass  # HA went away mid-write; keep CNI link
            except OSError as err:
                _LOG.warning("CNI link error: %s", err)
            finally:
                self._up_writer = None
                writer.close()
                _LOG.warning(
                    "CNI link dropped; reconnecting in %ss", _UPSTREAM_RECONNECT
                )
            await asyncio.sleep(_UPSTREAM_RECONNECT)

    # ------------------------------------------------------------------
    # Downstream: the Home Assistant client (only one at a time)
    # ------------------------------------------------------------------
    async def _handle_downstream(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
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
