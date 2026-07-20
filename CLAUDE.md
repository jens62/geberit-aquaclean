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

## Rules — always loaded

@.claude/rules/naming-conventions.md — MANDATORY: config, REST, MQTT, webui labels, Python identifiers, MQTT↔HA sync
@.claude/rules/release-process.md — Release checklist, HACS release, update.sh curl, tools/ curl
@.claude/rules/debugging-traps.md — Traps 1–15 + known open bugs (read first when debugging)
@.claude/rules/cross-component-parity.md — MANDATORY: Mera↔Alba mock parity, bridge↔mock-service parity; postponed sync = tracked REQ, never a silent gap

## Rules — read on demand (do not auto-load; read before the relevant task)

| File | Read when… |
|------|------------|
| `.claude/rules/ble-connection-modes.md` | Touching bridge modes, polling, ESPHome API, MQTT reconnect |
| `.claude/rules/device-state.md` | Touching `device_state`, `_set_ble_status`, GATT callbacks |
| `.claude/rules/error-codes.md` | Adding or changing error codes, hint propagation |
| `.claude/rules/circuit-breaker.md` | Touching circuit breaker or ESP32 auto-restart |
| `.claude/rules/ble-recovery.md` | Touching `wait_for_device_restart` |
| `.claude/rules/ble-protocol.md` | Any BLE protocol work: procedures, SPL, Commands, ProfileSettings |
| `docs/roadmap.md` | Planning new features or checking open TODOs |
| `.claude/rules/hacs-roadmap.md` | Planning HACS features, zeroconf, config flow |
| `.claude/rules/historical-notes.md` | Background reference only |
| `.claude/rules/requirements-documents.md` | Creating or editing any requirements definition document (`*-requirements.md` under `docs/developer/`) |
