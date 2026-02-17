# CLI Reference

The CLI mode runs a single command and exits.  Results are always written to **stdout as JSON**; log output goes to **stderr**.

```bash
python main.py --mode cli --command <command> [--address <ble-mac>]
```

`--address` overrides the BLE device address from `config.ini` for commands that need BLE.

Redirect stderr to suppress log noise:

```bash
python main.py --mode cli --command user-sitting-state 2>aquaclean.log
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
      "Toggle Lid", "Toggle Anal Shower"
    ],
    "failed": []
  },
  "message": "Published 13 HA discovery entities to 192.168.0.xxx:1883"
}
```

After running this, go to **Home Assistant → Settings → Devices & Services → MQTT** and you will see the **Geberit AquaClean** device with all entities grouped together.

### `remove-ha-discovery`

Removes all Geberit AquaClean entities from Home Assistant by publishing empty payloads to the discovery topics.  Only affects entities created by `publish-ha-discovery` — other MQTT entities are untouched.

```bash
python main.py --mode cli --command remove-ha-discovery
```

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
