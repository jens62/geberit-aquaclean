# CLI Reference

The CLI mode runs a single command and exits.  Results are always written to **stdout as JSON**; log output goes to **stderr**.

```bash
python main.py --mode cli --command <command> [--address <ble-mac>]
```

`--address` overrides the BLE device address from `config.ini` for commands that need BLE.

### Startup flags (service and api modes)

| Flag | Description |
|------|-------------|
| `--ha-discovery` | Force-publish HA MQTT discovery on startup (overrides `ha_discovery_on_startup = false` in config) |
| `--no-ha-discovery` | Skip HA MQTT discovery on startup (overrides `ha_discovery_on_startup = true` in config) |

```bash
aquaclean-bridge --mode api --no-ha-discovery   # start without republishing HA entities
aquaclean-bridge --mode api --ha-discovery      # force publish even if disabled in config
```

These flags have no effect in `--mode cli`.

Redirect stderr to suppress log noise:

```bash
python aquaclean_console_app/main.py --mode cli --command user-sitting-state 2>aquaclean.log
```
```json
{
  "status": "success",
  "command": "user-sitting-state",
  "device": "AquaClean Mera Comfort",
  "serial_number": "HB23XXEUXXXXXX",
  "data": {
    "is_user_sitting": false
  },
  "message": "Command user-sitting-state completed"
}
```

---

## Commands that require a BLE connection

### Device state

| Command | Response fields |
|---------|----------------|
| `status` | `is_user_sitting`, `is_anal_shower_running`, `is_lady_shower_running`, `is_dryer_running` |
| `system-parameters` | same as `status` |
| `user-sitting-state` | `is_user_sitting` |
| `anal-shower-state` | `is_anal_shower_running` |
| `lady-shower-state` | `is_lady_shower_running` |
| `dryer-state` | `is_dryer_running` |

```bash
python main.py --mode cli --command status
```
```json
{
  "status": "success",
  "command": "status",
  "device": "AquaClean Mera Comfort",
  "serial_number": "HB23XXEUXXXXXX",
  "data": {
    "is_user_sitting": false,
    "is_anal_shower_running": false,
    "is_lady_shower_running": false,
    "is_dryer_running": false
  },
  "message": "Command status completed"
}
```

### Device information

| Command | Response fields |
|---------|----------------|
| `info` | `sap_number`, `serial_number`, `production_date`, `description`, `initial_operation_date` |
| `identification` | `sap_number`, `serial_number`, `production_date`, `description` |
| `initial-operation-date` | `initial_operation_date` |
| `soc-versions` | `soc_versions` |
| `statistics-descale` | `days_until_next_descale`, `days_until_shower_restricted`, `shower_cycles_until_confirmation`, `number_of_descale_cycles`, `date_time_at_last_descale`, `unposted_shower_cycles` |

```bash
python main.py --mode cli --command info
```
```json
{
  "status": "success",
  "command": "info",
  "device": "AquaClean Mera Comfort",
  "serial_number": "HB23XXEUXXXXXX",
  "data": {
    "sap_number": "966.848.00.0",
    "serial_number": "HB23XXEUXXXXXX",
    "production_date": "11.04.2023",
    "description": "AquaClean Mera Comfort",
    "initial_operation_date": "31.05.2024"
  },
  "message": "Command info completed"
}
```

### Device commands

| Command | Description |
|---------|-------------|
| `toggle-lid` | Toggle lid open/closed |
| `toggle-anal` | Toggle anal shower on/off |

```bash
python main.py --mode cli --command toggle-lid
```
```json
{
  "status": "success",
  "command": "toggle-lid",
  "device": "AquaClean Mera Comfort",
  "serial_number": "HB23XXEUXXXXXX",
  "data": { "action": "lid_toggled" },
  "message": "Command toggle-lid completed"
}
```

---

## Commands that do NOT require a BLE connection

### `get-config`

Returns the current settings from `config.ini`.

```bash
python main.py --mode cli --command get-config
```
```json
{
  "status": "success",
  "command": "get-config",
  "data": {
    "ble_connection": "persistent",
    "poll_interval": 10.5,
    "mqtt_enabled": true,
    "device_id": "38:AB:XX:XX:ZZ:67",
    "api_host": "0.0.0.0",
    "api_port": 8080
  },
  "message": "Config read from config.ini"
}
```

