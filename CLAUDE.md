# Geberit AquaClean — Developer Context for Claude

Python bridge: Geberit AquaClean toilet (BLE) ↔ MQTT / SSE / REST API / web UI.
Entry point: `aquaclean_console_app/main.py`

---

## Key files

| File | Role |
|---|---|
| `main.py` | Config, `ServiceMode`, `ApiMode`, REST wiring, startup |
| `__main__.py` | Entry point for `aquaclean-bridge` command — **has its own argparse parser** |
| `RestApiService.py` | FastAPI routes + SSE broadcast queue |
| `MqttService.py` | paho-mqtt client; fires asyncio events into the main loop |
| `bluetooth_le/LE/BluetoothLeConnector.py` | BLE connector; ESPHome proxy path |
| `bluetooth_le/LE/ESPHomeAPIClient.py` | aioesphomeapi wrapper; owns notify callbacks |
| `aquaclean_core/Clients/AquaCleanClient.py` | High-level Geberit API; `start_polling()` |
| `ErrorCodes.py` | All error codes as `ErrorCode` NamedTuples; `ErrorManager` formatters |

**MANDATORY — Two parsers, keep in sync:** `main.py` (`if __name__ == "__main__":`) and
`__main__.py` (`entry_point()`) each define a full `JsonArgumentParser`. Mirror every
change (new `--command` choice, new `add_argument`, epilog) in **both** files.
Updating only one silently breaks the other.

---

## Config sections

```ini
[SERVICE]  ble_connection = persistent | on-demand
[POLL]     interval = float (seconds)
[ESPHOME]  host, port, noise_psk
[BLE]      device_id = BLE MAC address
[MQTT]     server, port, topic, username, password
[API]      host, port
```

---

## Python path (MANDATORY)

Always use `/Users/jens/venv/bin/python` — never `python3` or `python`.

---

## Communication style

When writing markdown with `[]()` links or `![]()` image embeds for terminal output,
wrap the entire response in a fenced code block — square brackets are interpreted by zsh.

---

## Rules — read the relevant file before every task

@.claude/rules/ble-connection-modes.md — BLE modes, polling, ESPHome API, MQTT reconnect
@.claude/rules/device-state.md — device_state fields, `_set_ble_status` semantics, identification, GATT callbacks
@.claude/rules/error-codes.md — ErrorCode system, hint propagation, CLI check-config
@.claude/rules/circuit-breaker.md — On-demand circuit breaker, ESP32 auto-restart (implemented)
@.claude/rules/ble-recovery.md — `wait_for_device_restart` protocol
@.claude/rules/debugging-traps.md — Traps 1–15 + known open bugs (read first when debugging)
@.claude/rules/ble-protocol.md — Protocol layers, Commands, ProfileSettings, SPL params, procedure codes
@.claude/rules/naming-conventions.md — MANDATORY: config, REST, MQTT, webui labels, Python identifiers, MQTT↔HA sync
@.claude/rules/release-process.md — Release checklist, HACS release, update.sh curl, tools/ curl
@.claude/rules/roadmap-todo.md — All open TODO items and implementation notes
@.claude/rules/hacs-roadmap.md — Planned HACS integration, zeroconf, Option A/B, dynamic UUIDs
@.claude/rules/historical-notes.md — Feature summary, haggis removal, ESPHome probe results
