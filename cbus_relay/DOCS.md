# C-Bus CNI Relay

A tiny always-on relay that keeps **one permanent connection** to your C-Bus
CNI and lets Home Assistant connect/disconnect to *it* freely.

## Why you need this

A Clipsal CNI accepts only **one** TCP connection and (on common firmware) does
**not** release it when the client disconnects. So every time Home Assistant
restarts, the CNI keeps the old session as a zombie and rejects HA's new
connection with `*** Connection already in use` — forcing a manual CNI
power-cycle.

This add-on holds the CNI connection permanently. When Home Assistant restarts,
only the HA↔relay hop drops; the relay↔CNI hop stays up, so the CNI never sees a
disconnect. **No more power-cycle after an HA restart.**

It is a transparent byte pipe — it does not touch the C-Bus protocol — so the
**Clipsal C-Bus (CNI)** integration keeps doing all the work, just pointed at the
relay instead of the CNI directly.

## Setup

1. **Configure** the add-on:
   - **cni_host** — your CNI's IP address (e.g. `192.168.101.200`)
   - **cni_port** — CNI port (default `10001`)
   - **listen_port** — port HA will connect to (default `10010`)
   - **cni_local_port** — fixed local source port for the CNI link (default
     `10011`; leave as-is unless it clashes with something — set `0` to disable
     and use a random port). See *Surviving a host reboot* below.
2. **Start** the add-on (and enable *Start on boot* + *Watchdog*).
3. In **Settings → Devices & Services → Clipsal C-Bus (CNI)**, set the
   integration's **Host** to your **Home Assistant host IP** (the machine running
   HA, e.g. `192.168.101.3`) and **Port** to `10010` — i.e. point it at the relay
   instead of the CNI.
4. That's it. Restart Home Assistant whenever you like — it reconnects through
   the relay with no power-cycle.

> Only one client can use the CNI, so make sure **nothing else** (Toolkit, the
> integration pointed directly at the CNI, another controller) is connected to
> the CNI — only this relay should talk to it.

## Surviving a host reboot

The relay keeps the CNI link up across HA **Core** restarts. A full **host**
reboot is different: the relay itself restarts, and its previous CNI connection
was never closed cleanly — so the CNI keeps that session as a zombie and rejects
the relay's fresh connection with `*** Connection already in use`. This used to
be the one case that still needed a manual CNI power-cycle.

The relay now recovers on its own:

- It reconnects to the CNI from a **fixed local source port** (`cni_local_port`,
  default `10011`). The new connection therefore reuses the same source IP/port,
  so it lands on the CNI's existing connection and prompts the CNI to reset the
  stale session — after which the relay connects cleanly. No power-cycle.
- It detects the `*** Connection already in use` banner during a brief handshake,
  so it never serves Home Assistant a dead link, and keeps retrying patiently
  until the session clears.

If your CNI's firmware is unusually stubborn and still won't release the session,
a power-cycle remains the last resort — but for common firmware this is now
automatic.

## Notes

- TCP keep-alive is enabled on the CNI link so a dead/half-open link is detected
  and re-established automatically.
