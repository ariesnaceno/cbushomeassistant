#!/usr/bin/env python3
"""Live connection test against a real CNI, using the vendored protocol.

Connects exactly like the Home Assistant integration does (PCIProtocol, which
runs the SMART+MONITOR init sequence on connect), then listens for events,
confirmations, and errors. Read-only-ish: it only sends the standard init
sequence (no lighting commands), so it won't change any loads.

Usage: python3 tools/cni_connect_test.py [HOST] [PORT] [SECONDS]
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

_VENDOR = os.path.join(os.path.dirname(__file__), "..", "custom_components", "cbus", "vendor")
sys.path.insert(0, os.path.abspath(_VENDOR))

from cbus.protocol.pciprotocol import PCIProtocol  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.101.200"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 10001
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0


class TestProtocol(PCIProtocol):
    def on_lighting_group_on(self, source_addr, group_addr):
        print(f"  EVENT  light ON   group={group_addr} from unit {source_addr}")

    def on_lighting_group_off(self, source_addr, group_addr):
        print(f"  EVENT  light OFF  group={group_addr} from unit {source_addr}")

    def on_lighting_group_ramp(self, source_addr, group_addr, duration, level):
        print(f"  EVENT  light RAMP group={group_addr} -> {level} over {duration}s")

    def on_confirmation(self, code, success):
        print(f"  CONFIRM code={code!r} success={success}")

    def on_pci_cannot_accept_data(self):
        print("  ERROR  PCI cannot accept data (checksum/buffer)")

    def on_reset(self):
        print("  PCI reset in progress")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    print(f"# connecting to CNI {HOST}:{PORT} (init sequence will run automatically)")
    loop = asyncio.get_running_loop()
    lost = loop.create_future()
    try:
        transport, _proto = await asyncio.wait_for(
            loop.create_connection(
                lambda: TestProtocol(
                    timesync_frequency=0,
                    handle_clock_requests=False,
                    connection_lost_future=lost,
                ),
                HOST,
                PORT,
            ),
            timeout=8,
        )
    except (OSError, asyncio.TimeoutError) as err:
        print(f"# CONNECT FAILED: {err}")
        return

    print("# CONNECTED. Init sequence (reset + SMART/MONITOR/CONNECT) sent.")
    print(f"# Listening {SECONDS:.0f}s. (Bus is unpowered, so expect no light events.)")
    try:
        await asyncio.wait_for(lost, timeout=SECONDS)
        print("# connection closed by CNI")
    except asyncio.TimeoutError:
        print("# still connected after listen window — init accepted cleanly.")
    finally:
        transport.close()
    print("# done.")


if __name__ == "__main__":
    asyncio.run(main())
