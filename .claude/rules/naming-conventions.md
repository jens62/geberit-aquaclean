# Naming Conventions (MANDATORY)

Consistent naming is a hard requirement across config, code, MQTT, REST, and webui.
**Always check existing names before introducing a new one.**

---

## Toggle values

All two-state config/runtime options use `persistent` | `on-demand` as values
(not `true`/`false`, not `enabled`/`disabled`):
- `ble_connection = persistent | on-demand` (`[SERVICE]`)
- `esphome_api_connection = persistent | on-demand` (`[ESPHOME]`)

---

## Config keys

- `[SERVICE] ble_connection`
- `[ESPHOME] esphome_api_connection`

---

## REST endpoints

- `POST /config/ble-connection`         body: `{"value": "persistent"|"on-demand"}`
- `POST /config/esphome-api-connection` body: `{"value": "persistent"|"on-demand"}`
- `POST /config/poll-interval`          body: `{"value": <float>}`

---

## MQTT topics (inbound config)

- `<topic>/centralDevice/config/bleConnection`
- `<topic>/centralDevice/config/pollInterval`
- `<topic>/esphomeProxy/config/apiConnection`

---

## Webui button labels — `PREFIX: Switch to <OTHER>` pattern

- BLE connection toggle: `BLE: Switch to On-Demand` / `BLE: Switch to Persistent`
- ESP32 API connection toggle: `ESP32: Switch to On-Demand` / `ESP32: Switch to Persistent`
- Other buttons: `BLE: Reconnect` / `BLE: Disconnect`, `ESP32: Connect` / `ESP32: Disconnect`

---

## Python identifiers

- Module-level: `esphome_api_connection` (string `"persistent"` | `"on-demand"`)
- `ApiMode` instance: `self.esphome_api_connection`
- `device_state` key: `"esphome_api_connection"`
- Runtime toggle method: `set_esphome_api_connection(value: str)`
- MQTT event: `SetEsphomeApiConnection`

---

## MQTT ↔ HA Discovery dependency (MANDATORY)

**Any change to an outbound MQTT topic requires a matching update in two places:**

1. `get_ha_discovery_configs()` in `main.py` — the auto-discovery path
   (`--command publish-ha-discovery` / `--command remove-ha-discovery`)
2. `homeassistant/configuration_mqtt.yaml` — the manual config alternative

Both must stay in sync with every `send_data_async(topic, ...)` call.

Similarly, adding a new MQTT-published feature should also be reflected in:
- `homeassistant/dashboard_button_card.yaml`
- `homeassistant/dashboard_simple_card.yaml`
