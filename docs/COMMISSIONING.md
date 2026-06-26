# C-Bus → Home Assistant — Installer Commissioning Checklist

A field runbook for integrating a Clipsal C-Bus site into Home Assistant using
this integration + the **C-Bus CNI Relay** add-on. Print it or keep it on your
phone. Tick as you go.

> **Golden rule:** the CNI allows **one** connection at a time. Home Assistant
> (via the relay) and **C-Bus Toolkit can never be connected at the same time.**
> Do all Toolkit commissioning **first**, then hand the CNI to HA.

---

## 0. Before the visit (prep)
- [ ] Client's **C-Bus network is already commissioned in Toolkit** (units have
      group addresses assigned). HA does **not** program C-Bus — it rides on it.
- [ ] You have, or will export on-site, the **Toolkit project backup** (`.cbz`)
      — optional but gives real group names in HA.
- [ ] A **Home Assistant** host is available (HA OS / supervised — needs the
      add-on store). Note its **IP address**: `____________`
- [ ] Confirm there is a **CNI / network PCI** (Ethernet), not just a serial PCI.

## 1. Network + CNI
- [ ] CNI is on the LAN. Find its IP: `____________` (router client list, or it
      may be static).
- [ ] Browse to `http://<cni-ip>/` → confirm it's a **Clipsal CNI** and note:
  - [ ] **C-Bus status: OK**, voltage **~30–36 V** (if "Power down"/Unknown, the
        C-Bus network isn't powered — fix the C-Bus PSU before going further).
  - [ ] Record: CNI IP `________`  TCP port (default **10001**)  MAC `________`
- [ ] **Disconnect Toolkit / C-Gate / any controller** from the CNI now.

## 2. Finish ALL Toolkit work first
- [ ] Program every unit's group addresses, switch button functions
      (**On/Off or Toggle**, bound to the right group), scenes, etc.
- [ ] **Test at the wall** that switches operate loads.
- [ ] (Optional) **Export the project** `.cbz` to the HA host, e.g.
      `/config/<site>.cbz` (Samba/SSH/File editor).
- [ ] **Close Toolkit / disconnect it from the CNI.** ← do not skip.
- [ ] **Power-cycle the CNI** so it releases the Toolkit session cleanly.

> Why: the CNI holds its one session even after a client disconnects. A power
> cycle here guarantees HA gets a clean session next.

## 3. Install the C-Bus CNI Relay add-on (recommended)
- [ ] HA → **Settings → Add-ons → Add-on Store → ⋮ → Repositories** → add
      `https://github.com/ariesnaceno/cbushomeassistant`
- [ ] Install **C-Bus CNI Relay** → **Configuration** tab:
  - `cni_host`: CNI IP (`192.168.x.x`)  ·  `cni_port`: `10001`  ·  `listen_port`: `10010`
- [ ] **Start** it. Set **Start on boot: ON**, **Watchdog: ON**,
      **Auto-update: OFF**.
- [ ] **Log** tab should show `Connected to CNI <ip>:10001`.

> The relay holds the CNI permanently so HA can restart/update **without a CNI
> power-cycle**. (Only a full host reboot / HA-OS update needs a one-time CNI
> power-cycle afterwards.)

## 4. Install the integration (HACS)
- [ ] HACS → ⋮ → **Custom repositories** → add the same repo URL, category
      **Integration** → download.
- [ ] **Restart Home Assistant Core.**
- [ ] **Settings → Devices & Services → Add Integration → "Clipsal C-Bus"**.
- [ ] Connection:
  - **Host** = the **HA host IP** (the relay), **Port = 10010**  *(via relay)*
  - *(or the CNI IP + 10001 directly if not using the relay — then HA restarts
    need a CNI power-cycle)*
  - Optional: point at the `.cbz` project file to auto-fill names.
- [ ] Entry created, shows **connected**.

## 5. Add the groups
Pick one (auto-discover is fastest on a powered site):

**A. Auto-discover (no typing):**
- [ ] Integration → **Configure → 🔍 Auto-discover groups from the bus**.
- [ ] **Walk the site and press each wall switch once** (each press transmits
      its group onto the bus).
- [ ] Reopen the option → tick the discovered groups → choose **type**
      (light / switch / cover) → **Submit**. Repeat as you press more.

**B. Toolkit file:**
- [ ] Configure → **Pick from a C-Bus Toolkit file** → enter `.cbz`/`.xml` path
      → tick groups → choose type.

**C. Manual:** Configure → **➕ Add light/switch/cover** for one-offs.

## 6. Name & type
- [ ] Rename entities to a consistent convention, e.g. **`Area – Load`**
      (`Kitchen – Downlights`).
- [ ] Set the **right type**: dimmable load → light; relay/on-off load that
      isn't a light (fan, pump, gate) → **switch**; blind/shutter → cover.
- [ ] Assign entities to **HA Areas** so dashboards/voice work cleanly.

## 7. Verify (do this with the client watching)
- [ ] **HA → C-Bus:** toggle a few entities in HA → loads respond.
- [ ] **C-Bus → HA:** press the **wall switches** → HA entities update live
      (~1–2 s). *(Note: a relay's own local override button and Toolkit do NOT
      transmit on the bus, so they won't reflect in HA — only real inputs do.)*
- [ ] Quick **HA Core restart** → C-Bus stays online, **no CNI power-cycle**
      (proves the relay is doing its job).

## 8. Handover notes for the client
- [ ] Routine HA updates: use **Restart Home Assistant Core** — C-Bus stays up.
- [ ] **HA-OS updates / full reboots / power outages where the CNI stays
      powered**: may need **one CNI power-cycle** afterwards (rare). A real total
      power failure self-heals (CNI loses power too → boots clean).
- [ ] To re-commission in **Toolkit** later: **stop the relay add-on →
      power-cycle CNI → use Toolkit → close Toolkit → power-cycle CNI → start
      relay**. (They can't share the CNI.)
- [ ] (Optional, for unattended sites) fit a **smart plug / DIN smart relay on
      the CNI** + an HA automation to auto power-cycle it if it ever can't
      connect — makes even full reboots hands-off.

## 9. Troubleshooting quick-ref
| Symptom | Likely cause / fix |
|---|---|
| `cannot_connect` / `*** Connection already in use` | Something else holds the CNI (Toolkit/C-Gate). Disconnect it; power-cycle the CNI. |
| Entities all *unavailable* after a full reboot | Relay went down with the host → CNI zombie. **Power-cycle the CNI once.** |
| Wall switch press doesn't update HA | Only **transmitting inputs** show. Relay local buttons / Toolkit don't broadcast. |
| Live sync stopped (updates only on reconnect) | Monitor session disrupted → **Restart HA Core**. |
| Entity shows on/off but no dimming | Load is on a **relay** unit (on/off only), not a dimmer — expected. |
| No state / commands do nothing | Check `http://<cni-ip>/` for C-Bus voltage; bus may be unpowered. |

---

### Site record (fill in & keep)
```
Client / site:        ____________________
HA host IP:           ____________________
CNI IP / port:        ____________ / 10001
Relay listen port:    10010
Integration version:  ____________
# groups (light/switch/cover): ___ / ___ / ___
Toolkit project file: ____________________
Commissioned by / date: ____________________
```
