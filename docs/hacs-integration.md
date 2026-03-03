# Geberit AquaClean — HACS Custom Integration

This is the **native Home Assistant integration** for the Geberit AquaClean.
It is installed via HACS and configured entirely within the Home Assistant UI — no separate Linux machine, no MQTT broker, and no config files required.

For the alternative MQTT-based setup (standalone bridge on a Raspberry Pi), see [`homeassistant/SETUP_GUIDE.md`](../homeassistant/SETUP_GUIDE.md).

> ### Two connection paths — ESPHome proxy (recommended) or local BLE adapter
>
> **ESPHome proxy** (an ESP32 running the ESPHome `bluetooth_proxy` component) is the
> recommended and well-tested path. It keeps the HA host out of BLE range entirely.
>
> **Local BLE adapter** (a Bluetooth adapter on the HA host — built-in or USB dongle)
> is supported. The integration uses HA's own `bluetooth` stack for local BLE connections
> (`bluetooth.async_ble_device_from_address()` + `bleak_retry_connector.establish_connection()`),
> so there is no raw adapter conflict with HA's scanner.
>
> **⚠️ Raspberry Pi built-in Bluetooth (BCM4345 chip) is not recommended.**
> On RPi 3/4/5 with HA OS, GATT connections consistently fail due to hardware constraints —
> see [Known hardware limitations — local BLE](#known-hardware-limitations--local-ble-adapter).
> A USB Bluetooth dongle or an ESPHome proxy is the reliable alternative.
>
> Reports from local-BLE users (with or without issues) are welcome via GitHub Issues.

---

## Architecture

```
Geberit AquaClean (BLE)
        ↕  Bluetooth Low Energy
  ┌─────────────────────────────────────────┐
  │  Option A: ESP32 ESPHome proxy          │  (recommended)
  │  ↕  TCP/IP (aioesphomeapi, port 6053)  │
  └─────────────────────────────────────────┘
  ┌─────────────────────────────────────────┐
  │  Option B: local BLE adapter on HA host │  (supported)
  └─────────────────────────────────────────┘
        ↕  Internal coordinator
  HA entities (sensors, switches, binary sensors)
```

### How the integration connects to the toilet

The integration uses the same `BluetoothLeConnector` code as the standalone bridge.

- **With ESPHome proxy:** opens a direct TCP connection to the ESP32 and performs every BLE operation (scan, connect, communicate) over that TCP link.
- **Without ESPHome proxy:** uses HA's `bluetooth` stack to locate the device in HA's scanner cache, then connects via `bleak_retry_connector`. Leave the ESPHome Proxy Host field empty during setup.

### What we use (and don't use) from HA's Bluetooth stack

Home Assistant has its own `bluetooth` integration that manages BLE adapters, exposes a Bluetooth panel, and lets integrations subscribe to BLE advertisements natively.

| Feature | This integration | HA native Bluetooth |
|---------|-----------------|---------------------|
| BLE adapter on HA hardware | Optional | Required |
| Device in HA Bluetooth panel | No | Yes |
| BLE auto-discovery | No | Yes |
| ESPHome proxy supported | Yes | Yes (different path) |
| Hardware cost | ESP32 (~€5–15) optional | ESP32 or BT dongle |
| HA scanner cache (local BLE) | **Yes** (`async_ble_device_from_address`) | n/a |

**For local BLE (no ESPHome proxy):** the integration looks up the `BLEDevice` from HA's
scanner cache and connects via `bleak_retry_connector.establish_connection()`. The BLE adapter
is managed by HA; no raw BlueZ/DBus conflict. The toilet does not appear in HA's Bluetooth
panel and there is no BLE-based auto-discovery.

**For ESPHome proxy:** the integration bypasses HA's BLE stack entirely and opens a direct TCP
connection to the ESP32. The HA host needs no BLE adapter.

The ESP32 ESPHome proxy (€5–15) is the recommended path. Local BLE is supported as an alternative.

---

## Prerequisites

- **Home Assistant OS** or Supervised, version **2024.4.1** or newer
- **HACS** installed (see below)
- **GitHub account** (required for HACS authentication)
- **One of the following BLE transports** (choose one):
  - **ESP32** running ESPHome with `bluetooth_proxy` *(recommended)* — see [`docs/esphome.md`](esphome.md)
  - **Local Bluetooth adapter** on the HA host (built-in or USB dongle), recognised by HA as a BLE adapter
- The BLE transport must be physically close to the toilet (BLE range ~10 m, less through walls)

---

## Step 1 — Install HACS

Skip this step if HACS is already installed.

1. Go to **Settings → Add-ons → Add-on Store**
2. Click the **three dots (⋮)** → **Repositories**
3. Add `https://github.com/hacs/addons` and close
4. Search for **Get HACS**, install it, and click **Start**
5. Open the **Logs** tab of the add-on; follow the instructions and restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration**, search for **HACS**, and complete GitHub authentication

> After HACS is installed, enable **Advanced Mode** in your profile (Profile → scroll down → Advanced Mode) to see all options.

---

## Step 2 — Add the custom repository to HACS

1. Open **HACS** from the sidebar
2. Click the **three dots (⋮)** in the top right → **Custom repositories**
3. Paste: `https://github.com/jens62/geberit-aquaclean`
4. Select category: **Integration**
5. Click **Add**

---

## Step 3 — Download the integration

1. In HACS, search for **Geberit AquaClean**
2. Click on the repository card
3. Click **Download** (bottom right)
4. In the version popup, select the latest stable version
   To see pre-release versions: toggle **Show beta versions** in the same popup
5. Click **Download** to confirm

---

## Step 4 — Restart Home Assistant

After downloading, Home Assistant must be restarted before the integration appears in the integration list.

**Settings → System → Restart**

---

## Step 5 — Configure the integration

1. Go to **Settings → Devices & Services**
2. Click **+ Add Integration** (bottom right)
3. Search for **Geberit AquaClean** and select it
4. Fill in the configuration form:

| Field | Description |
|-------|-------------|
| **BLE MAC Address** | MAC address of your AquaClean, e.g. `38:AB:41:2A:0D:67` — find it in the Geberit app or via `ble-scan.py` |
| **ESPHome Proxy Host** | IP address of your ESP32, e.g. `192.168.0.160` — **leave empty to use the local BLE adapter** |
| **ESPHome Proxy Port** | Default: `6053` — only change if you customised the ESPHome port |
| **ESPHome Encryption Key** | Base64 noise PSK from your ESPHome YAML — leave blank if not set |
| **Poll Interval (seconds)** | How often to fetch data; default `30` |

5. Click **OK** — behaviour depends on the transport:
   - **ESPHome proxy configured:** a live BLE connection test is performed (connects to the toilet and disconnects). Requires the toilet to be on and the ESP32 reachable. Allow up to 30 seconds.
   - **ESPHome Proxy Host left empty (local BLE):** only the presence of a local Bluetooth adapter is checked. No live BLE test is performed — the first poll after setup verifies actual connectivity.

> **No local Bluetooth adapter?** If you leave the ESPHome Proxy Host empty and your HA host has no BLE adapter, setup will fail with "No local Bluetooth adapter found". Either add a USB BT dongle or fill in the ESPHome Proxy Host.

---

## Entities

After setup, HA registers three devices under Settings → Devices & Services:

### Geberit AquaClean (toilet)

| Type | Entity |
|------|--------|
| Binary sensor | **BLE Connected** — `True` (green) when the last poll reached the Geberit via BLE, `False` (red) when the last poll failed; attribute `connected_at` shows the timestamp of the last successful BLE connect |
| Binary sensor | User Sitting, Anal Shower Running, Lady Shower Running, Dryer Running |
| Sensor | **BLE Connection** — shows `{BLE device name} (MAC)` after the first successful poll, or just the MAC until then |
| Sensor | Model, Serial Number, SAP Number, Production Date, Initial Operation Date, SOC Versions |
| Sensor (descale) | Days Until Next Descale, Days Until Shower Restricted, Shower Cycles Until Confirmation, Number of Descale Cycles, Last Descale, Unposted Shower Cycles |
| Button | Toggle Lid, Toggle Anal Shower, Toggle Lady Shower |
| Sensor (poll) | Last Poll, Poll Interval, Next Poll |
| Sensor | **BLE Signal** — Geberit BLE advertisement RSSI in dBm (signal strength between ESP32 and toilet) |

### AquaClean Proxy *(only when ESPHome host is configured)*

| Type | Entity |
|------|--------|
| Binary sensor | **Connected** — shows Connected (green) as long as the ESP32 is reachable; only drops to Disconnected when a poll actually fails at the TCP level |
| Sensor | **Connection** — shows `{ESPHome device name} (host:port)` after the first successful poll, or just `host:port` until then |
| Sensor | **WiFi Signal** — ESP32 WiFi RSSI in dBm (requires `platform: wifi_signal` in ESPHome YAML) |
| Sensor (diagnostic) | **Free Heap** — ESP32 free heap memory in bytes (requires `platform: debug, free:` in ESPHome YAML) |
| Sensor (diagnostic) | **Max Free Block** — ESP32 max contiguous free block in bytes (requires `platform: debug, block:` in ESPHome YAML) |
| Sensor (diagnostic) | **Last Connect** — connect time of the last poll cycle in ms |
| Sensor (diagnostic) | **Last Poll** — GATT data fetch time of the last poll cycle in ms |
| Sensor (diagnostic) | **Avg Connect** — rolling average connect time since HA started in ms |
| Sensor (diagnostic) | **Min Connect** — session minimum connect time in ms |
| Sensor (diagnostic) | **Max Connect** — session maximum connect time in ms |
| Sensor (diagnostic) | **Avg Poll** — rolling average GATT fetch time since HA started in ms |
| Sensor (diagnostic) | **Min Poll** — session minimum GATT fetch time in ms |
| Sensor (diagnostic) | **Max Poll** — session maximum GATT fetch time in ms |
| Sensor (diagnostic) | **Poll Samples** — number of successful polls since HA started |
| Sensor (diagnostic) | **Transport** — connection path: `bleak` (local BLE), `esp32-wifi`, or `esp32-eth` |
| Sensor (diagnostic) | **Avg BLE RSSI** — session average BLE signal strength between ESP32 and toilet (dBm) |
| Sensor (diagnostic) | **Min BLE RSSI** — session worst BLE signal strength (dBm) |
| Sensor (diagnostic) | **Max BLE RSSI** — session best BLE signal strength (dBm) |
| Sensor (diagnostic) | **Avg WiFi RSSI** — session average ESP32 WiFi signal (dBm; `Unavailable` in ETH mode) |
| Sensor (diagnostic) | **Min WiFi RSSI** — session worst ESP32 WiFi signal (dBm; `Unavailable` in ETH mode) |
| Sensor (diagnostic) | **Max WiFi RSSI** — session best ESP32 WiFi signal (dBm; `Unavailable` in ETH mode) |
| Button | Restart AquaClean Proxy |

---

## Dashboard

A ready-to-use Lovelace dashboard is included in the repository at [`lovelace/dashboard.yaml`](../lovelace/dashboard.yaml).
It covers live status, controls, poll countdown, descale statistics, and device information — using only built-in HA card types (no extra HACS plugins needed).

### Import

1. **Settings → Dashboards → Add Dashboard** → name it e.g. "AquaClean", click **Create**
2. Open the new dashboard → click the **pencil (edit)** icon → **three dots (⋮)** → **Raw configuration editor**
3. Paste the contents of [`lovelace/dashboard.yaml`](../lovelace/dashboard.yaml) and click **Save**

> **⚠️ HA strips all YAML comments on save.**
> When you save in the Raw configuration editor, Home Assistant permanently removes every
> comment line from your dashboard YAML. Any commented-out optional blocks (like the gauge
> below) will be gone the next time you open the editor.
> **Add optional cards before saving**, or keep the original `lovelace/dashboard.yaml`
> file as your reference copy.

### Poll countdown bar *(requires `custom:timer-bar-card`)*

The Poll Status card includes a `custom:timer-bar-card` that drains smoothly from the
last poll to the next poll. It requires the **Timer Bar Card** frontend plugin from HACS:

1. Open **HACS → Frontend**
2. Search for **Timer Bar Card** → Install → reload browser

The bar card reads `sensor.geberit_aquaclean_last_poll` and
`sensor.geberit_aquaclean_next_poll` directly as start/end timestamps.
No additional template sensors are needed.

### Signal quality labels *(requires `configuration.yaml` additions)*

The **BLE Connection** and **ESPHome Proxy** dashboard cards display labels such as
*"Excellent (−62 dBm)"* or *"Fair (−74 dBm)"* for signal strength. These are
computed by two template sensors that must be added to your Home Assistant
`configuration.yaml`.

See [`homeassistant/configuration_hacs.yaml`](../homeassistant/configuration_hacs.yaml)
for the complete template sensor definitions.

**To add them:**

1. Open your `configuration.yaml`
2. Paste the `template:` block from
   [`homeassistant/configuration_hacs.yaml`](../homeassistant/configuration_hacs.yaml)
3. Reload via **Developer Tools → YAML → Template Entities → Reload**

---

**Nobody on the toilet — User Sitting = Frei:**

![Geberit AquaClean device page — toilet unoccupied, Toggle Lid pressed](hacs-toggle-lid.png)

**Toilet in use — User Sitting = Erkannt:**

![Geberit AquaClean device page — user sitting detected](hacs-user-is-sitting.png)

---

## Automations

### Error notification when the toilet is unreachable

When a poll fails, `binary_sensor.geberit_aquaclean_ble_connected` turns `off` and its `error_hint` attribute contains a human-readable explanation (e.g. *"Ensure the Geberit AquaClean is powered on and within BLE range…"*).

Use this to send a push notification with the specific hint:

**Settings → Automations & Scenes → Create Automation → Edit in YAML:**

```yaml
alias: AquaClean — notify on connection error
trigger:
  - platform: state
    entity_id: binary_sensor.geberit_aquaclean_ble_connected
    to: "off"
action:
  - service: notify.mobile_app_your_phone   # replace with your notifier
    data:
      title: "AquaClean unreachable"
      message: >
        {{ state_attr('binary_sensor.geberit_aquaclean_ble_connected', 'error_hint')
           | default('Connection failed — check HA logs for details.') }}
```

> Replace `notify.mobile_app_your_phone` with your actual notifier (e.g. `notify.mobile_app_jens_iphone`).
> Add a `for: "00:02:00"` condition to the trigger to avoid alerts on transient single-poll failures.

The `error_hint` is also visible directly on the **BLE Connection** and **ESPHome Proxy** dashboard cards — it appears as the **Error Hint** row when a poll has failed and is blank when everything is working.

---

## Updating

HACS shows a notification when a new version is available (Settings → Devices & Services → HACS shows a badge).

To update:
1. Open HACS → find Geberit AquaClean → **Update** (or three dots → **Redownload**)
2. Restart Home Assistant

> **HACS only sees GitHub Releases, not bare git tags.** If a new version does not appear after clicking the three dots → **Update information**, the release was likely only tagged but not published as a GitHub Release.

---

## Logging and troubleshooting

### Enable debug logging

Add to your `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.geberit_aquaclean: debug   # integration glue code
    aquaclean_console_app: debug                 # BLE protocol library
```

> This setting has no effect on the standalone bridge; it only controls the HACS integration logging.

> **`trace` / `silly` cannot be set in `configuration.yaml`.**
> HA validates log level names at config load time, before custom integrations are imported.
> Because `trace` and `silly` are registered by our integration (not by Python's standard
> `logging` module), HA does not know them yet and rejects the config with an error.
> Use the dynamic method or the startup automation below instead.

### Find the logs

**Settings → System → Logs → Show raw logs** — filter for `aquaclean` or `geberit`.

Or use the dynamic approach (no restart required):
**Settings → Developer Tools → Actions** → call action `logger.set_level` with:

```yaml
custom_components.geberit_aquaclean: debug
aquaclean_console_app: debug
```

### Enable trace / silly logging

`trace` and `silly` work via `logger.set_level` once the integration is loaded, but not in
`configuration.yaml` (see note above). To apply them automatically at every HA start, add
this automation:

**Settings → Automations & Scenes → Create Automation → Edit in YAML:**

```yaml
alias: AquaClean — enable trace logging at startup
trigger:
  - platform: homeassistant
    event: started
action:
  - action: logger.set_level
    data:
      custom_components.geberit_aquaclean: trace
      aquaclean_console_app: trace   # or: silly
```

The automation fires after all integrations have loaded, so `trace`/`silly` are already
registered when the action runs.

### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Cannot connect" on config form | Wrong MAC, ESP32 unreachable, or toilet off | Check MAC, verify ESP32 at `http://192.168.0.160`, ensure toilet is on |
| New version not shown in HACS | Version was pushed as git tag only, not a GitHub Release | Click three dots → **Update information**; if still missing, a Release is missing on GitHub |
| All entities unavailable after setup | Coordinator poll failed | Check logs for the actual error; most likely ESP32 or BLE issue |
| `AttributeError: 'HassLogger' has no attribute 'trace'` | Outdated version (< 2.4.18) | Update to latest via HACS |
| Duplicate subscription error (`Only one API subscription`) | Previous TCP connection not released | Restart HA; covered by v2.4.15+ fix |
| E0003 every poll, times out at 36 s, then "not in cache" | Raspberry Pi built-in BT (BCM4345) + bleak 2.1.1 hardware limitation | Use a USB BT dongle or ESPHome proxy — see [Known hardware limitations](#known-hardware-limitations--local-ble-adapter) |

### Known hardware limitations — local BLE adapter

#### Raspberry Pi built-in Bluetooth (BCM4345 chip) — not reliable

Tested on RPi with the built-in BCM4345C0 chip, HA OS, and bleak 2.1.1 (the version HA ships as of early 2026):

**Observed behaviour (log `local-assets/config-hacs-no-esp.log`, 2026-03-03):**

1. **Phase 1 (~5 polls, ~3 min):** Device IS found in HA's scanner cache. `establish_connection` is called but times out at exactly 36 s every attempt. `Finished fetching geberit_aquaclean data in 36.0 seconds (success: False)`.
2. **Phase 2 (onwards):** Device drops from HA's cache. "Device not in cache yet; waiting up to 30 s for advertisement" — permanent timeout. The first phase's connect attempts suppressed HA's scanner, so no fresh advertisements arrived to renew the cache entry.

**Root causes:**

| # | Problem | Effect |
|---|---------|--------|
| 1 | **BCM4345 cannot scan and connect simultaneously** — the chip pauses the active BLE scanner while a GATT connection is being established | Each 36 s failed connect attempt blocks HA's scanner; no advertisements received; Geberit drops from cache after ~5 failures |
| 2 | **bleak 2.1.1 takes ~25 s per connect attempt** on RPi5 + BCM4345C0 + BlueZ 5.84 (bleak 2.0.0 = ~1.8 s on the same hardware) | `bleak_retry_connector.establish_connection()` has `MAX_CONNECT_TIME = 35 s` — barely enough for one attempt; never completes |

These are not code bugs. They are hardware and driver constraints that cannot be worked around without changing the hardware or BLE library.

**ESPHome proxy is unaffected** — the ESP32's dedicated BLE radio has no scanner/connector conflict and bleak 2.1.1 is not involved.

**Workarounds for local BLE:**

- **USB Bluetooth dongle** — a dedicated adapter avoids the scan/connect scheduler conflict. Tested adapters: *unknown, community reports welcome*.
- **ESPHome proxy** — the recommended path; an ESP32 (~€5–15) eliminates the problem entirely.

### ESPHome proxy troubleshooting

See [`docs/esphome-troubleshooting.md`](esphome-troubleshooting.md) for ESP32-specific issues (stuck BLE scanner, subscription conflicts, auto-restart).

---

## Disabling the integration

The integration can be disabled or deleted from **Settings → Devices & Services → Geberit AquaClean → three dots (⋮)**.

Deleting it removes the config entry but leaves historical data in the HA recorder. To fully remove all traces (devices, entities), restart HA after deletion.

---

## Coexistence with the standalone bridge

Do **not** run both the HACS integration and the standalone MQTT bridge against the same ESP32 at the same time. Both open TCP connections to the ESP32 and compete for the single BLE advertisement subscription slot. See [`docs/ble-coexistence.md`](ble-coexistence.md).

Also disable the **ESPHome** integration in HA (`Settings → Devices & Services → ESPHome`) if it is tracking the same ESP32 proxy — it would occupy the subscription slot and block all BLE connections from the bridge.
