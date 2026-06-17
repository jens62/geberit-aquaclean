# Geberit AquaClean — Developer Context for Codex

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

@.Codex/rules/naming-conventions.md — MANDATORY: config, REST, MQTT, webui labels, Python identifiers, MQTT↔HA sync
@.Codex/rules/release-process.md — Release checklist, HACS release, update.sh curl, tools/ curl
@.Codex/rules/debugging-traps.md — Traps 1–15 + known open bugs (read first when debugging)

## Rules — read on demand (do not auto-load; read before the relevant task)

| File | Read when… |
|------|------------|
| `.Codex/rules/ble-connection-modes.md` | Touching bridge modes, polling, ESPHome API, MQTT reconnect |
| `.Codex/rules/device-state.md` | Touching `device_state`, `_set_ble_status`, GATT callbacks |
| `.Codex/rules/error-codes.md` | Adding or changing error codes, hint propagation |
| `.Codex/rules/circuit-breaker.md` | Touching circuit breaker or ESP32 auto-restart |
| `.Codex/rules/ble-recovery.md` | Touching `wait_for_device_restart` |
| `.Codex/rules/ble-protocol.md` | Any BLE protocol work: procedures, SPL, Commands, ProfileSettings |
| `docs/roadmap.md` | Planning new features or checking open TODOs |
| `.Codex/rules/hacs-roadmap.md` | Planning HACS features, zeroconf, config flow |
| `.Codex/rules/historical-notes.md` | Background reference only |
