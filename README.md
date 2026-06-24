# Clipsal C-Bus integration for Home Assistant

A custom [Home Assistant](https://www.home-assistant.io/) integration that
connects **Clipsal C-Bus** lighting to Home Assistant through a **C-Gate**
server, with **accurate, real-time status feedback**.

Whenever a C-Bus group changes — from Home Assistant, a physical wall switch, a
scene, a PIR/occupancy sensor, a timer, or any other bus event — Home Assistant
reflects the change immediately. This is achieved by listening to C-Gate's
**status-change stream** rather than polling, so the state in Home Assistant
always matches the real state of the bus.

## How it works

```
 C-Bus network ──(PCI / CNI)── C-Gate ──┬── command port (20023)  ← HA sends on/off/ramp + queries levels
                                        └── status-change port (20025) → HA receives real-time level changes
```

* **`local_push`** integration — no polling, instant updates.
* On every (re)connect, the integration re-queries each configured group's
  level so state can never silently drift.
* Automatic reconnection if C-Gate restarts or the network drops.

## Requirements

1. A running **C-Gate** server (Clipsal's free C-Bus server software) with
   network access from your Home Assistant host.
2. C-Gate's TCP ports enabled for your Home Assistant host. Edit C-Gate's
   `config/access.txt` to allow your HA IP, then ensure these are enabled in
   `config/C-GateConfig.txt`:
   - `command.port=20023`
   - `event.port=20024`
   - `status-change.port=20025`
3. Your C-Bus project loaded and started in C-Gate (e.g. `project load HOME`
   then `project start HOME`).

## Installation

### HACS (recommended)

1. In HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/ariesnaceno/cbushomeassistant` as an **Integration**.
3. Install **Clipsal C-Bus (C-Gate)** and restart Home Assistant.

### Manual

Copy `custom_components/cbus` into your Home Assistant `config/custom_components`
directory and restart Home Assistant.

## Configuration

1. **Settings → Devices & Services → Add Integration → Clipsal C-Bus (C-Gate)**.
2. Enter:
   - **Host / IP** of the C-Gate server.
   - **Project name** (exactly as loaded in C-Gate, e.g. `HOME`).
   - **Network number** (usually `254`).
   - **Command port** (`20023`) and **Status-change port** (`20025`).
   - **Light groups** — dimmable lighting, one per line as `group:Friendly Name`.
   - **Switch groups** — non-dimmable relay loads (fans, pumps, exhausts).
   - **Cover groups** — blinds/shutters driven via the lighting application.

     ```
     # Lights
     1:Living Room
     4:Kitchen
     # Switches
     12:Exhaust Fan
     # Covers
     30:Living Room Blind
     ```

You can edit any of the group lists later via the integration's **Configure**
button.

## Features

| Feature | Supported |
|--------|-----------|
| Lights: on/off + dimming (0–255) | ✅ |
| Lights/covers: transition / ramp time | ✅ |
| Switches (relay on/off groups) | ✅ |
| Covers (blinds/shutters with position) | ✅ |
| Real-time feedback from physical switches | ✅ |
| Auto-reconnect & state re-sync | ✅ |
| Multiple C-Gate projects / entries | ✅ |

C-Bus levels (0–255) map directly to Home Assistant brightness (0–255).

## Troubleshooting

- **`cannot_connect` during setup** — verify the host/ports and that your HA IP
  is allowed in C-Gate `access.txt`.
- **State not updating from wall switches** — confirm the **status-change port
  (20025)** is enabled and reachable; this stream is what provides live feedback.
- Enable debug logging:

  ```yaml
  logger:
    logs:
      custom_components.cbus: debug
  ```

## License

[MIT](LICENSE)
