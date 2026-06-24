"""Wire-format regression tests for the vendored C-Bus protocol.

These assert the exact bytes sent on the wire for lighting commands, so an
accidental change to the vendored library (or our usage of it) is caught even
without a live CNI. Run: ``python3 tests/test_encoding.py``.
"""

import os
import sys

_VENDOR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "cbus", "vendor"
)
sys.path.insert(0, os.path.abspath(_VENDOR))

from cbus.protocol.pm_packet import PointToMultipointPacket  # noqa: E402
from cbus.protocol.application.lighting import (  # noqa: E402
    LightingOnSAL,
    LightingOffSAL,
    LightingRampSAL,
)


def wire(pkt) -> bytes:
    """Return the on-wire prefix (backslash + hex), excluding conf code + CR."""
    return b"\\" + pkt.encode_packet()


def check(label, got, expected):
    status = "ok" if got == expected else "FAIL"
    print(f"[{status}] {label}: {got!r}")
    assert got == expected, f"{label}: expected {expected!r}, got {got!r}"


def main() -> None:
    # ON group 4  -> 05 38 00 79 04 + checksum 46
    check(
        "ON ga4",
        wire(PointToMultipointPacket(sals=[LightingOnSAL(4)])),
        b"\\053800790446",
    )
    # ON group 100 -> matches the library's own documented example \0538007964
    check(
        "ON ga100",
        wire(PointToMultipointPacket(sals=[LightingOnSAL(100)])),
        b"\\0538007964E6",
    )
    # OFF group 4 -> 05 38 00 01 04 + checksum BE
    check(
        "OFF ga4",
        wire(PointToMultipointPacket(sals=[LightingOffSAL(4)])),
        b"\\0538000104BE",
    )
    # RAMP group 4 to level 128 over 8s -> rate 0x12, ga 04, level 80
    check(
        "RAMP ga4 8s 128",
        wire(PointToMultipointPacket(sals=LightingRampSAL(4, 8, 128))),
        b"\\0538001204802D",
    )
    print("\nAll wire-format tests passed.")


if __name__ == "__main__":
    main()
