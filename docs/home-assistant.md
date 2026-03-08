# Home Assistant Integration

Two integration methods are available:

| Method | Requires | Status |
|--------|----------|--------|
| **HACS native integration** | HACS installed in HA | Stable |
| **MQTT Discovery** (standalone bridge) | MQTT broker + Raspberry Pi / server near the toilet | Stable |

---

## HACS native integration

No MQTT broker required. The integration talks directly to the AquaClean over BLE
(local adapter or ESPHome proxy) from within Home Assistant.

### Install

1. **Add the custom repository** — HACS → ⋮ → **Custom repositories**
   → URL: `https://github.com/jens62/geberit-aquaclean` → category **Integration** → Add

2. **Install** — HACS → Integrations → search **Geberit AquaClean**
   → Download → select the latest version

3. **Restart Home Assistant**

4. **Configure** — Settings → Devices & Services → **Add Integration**
   → search **Geberit AquaClean** → fill in:
   - BLE MAC address of your AquaClean (e.g. `38:AB:41:2A:0D:67`)
   - ESPHome Proxy Host (optional — recommended if the toilet is not in BLE range of HA)
   - Poll interval (default 30 s)

> **ESPHome proxy note:** if you use the ESP32 proxy, ensure the `aquaclean-proxy`
> integration in Home Assistant is **disabled** — two simultaneous connections to the
> ESP32 block BLE scanning.  See [esphome-troubleshooting.md](esphome-troubleshooting.md).

### Local BLE vs ESPHome proxy

The **ESPHome Proxy Host** field is the only switch:

| `ESPHome Proxy Host` field | Transport used |
|----------------------------|----------------|
| Empty (left blank)         | Local BLE adapter on the HA machine (bleak) |
| Filled in (e.g. `192.168.0.160`) | ESPHome proxy via aioesphomeapi over TCP |

No separate toggle exists — the presence of the host determines which path is used.
The ESPHome port defaults to `6053`; the encryption key is optional (leave blank for
unencrypted, which is the recommended default on a trusted home LAN).

> **Raspberry Pi built-in adapter (BCM4345 + bleak 2.1.1):** On a Raspberry Pi 5 running
> HA OS, the built-in Bluetooth chip cannot scan and maintain a GATT connection simultaneously.
> This causes repeated 36-second timeouts until the device drops from HA's BLE cache, making
> local BLE unreliable on this hardware.  The **ESPHome proxy is the recommended transport**
> for Raspberry Pi installations.  A USB Bluetooth dongle on the same machine may also work
> (untested).

### Changing settings after setup (options flow)

All settings can be changed without re-adding the integration:

**Settings → Devices & Services → Geberit AquaClean → Configure**

The Configure button opens the options form with all current values pre-filled:
- BLE MAC address
- ESPHome Proxy Host / Port / Encryption Key
- Poll interval

A connection test is performed on save.  The integration reloads automatically — no
HA restart needed.

### Logs

**Settings → System → Logs** — search or filter for `geberit_aquaclean`.

Raw log file: `/config/home-assistant.log`

### Log level

> **Note:** The `config.ini` `[logging] level` setting has **no effect** when running
> under HACS — that setting only applies to the standalone bridge.  Use the options
> below to control log verbosity inside Home Assistant.

**Permanent** — set in `configuration.yaml` and restart HA:

```yaml
logger:
  default: warning
  logs:
    custom_components.geberit_aquaclean: debug   # integration glue code
    aquaclean_console_app: debug                  # BLE protocol library
    aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector: debug  # BLE detail
```

**Dynamic** — no restart required, takes effect immediately:

Settings → Developer Tools → Actions → `logger.set_level` → call action with:

```yaml
custom_components.geberit_aquaclean: debug
aquaclean_console_app: debug
```

Useful levels:
- `warning` — errors and warnings only (default / production)
- `info` — connection lifecycle events
- `debug` — full BLE handshake, GATT operations, coordinator polls

### Dashboard card (button-card)

Two ready-made dashboards are provided:

