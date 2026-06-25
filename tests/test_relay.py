"""Functional tests for the C-Bus CNI relay (no hardware required).

Two scenarios, both against a mock "CNI":

1. **Steady state** — data flows both ways and the relay holds the single CNI
   link up across an HA reconnect (the whole point of the relay).

2. **Host reboot** — the CNI still holds the relay's pre-reboot session as a
   zombie and rejects the fresh connection with ``*** Connection already in
   use``. The relay must not serve that dead link to HA, and must keep retrying
   until the session clears, after which it works normally.

Run: python3 tests/test_relay.py
"""

import asyncio
import os
import sys

_RELAY_DIR = os.path.join(os.path.dirname(__file__), "..", "cbus_relay")
sys.path.insert(0, os.path.abspath(_RELAY_DIR))

# Configure the relay before importing it (it reads env at import time). Both
# tests reconfigure the module's globals directly for their own mock CNI.
os.environ.setdefault("CNI_HOST", "127.0.0.1")
os.environ.setdefault("CNI_PORT", "10001")
import cbus_relay  # noqa: E402


def check(label: str, cond: bool) -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {label}")
    assert cond, label


async def _wait_ready(relay_port: int, send: bytes, expect: bytes,
                      deadline: float) -> bytes:
    """Poll the relay until it serves a working round-trip, or time out.

    Until the upstream CNI link is up the relay accepts an HA client then drops
    it immediately, so a round-trip yields nothing — exactly what we retry past.
    """
    loop = asyncio.get_event_loop()
    while loop.time() < deadline:
        try:
            r, w = await asyncio.open_connection("127.0.0.1", relay_port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        w.write(send)
        try:
            await w.drain()
            data = await asyncio.wait_for(r.read(4096), timeout=0.5)
        except (OSError, asyncio.TimeoutError):
            data = b""
        w.close()
        if data:
            return data
        await asyncio.sleep(0.15)
    return b""


async def test_steady_state() -> None:
    cni_conns = {"count": 0, "live": 0}

    async def mock_cni(reader, writer):
        cni_conns["count"] += 1
        cni_conns["live"] += 1
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(b"CNI:" + data)  # prefix proves it round-tripped
                await writer.drain()
        except OSError:
            pass
        finally:
            cni_conns["live"] -= 1
            writer.close()

    cni = await asyncio.start_server(mock_cni, "127.0.0.1", 0)
    cni_port = cni.sockets[0].getsockname()[1]

    cbus_relay.CNI_HOST = "127.0.0.1"
    cbus_relay.CNI_PORT = cni_port
    cbus_relay.CNI_LOCAL_PORT = 0  # don't pin a source port under CI
    cbus_relay._HANDSHAKE_GRACE = 0.3

    relay = cbus_relay.Relay()
    up_task = asyncio.ensure_future(relay._upstream_manager())  # noqa: SLF001
    server = await asyncio.start_server(
        relay._handle_downstream, "127.0.0.1", 0  # noqa: SLF001
    )
    relay_port = server.sockets[0].getsockname()[1]

    try:
        deadline = asyncio.get_event_loop().time() + 6.0
        r1 = await _wait_ready(relay_port, b"hello", b"CNI:hello", deadline)
        check("client1 round-trips via CNI", r1 == b"CNI:hello")
        await asyncio.sleep(0.2)  # HA #1 disconnected

        check("CNI still connected after HA disconnect", cni_conns["live"] == 1)

        r2 = await _wait_ready(relay_port, b"world", b"CNI:world", deadline)
        check("client2 round-trips via CNI", r2 == b"CNI:world")

        check("CNI connected exactly once (no reconnect churn)",
              cni_conns["count"] == 1)
    finally:
        up_task.cancel()
        try:
            await up_task
        except asyncio.CancelledError:
            pass
        server.close()
        cni.close()


async def test_recovers_from_already_in_use() -> None:
    """Simulate a host reboot: first connection is rejected, then it clears."""
    cni_conns = {"count": 0}

    async def mock_cni(reader, writer):
        cni_conns["count"] += 1
        if cni_conns["count"] == 1:
            # Stale zombie session from before the reboot.
            writer.write(b"*** Connection already in use\r\n")
            await writer.drain()
            writer.close()
            return
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(b"CNI:" + data)
                await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    cni = await asyncio.start_server(mock_cni, "127.0.0.1", 0)
    cni_port = cni.sockets[0].getsockname()[1]

    cbus_relay.CNI_HOST = "127.0.0.1"
    cbus_relay.CNI_PORT = cni_port
    cbus_relay.CNI_LOCAL_PORT = 0
    cbus_relay._HANDSHAKE_GRACE = 0.3
    cbus_relay._IN_USE_RECONNECT = 0.2
    cbus_relay._UPSTREAM_RECONNECT = 0.2

    relay = cbus_relay.Relay()
    up_task = asyncio.ensure_future(relay._upstream_manager())  # noqa: SLF001
    server = await asyncio.start_server(
        relay._handle_downstream, "127.0.0.1", 0  # noqa: SLF001
    )
    relay_port = server.sockets[0].getsockname()[1]

    try:
        deadline = asyncio.get_event_loop().time() + 8.0
        resp = await _wait_ready(relay_port, b"ping", b"CNI:ping", deadline)
        check("relay recovers and serves HA after the rejection clears",
              resp == b"CNI:ping")
        check("relay retried past the 'already in use' rejection",
              cni_conns["count"] >= 2)
    finally:
        up_task.cancel()
        try:
            await up_task
        except asyncio.CancelledError:
            pass
        server.close()
        cni.close()


def test_handshake_classification() -> None:
    """The handshake labels rejection, hang-up, real data and silence."""

    async def run() -> None:
        relay = cbus_relay.Relay()
        cbus_relay._HANDSHAKE_GRACE = 0.2

        rejected = asyncio.StreamReader()
        rejected.feed_data(b"*** Connection already in use\r\n")
        check("banner -> in_use",
              await relay._handshake(rejected) == ("in_use", b""))  # noqa: SLF001

        closed = asyncio.StreamReader()
        closed.feed_eof()
        check("eof -> closed",
              await relay._handshake(closed) == ("closed", b""))  # noqa: SLF001

        event = asyncio.StreamReader()
        event.feed_data(b"some-bus-event")
        check("early data -> ok+data",
              await relay._handshake(event) == ("ok", b"some-bus-event"))  # noqa: SLF001

        silent = asyncio.StreamReader()  # never fed -> grace times out
        check("silence -> ok",
              await relay._handshake(silent) == ("ok", b""))  # noqa: SLF001

    asyncio.run(run())


async def _async_main() -> None:
    await test_steady_state()
    await test_recovers_from_already_in_use()


def main() -> None:
    test_handshake_classification()
    asyncio.run(_async_main())
    print("\nAll relay tests passed.")


if __name__ == "__main__":
    main()
