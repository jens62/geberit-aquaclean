# MQTT Reference

MQTT is used in **service mode** and **api mode** (when `mqtt_enabled = true` in `config.ini`).

All topics are prefixed with the value of `[MQTT] topic` in `config.ini` (default: `Geberit/AquaClean`).  In the tables below the prefix is written as `{prefix}`.

---

## Published topics (outgoing)

The application publishes to these topics.  All messages are published with `retain=True`.

### Connection status

| Topic | Values | Description |
|-------|--------|-------------|
| `{prefix}/centralDevice/connected` | `Connecting to <addr> ...` → `True` → `False` | BLE connection lifecycle |
| `{prefix}/centralDevice/error` | error string or `No error` | Last BLE error (cleared to `No error` on each new connect attempt) |

### Device state (monitor)

Published every poll cycle and after each relevant command.

| Topic | Values | Description |
|-------|--------|-------------|
| `{prefix}/peripheralDevice/monitor/isUserSitting` | `True` / `False` | Seat occupancy sensor |
| `{prefix}/peripheralDevice/monitor/isAnalShowerRunning` | `True` / `False` | Anal shower active |
| `{prefix}/peripheralDevice/monitor/isLadyShowerRunning` | `True` / `False` | Lady shower active |
| `{prefix}/peripheralDevice/monitor/isDryerRunning` | `True` / `False` | Dryer active |

### Device information

Published on connect (when identification data is fetched).

| Topic | Example value | Description |
|-------|---------------|-------------|
| `{prefix}/peripheralDevice/information/Identification/SapNumber` | `966.848.00.0` | SAP article number |
| `{prefix}/peripheralDevice/information/Identification/SerialNumber` | `HB23XXEUXXXXXX` | Serial number |
| `{prefix}/peripheralDevice/information/Identification/ProductionDate` | `11.04.2023` | Production date |
| `{prefix}/peripheralDevice/information/Identification/Description` | `AquaClean Mera Comfort` | Model name |
| `{prefix}/peripheralDevice/information/initialOperationDate` | `31.05.2024` | Date first put into service |
| `{prefix}/peripheralDevice/information/SocVersions` | version string | SOC firmware version |

---

## Subscribed topics (incoming)

The application subscribes to these topics and reacts to incoming messages.

### Commands

| Topic | Payload | Effect |
|-------|---------|--------|
| `{prefix}/peripheralDevice/control/toggleLidPosition` | any | Toggle lid open/closed |
| `{prefix}/peripheralDevice/control/toggleAnal` | any | Toggle anal shower on/off |

### Connection control

| Topic | Payload | Effect |
|-------|---------|--------|
| `{prefix}/centralDevice/control/connect` | any | Request BLE connect |
| `{prefix}/centralDevice/control/disconnect` | any | Request BLE disconnect (persistent mode) |

### Runtime configuration

| Topic | Payload | Effect |
|-------|---------|--------|
| `{prefix}/centralDevice/config/bleConnection` | `persistent` or `on-demand` | Switch BLE connection mode without restart |
| `{prefix}/centralDevice/config/pollInterval` | float (seconds) | Set poll interval; `0` disables background polling |

---

## Example: monitor with MQTT Explorer

Subscribe to `Geberit/AquaClean/#` to see all published values in real time.

## Example: trigger lid toggle

```bash
mosquitto_pub -h YOUR_BROKER -t "Geberit/AquaClean/peripheralDevice/control/toggleLidPosition" -m "1"
```

## Example: switch to on-demand mode

```bash
mosquitto_pub -h YOUR_BROKER -t "Geberit/AquaClean/centralDevice/config/bleConnection" -m "on-demand"
```

## Example: set poll interval to 30 seconds

```bash
mosquitto_pub -h YOUR_BROKER -t "Geberit/AquaClean/centralDevice/config/pollInterval" -m "30"
```