### `publish-ha-discovery`

Publishes Home Assistant MQTT discovery messages so HA automatically creates all entities.  Reads broker connection settings from `config.ini` — no BLE needed.

> **Safe to run while the service is active.** `publish-ha-discovery` (and `remove-ha-discovery`) open their own MQTT connection with a randomly generated client ID, so they do not interfere with a running `--mode api` or `--mode service` instance.

```bash
python main.py --mode cli --command publish-ha-discovery
```
```json
{
  "status": "success",
  "command": "publish-ha-discovery",
  "data": {
    "topic_prefix": "Geberit/AquaClean",
    "broker": "192.168.0.xxx:1883",
    "published": [
      "User Sitting", "Anal Shower Running", "Lady Shower Running", "Dryer Running",
      "SAP Number", "Serial Number", "Production Date", "Description",
      "Initial Operation Date", "Connected", "Error",
      "Days Until Next Descale", "Days Until Shower Restricted",
      "Shower Cycles Until Confirmation", "Number of Descale Cycles",
      "Last Descale", "Unposted Shower Cycles",
      "Toggle Lid", "Toggle Anal Shower"
    ],
    "failed": []
  },
  "message": "Published 19 HA discovery entities to 192.168.0.xxx:1883"
}
```

After running this, go to **Home Assistant → Settings → Devices & Services → MQTT** and you will see the **Geberit AquaClean** device with all entities grouped together.

### `remove-ha-discovery`

Removes all Geberit AquaClean entities from Home Assistant by publishing empty payloads to the discovery topics.  Only affects entities created by `publish-ha-discovery` — other MQTT entities are untouched.  Also safe to run while the service is active (see note above).

```bash
python main.py --mode cli --command remove-ha-discovery
```

### `system-info`

Returns a snapshot of the runtime environment: app version, Python version, OS, libraries, BLE adapter details, and configuration values.  Useful for diagnosing customer installs without a BLE connection.

```bash
aquaclean-bridge --mode cli --command system-info
```
```json
{
  "status": "success",
  "command": "system-info",
  "message": "System info collected",
  "data": {
    "app_version": "2.4.21",
    "python_version": "3.11.2",
    "os": "Linux 6.1.64-v8+",
    "os_pretty_name": "Kali GNU/Linux Rolling",
    "os_version": "2025.4",
    "machine": "aarch64",
    "environment": "standalone",
    "docker": false,
    "config": {
      "ble_connection": "on-demand",
      "esphome_api_connection": "on-demand",
      "poll_interval": 30.0,
      "device_id": "38:AB:XX:XX:ZZ:67",
      "esphome_host": "192.168.0.xxx",
      "log_level": "INFO"
    },
    "libraries": {
      "bleak": "2.0.0",
      "aioesphomeapi": "24.6.2",
      "fastapi": "0.115.5",
      "uvicorn": "0.32.0",
      "paho-mqtt": "2.1.0",
      "aiorun": "2024.5.1"
    },
    "bluetooth": {
      "bluez_version": "5.82",
      "adapter": "hci0",
      "adapter_bus": "UART",
      "adapter_address": "DC:A6:32:XX:XX:XX",
      "adapter_manufacturer": "Cypress Semiconductor",
      "firmware_version": "BCM4345C0 0190"
    }
  }
}
```

The same data is also available via `GET /info/system` in `--mode api` and is published to MQTT on startup at `{topic}/centralDevice/systemInfo`.

### `performance-stats`

Returns in-memory timing statistics accumulated since the bridge started.  Data is only meaningful when called against a running `--mode api` service via the REST API (`GET /info/performance`).  The CLI version always returns empty stats since no polls occur in one-shot CLI mode.

Supports `--format markdown` for a human-readable table.

```bash
aquaclean-bridge --mode cli --command performance-stats --format markdown
```

The same data is available via `GET /info/performance` (add `?format=markdown` for plain text) and is published to MQTT after every poll at `{topic}/centralDevice/performanceStats`.

---

## Error output

Errors are also returned as JSON to stdout:

```json
{
  "status": "error",
  "command": "invalid",
  "message": "Argument Error: argument --command: invalid choice: 'foo' ...",
  "data": {}
}
```

This makes it straightforward to check `result["status"] == "success"` in scripts.