- **`homeassistant/lovelace/dashboard.yaml`** — recommended; uses
  [custom:button-card](https://github.com/custom-cards/button-card) and
  [custom:timer-bar-card](https://github.com/rianadon/timer-bar-card) (both installable
  from HACS Frontend).  Includes poll countdown, BLE / WiFi signal bars, and live status tiles.
  Also requires the two template sensors from `homeassistant/configuration_hacs.yaml`.
- **`homeassistant/dashboard_button_card_hacs.yaml`** — simpler alternative; uses only
  custom:button-card, no timer-bar-card.

**Install the SVG icons first** — the card uses custom Geberit graphics that must be
copied manually to `/config/www/custom_icons/geberit/` on your HA instance
(File Editor or Samba add-on):

| File in `graphics/` | Required by |
|---------------------|-------------|
| `adjustabletoiletseat.svg` | Toggle Lid button |
| `is_user_sitting-on.svg`, `is_user_sitting-off.svg` | User Sitting sensor |
| `analshower.svg` | Anal Shower button + sensor |
| `ladywash.svg` | Lady Shower button + sensor |
| `dryer_to_the_right-on.svg`, `dryer_to_the_right-off.svg` | Dryer sensor |

**Add the card to your dashboard:**

1. Dashboard → Edit → Add Card → **Manual**
2. Paste the contents of `homeassistant/dashboard_button_card_hacs.yaml`

**Entity IDs** are generated from the fixed device name `"Geberit AquaClean"`, giving
predictable IDs like `binary_sensor.geberit_aquaclean_user_sitting`.  If HA assigns
different IDs on your instance, check **Developer Tools → States** and update the YAML.

**Differences from the MQTT dashboard** (`dashboard_button_card.yaml`):

| MQTT version | HACS version |
|---|---|
| Toggle Lid / Shower are `switch` entities | They are `button` entities — tapping triggers the action |
| Connection Status section | `binary_sensor` + `sensor` entities available; see entity list below |
| ESPHome Proxy Status section | `binary_sensor` + `sensor` entities available; proxy handled internally |

### Web UI

The standalone bridge's web UI (REST API on port 8080) does **not exist** in the HACS
integration — it is only started when running `aquaclean-bridge --mode api`.
The HACS integration never starts that process.

HA's own UI replaces everything the web UI provided:

| Standalone web UI | HA equivalent |
|---|---|
| Live device state | Dashboard (`dashboard_button_card_hacs.yaml`) |
| Toggle lid / showers | Button entities on the dashboard |
| Raw entity values | Developer Tools → States |
| State history | History panel / Logbook |
| Configuration | Settings → Devices & Services → Configure |
| Logs / errors | Settings → System → Logs |

### Entities created

HA registers three devices under **Settings → Devices & Services**:

**Geberit AquaClean** (toilet)

| Platform | Entity |
|----------|--------|
| Binary sensor | User Sitting, Anal Shower Running, Lady Shower Running, Dryer Running, BLE Connected |
| Sensor | Model, Serial Number, SAP Number, Production Date, Initial Operation Date, SOC Versions |
| Sensor (connection) | BLE Connection (device name + MAC), BLE Signal (dBm) |
| Sensor (descale) | Days Until Next Descale, Days Until Shower Restricted, Shower Cycles Until Confirmation, Number of Descale Cycles, Last Descale, Unposted Shower Cycles |
| Sensor (poll) | Last Poll ms, Poll Interval, Next Poll |
| Sensor (stats) | Avg Connect ms, Min Connect ms, Avg Poll ms, Min Poll ms, Avg BLE ms, Min BLE ms, Avg BLE RSSI, Min BLE RSSI, Poll Count, Avg WiFi ms, Min WiFi ms, Avg WiFi RSSI, Min WiFi RSSI |
| Button | Toggle Lid, Toggle Anal Shower, Toggle Lady Shower |

**AquaClean Proxy** *(only when ESPHome host is configured)*

| Platform | Entity |
|----------|--------|
| Binary sensor | Proxy Connected |
| Sensor | Proxy Connection (device name + host:port), WiFi Signal (dBm) |
| Button | Restart AquaClean Proxy |

---

## MQTT Discovery (standalone bridge)

The application integrates with Home Assistant via **MQTT Discovery** — no manual YAML editing required.

For the full setup guide, custom icons, and dashboard card examples see [`homeassistant/SETUP_GUIDE.md`](../homeassistant/SETUP_GUIDE.md).

---

## Architecture

```
AquaClean (BLE) ←→ Raspberry Pi (Bridge) → MQTT Broker → Home Assistant
   [Bathroom]            [Bathroom]           [Network]      [Anywhere]
```

The Raspberry Pi must be physically close to the toilet (BLE range).  Home Assistant can run anywhere on the network.

---

## Quick setup

### 1. Configure MQTT in config.ini

```ini
[MQTT]
server   = 192.168.0.xxx   # IP of your MQTT broker
port     = 1883
topic    = Geberit/AquaClean
```

### 2. Start the bridge

```bash
aquaclean-bridge --mode api
```

By default (`ha_discovery_on_startup = true` in `config.ini`), all Home Assistant MQTT discovery entities are published automatically on every startup — no manual step needed.  You will see in the log:

```
INFO  Published 21 HA discovery entities on startup
```

**Disable automatic publishing** (optional):

```bash
aquaclean-bridge --mode api --no-ha-discovery
```

Or set `ha_discovery_on_startup = false` in `config.ini` to disable permanently.

**Publish manually** (if auto-publish is disabled, or to force a republish):

```bash
aquaclean-bridge --mode cli --command publish-ha-discovery
```

This is safe to run while the service is already active — it uses a separate MQTT connection and requires no BLE.

### 3. Verify in Home Assistant

Go to **Settings → Devices & Services → MQTT** — you will see three devices grouped together:

**Geberit AquaClean** (toilet)

| Type | Entities |
|------|---------|
| Binary sensor | User Sitting, Anal Shower Running, Lady Shower Running, Dryer Running |
| Sensor | SAP Number, Serial Number, Production Date, Description, Initial Operation Date, SOC Versions |
| Sensor (descale) | Days Until Next Descale, Days Until Shower Restricted, Shower Cycles Until Confirmation, Number of Descale Cycles, Last Descale, Unposted Shower Cycles |
| Switch | Toggle Lid, Toggle Anal Shower |

**AquaClean Bridge** (standalone bridge process)

| Type | Entities |
|------|---------|
| Sensor | Connected, Error, System Info, Performance Stats, Last Poll, Poll Interval |

**AquaClean Proxy** *(only when ESPHome host is configured)*

| Type | Entities |
|------|---------|
| Button | Restart AquaClean Proxy |

![Descale Statistics in Home Assistant](homeassistant-descale-statistics.png)

---

## Removing entities

To remove all Geberit AquaClean entities from Home Assistant:

```bash
python main.py --mode cli --command remove-ha-discovery
```

This publishes empty retained payloads to all discovery topics.  Only entities created by `publish-ha-discovery` are affected.

---

## Keeping discovery in sync

The discovery configuration is defined alongside the MQTT publish calls in `main.py`.  When a new `send_data_async()` call is added, a matching entity is added to `get_ha_discovery_configs()` in the same file.

Because `ha_discovery_on_startup = true` by default, entities are automatically re-registered each time the bridge restarts — so after any config change, simply restart the service.

---

## openHAB

The bridge's MQTT discovery messages follow the **Home Assistant MQTT Discovery** format — the same format used by Tasmota, Zigbee2MQTT, and many other devices. openHAB supports this format natively via its **Home Assistant MQTT Components** binding, so no extra work is needed on the bridge side.

To use the bridge with openHAB:

1. Point `[MQTT] server` in `config.ini` at the same MQTT broker that openHAB uses.
2. Run `aquaclean-bridge --mode cli --command publish-ha-discovery` once (or leave `ha_discovery_on_startup = true`).
3. In openHAB, install the **MQTT Binding** and enable the **Home Assistant MQTT Components** feature. openHAB will discover the bridge's devices automatically from the `homeassistant/` discovery topics.

> openHAB's HA-format support is well-maintained and widely used. If a specific entity type does not map cleanly into openHAB's model, fall back to the raw MQTT topics documented in [mqtt.md](mqtt.md) and configure them manually in openHAB.

---

## Manual configuration (alternative)

If you prefer YAML over auto-discovery, see `homeassistant/configuration_mqtt.yaml` for a complete entity configuration.  All MQTT topics are documented in [mqtt.md](mqtt.md).
