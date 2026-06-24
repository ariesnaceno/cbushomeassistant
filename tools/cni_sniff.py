#!/usr/bin/env python3
"""Passive C-Bus CNI sniffer with clean-session retry.

Connects to the CNI raw PCI port and dumps everything received as timestamped
hex + ASCII so we can reverse the lighting wire format for this network.

Read-only: it never writes to the bus. It retries on "Connection already in
use" (a zombie session the CNI holds after an abrupt disconnect) until it gets
a real session, then holds it open and streams.
"""

from __future__ import annotations

import socket
import sys
import time

HOST = "192.168.101.200"
PORT = 10001
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
CONNECT_DEADLINE = 180.0  # keep retrying for the zombie session to clear


def _try_session() -> socket.socket | None:
    """Open one connection; return it if live, None if 'already in use'."""
    try:
        sock = socket.create_connection((HOST, PORT), timeout=5)
    except OSError as err:
        print(f"# connect failed: {err}")
        return None
    sock.settimeout(1.0)
    # Peek for an immediate "already in use" rejection.
    try:
        first = sock.recv(256)
    except socket.timeout:
        return sock  # silent = live session, nothing on bus yet
    if b"already in use" in first:
        sock.close()
        return None
    # Live data arrived right away — print it and keep the socket.
    _dump(first)
    return sock


def _dump(data: bytes) -> None:
    ts = time.strftime("%H:%M:%S")
    asc = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    print(f"{ts}  HEX {data.hex(' ')}")
    print(f"{ts}  ASC {asc}")


def main() -> None:
    print(f"# connecting to {HOST}:{PORT} (passive)")
    deadline = time.time() + CONNECT_DEADLINE
    sock = None
    while time.time() < deadline:
        sock = _try_session()
        if sock is not None:
            break
        print("# CNI busy (zombie session); retrying in 5s...")
        time.sleep(5)
    if sock is None:
        print("# gave up waiting for a free CNI session")
        return

    print("# CONNECTED — TOGGLE A C-BUS LIGHT NOW (on, off, dim a few times)")
    end = time.time() + DURATION
    total = 0
    try:
        while time.time() < end:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                print("# connection closed by CNI")
                break
            _dump(data)
            total += len(data)
    finally:
        sock.close()
    print(f"# done. total payload bytes: {total}")


if __name__ == "__main__":
    main()
