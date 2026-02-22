# Home Assistant Integration

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

### 2. Publish MQTT Discovery

Run once to register all entities automatically in Home Assistant.  This command is safe to run while the service is already active in `--mode api` or `--mode service` — it uses a separate MQTT connection and requires no BLE.

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

### 3. Verify in Home Assistant

Go to **Settings → Devices & Services → MQTT** — you will see a **Geberit AquaClean** device with 13 entities grouped together:

| Type | Entities |
|------|---------|
| Binary sensor | User Sitting, Anal Shower Running, Lady Shower Running, Dryer Running |
| Sensor | SAP Number, Serial Number, Production Date, Description, Initial Operation Date, Connected, Error |
| Switch | Toggle Lid, Toggle Anal Shower |

### 4. Start the service

```bash
python main.py --mode service
```

Or in api mode (adds REST API and web UI):

```bash
python main.py --mode api
```

---

## Removing entities

To remove all Geberit AquaClean entities from Home Assistant:

```bash
python main.py --mode cli --command remove-ha-discovery
```

This publishes empty retained payloads to all discovery topics.  Only entities created by `publish-ha-discovery` are affected.

---

## Keeping discovery in sync

The discovery configuration is defined alongside the MQTT publish calls in `main.py`.  When a new `send_data_async()` call is added, a matching entity is added to `get_ha_discovery_configs()` in the same file.  Re-run `publish-ha-discovery` after any such change to update Home Assistant.

---

## Manual configuration (alternative)

If you prefer YAML over auto-discovery, see `homeassistant/configuration_mqtt.yaml` for a complete entity configuration.  All MQTT topics are documented in [mqtt.md](mqtt.md).
