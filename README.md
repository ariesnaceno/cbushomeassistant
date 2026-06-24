# Clipsal C-Bus integration for Home Assistant (direct CNI/PCI)

A custom [Home Assistant](https://www.home-assistant.io/) integration that
connects **Clipsal C-Bus** lighting to Home Assistant by talking **directly to a
C-Bus CNI** (or serial-over-TCP PCI) — **no C-Gate server required** — with
**accurate, real-time status feedback**.

The CNI is placed into **SMART + MONITOR** mode, so it reports *every* lighting
change on the bus — whether it came from Home Assistant, a wall switch, a scene,
a PIR/occupancy sensor, a timer, or any other unit. Home Assistant therefore
always reflects the true state of the bus. This is a `local_push` integration:
no polling, instant updates.

## Quick start — fresh Home Assistant + new C-Bus project

Short answer: you install the repo **and** do a 2-minute setup (point it at your
CNI and list your group addresses). Here's the whole thing, start to finish.

**Before you start, make sure:**
- Your **CNI is on the network** and you know its **IP address** (check your
  router's client list, or open `http://<cni-ip>/` to confirm it's a Clipsal CNI).
- Your **C-Bus network is powered** and has devices on it. Open `http://<cni-ip>/`
  — it should say **C-Bus status: OK** with **~30–36 V**. If it says "Power down",
  fix the C-Bus power supply first.
- **Nothing else is connected to the CNI.** A CNI allows only **one** connection.
  Quit/stop C-Bus Toolkit, C-Gate, cbus2mqtt/cmqttd, or any other controller that
  talks to this CNI. (Toolkit can't be connected at the same time as this — see
  [Using C-Bus Toolkit alongside](#using-c-bus-toolkit-alongside).)

**Steps:**

1. **Find your group addresses.** In **C-Bus Toolkit**, note the lighting group
   addresses (0–255) and names you want in Home Assistant — e.g. `4 = Kitchen`,
   `1 = Living Room`. *(Optional: export a project backup `.cbz` to auto-fill the
   names — step 4.)* Then **disconnect Toolkit from the CNI.**

2. **Install the integration** (via HACS — recommended):
   - HACS → **⋮ → Custom repositories** → add
     `https://github.com/ariesnaceno/cbushomeassistant` as type **Integration**.
   - Find **“Clipsal C-Bus (CNI)”** → **Download** → **Restart Home Assistant**.
   - *(No HACS? See [Manual install](#manual). Or run `scripts/install.sh` from
     the Terminal add-on.)*

3. **Add the integration:** **Settings → Devices & Services → Add Integration →
   “Clipsal C-Bus (CNI)”.**

4. **Fill in the form:**
   - **Host / IP** = your CNI's IP (e.g. `192.168.1.50`)
   - **Port** = `10001` (CNI default)
   - **Light groups** — one per line as `address:Name`, e.g.
     ```
     1:Living Room
     4:Kitchen
     ```
   - **Switch groups** / **Cover groups** — same format, for relay loads / blinds.
   - **Project file** *(optional)* — path to a Toolkit `.cbz`/`.xml` (e.g.
     `/config/HOME.cbz`) to auto-fill the names instead of typing them.

5. **Done.** Your C-Bus lights/switches/covers appear as entities. Toggle one in
   Home Assistant — the physical load responds, and flipping the wall switch
   updates Home Assistant instantly. Add more or rename later via the
   integration's **Configure** button.

> **If something's not connecting,** it's almost always the single-connection
> rule (step "Before you start") or an unpowered bus. See
> [Troubleshooting](#troubleshooting).

## How it works

```
 C-Bus network ──(PCI)── CNI ──TCP:10001── Home Assistant (this integration)
                                              ├─ sends on/off/ramp commands
                                              └─ receives MONITOR-mode events  ← accurate feedback
```

The raw C-Bus serial protocol is handled by a vendored copy of the proven
[`cbus`/libcbus](https://github.com/micolous/cbus) protocol library (see
[`custom_components/cbus/vendor/`](custom_components/cbus/vendor/) and its
`NOTICE.md`). Our integration adds the Home Assistant entities, connection
management (auto-reconnect, re-runs the PCI init sequence), and a group-level
state cache.

## Requirements

1. A **C-Bus CNI** (e.g. 5500CN/CN2) or a PCI exposed over TCP, reachable from
   Home Assistant on its raw port (default **10001**).
2. **Exclusive access to the CNI.** A CNI allows only **one** TCP connection at
   a time. While Home Assistant is connected you cannot also have C-Bus Toolkit,
   C-Gate, or another controller connected to the same CNI. (If you need shared
   access, use a C-Gate-based setup instead.)
3. A powered, working C-Bus network. You can check the CNI's own status page at
   `http://<cni-ip>/` — it should report **C-Bus status: OK** with a network
   voltage of roughly **30–36 V**.

## Installation

### HACS (recommended)

1. HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/ariesnaceno/cbushomeassistant` as an **Integration**.
3. Install **Clipsal C-Bus (CNI)** and restart Home Assistant.

### Manual

Copy `custom_components/cbus` into your Home Assistant `config/custom_components`
directory and restart Home Assistant.

## Configuration

1. **Settings → Devices & Services → Add Integration → Clipsal C-Bus (CNI)**.
2. Enter:
   - **Host / IP** of the CNI.
   - **TCP port** (CNI default `10001`).
   - **Light groups** — dimmable lighting, one per line as `group:Friendly Name`.
   - **Switch groups** — non-dimmable relay loads (fans, pumps, exhausts).
   - **Cover groups** — blinds/shutters driven via the lighting application.
   - **Project file** *(optional)* — path to a **C-Bus Toolkit** backup
     (`.cbz` or `.xml`, e.g. `/config/HOME.cbz`). If supplied, the integration
     reads your group **names** from it and pre-fills the list on a confirmation
     page, so you don't have to type them.

   ```
   # Lights
   1:Living Room
   4:Kitchen
   # Switches
   12:Exhaust Fan
   # Covers
   30:Living Room Blind
   ```

### Managing groups after setup (menu)

Open the integration's **Configure** button any time for a simple menu:

- **Pick from a C-Bus Toolkit file** — enter a `.cbz`/`.xml` path and get a
  **checklist of every group**; tick the ones you want and choose the type.
- **➕ Add a light / switch / cover** — enter a group number and name; repeat to
  add more.
- **🗑 Remove groups** — tick groups to remove.
- **💾 Save and finish** — applies your changes.

No need to hand-type `address:Name` lines unless you want to.

> **Group names:** in direct-CNI mode there is no project database on the bus to
> read names from. Either point the setup at a **C-Bus Toolkit** backup file to
> auto-fill them (recommended), or type them manually as `address:Name`.

## Features

| Feature | Supported |
|--------|-----------|
| Lights: on/off + dimming (0–255) | ✅ |
| Lights/covers: transition / ramp time | ✅ |
| Switches (relay on/off groups) | ✅ |
| Covers (blinds/shutters with position) | ✅ |
| Real-time feedback from physical switches | ✅ |
| Auto-reconnect (re-runs PCI init) | ✅ |
| Auto-fill group names from Toolkit `.cbz`/`.xml` | ✅ |
| No C-Gate / no MQTT broker required | ✅ |

C-Bus levels (0–255) map directly to Home Assistant brightness (0–255).

### Initial state

Because there is no level database to poll, a group's state is **unknown until
the first event** (any change on the bus, or a command from Home Assistant).
After that, MONITOR mode keeps it accurate. (Optional level status-requests on
startup are on the roadmap.)

## Using C-Bus Toolkit alongside

A CNI accepts **only one** connection at a time, so Home Assistant and C-Bus
Toolkit **cannot both be connected to the same CNI simultaneously**. When you
need to program with Toolkit:

1. In Home Assistant, **disable** the integration entry (Settings → Devices &
   Services → C-Bus → ⋮ → **Disable**), or stop Home Assistant.
2. Connect with Toolkit, do your work, then **disconnect Toolkit**.
3. **Re-enable** the integration in Home Assistant.

> Tip: after the integration has held the CNI, an abrupt disconnect can leave the
> CNI holding a stale session. If Home Assistant then reports
> `*** Connection already in use` and won't reconnect, **power-cycle the CNI** to
> clear it. If you need Toolkit and Home Assistant connected at the same time,
> use a C-Gate-based setup instead (C-Gate multiplexes the single CNI link).

## Troubleshooting

- **`cannot_connect` during setup** — check the IP/port, and make sure nothing
  else holds the CNI's single connection (Toolkit, C-Gate, another controller).
- **`*** Connection already in use`** — another client owns the CNI. Disconnect
  it; if it persists, power-cycle the CNI to clear a stale session.
- **No state at all / commands do nothing** — check the CNI status page
  (`http://<cni-ip>/`). If it shows **C-Bus status: Power down** or unknown
  voltage, the C-Bus network itself is unpowered — fix the C-Bus power supply.
- Enable debug logging:

  ```yaml
  logger:
    logs:
      custom_components.cbus: debug
      cbus: debug
  ```

## Development

No hardware needed:

- `python3 tests/test_encoding.py` — checks the exact on-wire bytes for lighting
  commands.
- `python3 tests/test_toolkit.py` — checks the Toolkit `.cbz`/`.xml` name parser.

## License

[MIT](LICENSE) for this integration. The vendored `cbus` protocol library under
`custom_components/cbus/vendor/` is **LGPL-3.0-or-later** — see its `NOTICE.md`.
