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

### 2. Start the bridge

```bash
aquaclean-bridge --mode api
```

By default (`ha_discovery_on_startup = true` in `config.ini`), all Home Assistant MQTT discovery entities are published automatically on every startup — no manual step needed.  You will see in the log:

```
INFO  Published 19 HA discovery entities on startup
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

Go to **Settings → Devices & Services → MQTT** — you will see a **Geberit AquaClean** device with 19 entities grouped together:

| Type | Entities |
|------|---------|
| Binary sensor | User Sitting, Anal Shower Running, Lady Shower Running, Dryer Running |
| Sensor | SAP Number, Serial Number, Production Date, Description, Initial Operation Date, Connected, Error |
| Sensor (descale) | Days Until Next Descale, Days Until Shower Restricted, Shower Cycles Until Confirmation, Number of Descale Cycles, Last Descale, Unposted Shower Cycles |
| Switch | Toggle Lid, Toggle Anal Shower |

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

## Manual configuration (alternative)

If you prefer YAML over auto-discovery, see `homeassistant/configuration_mqtt.yaml` for a complete entity configuration.  All MQTT topics are documented in [mqtt.md](mqtt.md).
