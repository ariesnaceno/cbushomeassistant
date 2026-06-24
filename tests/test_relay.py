"""Functional test for the C-Bus CNI relay.

Spins up a mock "CNI" (single-connection, counts how many times it is
connected to), runs the relay against it, then connects two successive "HA"
clients. Proves: data flows both ways, and the CNI is connected **once** and
stays up across the HA reconnect (the whole point of the relay).

Run: python3 tests/test_relay.py
"""

import asyncio
import os
import sys

_RELAY_DIR = os.path.join(os.path.dirname(__file__), "..", "cbus_relay")
sys.path.insert(0, os.path.abspath(_RELAY_DIR))


async def main() -> None:
    cni_conns = {"count": 0, "live": 0}

    async def mock_cni(reader, writer):
        cni_conns["count"] += 1
        cni_conns["live"] += 1
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                # Echo with a prefix so we can tell it round-tripped via the CNI.
                writer.write(b"CNI:" + data)
                await writer.drain()
        except OSError:
            pass
        finally:
            cni_conns["live"] -= 1
            writer.close()

    cni = await asyncio.start_server(mock_cni, "127.0.0.1", 0)
    cni_port = cni.sockets[0].getsockname()[1]

    # Configure + import the relay against the mock CNI.
    os.environ["CNI_HOST"] = "127.0.0.1"
    os.environ["CNI_PORT"] = str(cni_port)
    os.environ["LISTEN_HOST"] = "127.0.0.1"
    os.environ["LISTEN_PORT"] = "0"  # not used; we call the server directly
    import cbus_relay

    relay = cbus_relay.Relay()
    asyncio.ensure_future(relay._upstream_manager())  # noqa: SLF001
    server = await asyncio.start_server(
        relay._handle_downstream, "127.0.0.1", 0  # noqa: SLF001
    )
    relay_port = server.sockets[0].getsockname()[1]
    await asyncio.sleep(0.3)  # let the relay connect upstream to the CNI

    async def ha_roundtrip(msg: bytes) -> bytes:
        r, w = await asyncio.open_connection("127.0.0.1", relay_port)
        w.write(msg)
        await w.drain()
        data = await asyncio.wait_for(r.read(4096), timeout=2)
        w.close()
        return data

    def check(label, cond):
        print(f"[{'ok' if cond else 'FAIL'}] {label}")
        assert cond, label

    # HA client #1
    r1 = await ha_roundtrip(b"hello")
    check("client1 round-trips via CNI", r1 == b"CNI:hello")
    await asyncio.sleep(0.2)  # HA #1 disconnected

    # CNI must still be connected (relay kept it up)
    check("CNI still connected after HA disconnect", cni_conns["live"] == 1)

    # HA client #2 (simulates HA restart reconnecting)
    r2 = await ha_roundtrip(b"world")
    check("client2 round-trips via CNI", r2 == b"CNI:world")

    # The relay connected to the CNI exactly once the whole time.
    check("CNI connected exactly once (no reconnect churn)", cni_conns["count"] == 1)

    server.close()
    cni.close()
    print("\nAll relay tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
