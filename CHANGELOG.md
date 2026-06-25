# Changelog

All notable changes to the **Clipsal C-Bus (CNI)** integration and the bundled
**C-Bus CNI Relay** add-on.

---

## Integration

### 2.2.0
- **Survive a full host reboot without a CNI power-cycle.** After a host reboot
  the CNI can still hold the pre-reboot session as a zombie and reject the new
  connection with `*** Connection already in use`. The integration now
  reconnects from a **fixed local source port**, so the new SYN reuses the CNI's
  existing connection 4-tuple and prompts it to drop the stale session — then
  the next attempt connects cleanly. Best-effort: it falls back to an ephemeral
  port if the fixed one can't be bound. (Pair with the relay add-on ≥1.1.0 if
  you connect through the relay.)

### 2.1.0
- **Availability debounce.** A brief connection blip no longer flickers every
  group to *unavailable* and back. Entities only go unavailable if the link
  stays down past a short grace (~25s) — quieter history/logs and no spurious
  "offline". Genuine outages still show unavailable.

### 2.0.9
- **Reconfigure flow.** Change the host/port in place (e.g. to point the
  integration at the relay) without deleting and re-adding it — your groups are
  kept. Settings → C-Bus → ⋮ → Reconfigure.

### 2.0.4 – 2.0.8 (connection robustness)
- Edit groups **without reloading** the integration — entities are reconciled in
  place via a dispatcher signal, so editing groups never drops the CNI link.
- Removing a group now deletes its **entity-registry** entry cleanly (no
  orphaned "unavailable" entities).
- **TCP keep-alive** on the CNI link so a dead/half-open connection is detected
  within ~60s.
- Clean shutdown handling (close the connection on Home Assistant stop). *Note:*
  some CNIs don't release their session on disconnect at all — that's what the
  **relay add-on** is for (see below).

### 2.0.1 – 2.0.3 (usability)
- **Menu-driven group editor** (Configure): pick groups from a C-Bus Toolkit
  file as a checklist, or add a light/switch/cover one at a time, plus remove.
- **Connection handshake grace** so a rejected attempt is never logged as
  "Connected", and reconnect warnings are throttled (no log spam).
- Clear handling of the CNI's `*** Connection already in use` rejection.

### 2.0.0
- **Direct-CNI backend.** Talks straight to a C-Bus CNI/PCI over TCP (no C-Gate,
  no MQTT) using a vendored copy of the proven `micolous/cbus` protocol library.
  `local_push`, SMART+MONITOR mode for accurate real-time feedback.
- Light, switch and cover platforms; group names importable from a C-Bus Toolkit
  `.cbz`/`.xml` backup.

---

## C-Bus CNI Relay add-on

### 1.1.0
- **Recover from a host reboot automatically.** When the host reboots, the relay
  restarts and the CNI may still hold its old session as a zombie, rejecting the
  fresh connection with `*** Connection already in use` — previously the one case
  that still needed a CNI power-cycle. The relay now reconnects from a **fixed
  local source port** (`cni_local_port`, default `10011`) so the CNI drops the
  stale session, and it **detects the "already in use" banner** during a short
  handshake so it never serves Home Assistant a dead link, retrying until the
  session clears.

### 1.0.1
- **Re-initialise Home Assistant after a CNI-side drop.** The relay now only
  serves HA while the CNI link is up, and drops the HA connection whenever the
  CNI link drops — so HA reconnects and re-runs its SMART+MONITOR init against
  the fresh CNI session, instead of silently talking to a reconnected,
  un-initialised CNI.

### 1.0.0
- Initial release. A tiny persistent TCP relay that holds the single CNI
  connection so Home Assistant can restart/update **without needing a CNI
  power-cycle**. HA connects/disconnects to the relay freely; the relay keeps
  the CNI link alive.

---

## Why the relay exists

Some Clipsal CNIs (e.g. CNI2 firmware 5.5.00) accept only **one** TCP connection
**and do not release it when the client disconnects**. So after a Home Assistant
restart the CNI rejects the new connection with `*** Connection already in use`
until it is power-cycled. The relay holds one permanent connection to the CNI, so
HA can come and go without ever disturbing it. This is the recommended setup for
such CNIs.

> A **relay** restart (add-on update, crash, or host reboot) used to be the one
> case that still needed a CNI power-cycle. As of relay 1.1.0 the relay recovers
> from that on its own by reconnecting from a fixed local source port, which
> prompts the CNI to drop its stale session. A power-cycle is only a last resort
> for unusually stubborn firmware.
