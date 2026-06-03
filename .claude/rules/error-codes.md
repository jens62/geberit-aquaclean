# Error Code System

## `ErrorCodes.py`

All application errors are defined as `ErrorCode` NamedTuples:

```python
class ErrorCode(NamedTuple):
    code: str       # "E0003"
    message: str    # short description
    category: str   # "BLE" | "ESP32" | "RECOVERY" | "COMMAND" | "API" | "MQTT" | "CONFIG" | "SYSTEM"
    severity: str   # "INFO" | "WARNING" | "ERROR" | "CRITICAL"
    hint: str = ""  # user-facing resolution instructions
    doc_url: str = ""  # reserved — populate per-code when docs pages exist
```

`doc_url` is intentionally empty on every code. When documentation pages are
written, populate each code's `doc_url` here — no structural changes needed.
`ErrorManager.to_json()` / `to_dict()` include `doc_url` only when non-empty.

---

## Hint propagation

**BLE errors** (`_set_ble_status("error", error_hint=X.hint)`):
- Stored in `device_state["ble_error_hint"]`
- Broadcast via SSE; webapp shows it below the error in the BLE status widget
- Cleared automatically by `_set_ble_status("connecting" | "connected" | "disconnected")`

**ESP32 proxy errors** (`_update_esphome_proxy_state(error_hint=X.hint)`):
- Stored in `esphome_proxy_state["error_hint"]`
- Broadcast as `esphome_proxy_error_hint` in SSE; webapp appends it to the error text

**MQTT**: `ErrorManager.to_json(E_XXX)` always includes `"hint"` in the payload.

### `_publish_esphome_proxy_status` — temp `ErrorCode` pattern

This method re-publishes stored `esphome_proxy_state` to MQTT. Because the state
stores raw strings (not the original `ErrorCode`), it reconstructs a temporary
`ErrorCode` to format the JSON:

```python
temp_error = ErrorCode(error_code, error_msg, "ESP32", "ERROR", error_hint)
ErrorManager.to_json(temp_error)
```

The hint survives this round-trip because `error_hint` is stored in
`esphome_proxy_state["error_hint"]` alongside the code and message strings.

---

## CLI config validation

`--mode cli --command check-config` — validates config, returns JSON
with `{"status": "success"|"error", "data": {"errors": [...]}}`.

`_check_config_errors()` validates:
- `[BLE] device_id` (MAC format)
- `[SERVICE] ble_connection` and `[ESPHOME] esphome_api_connection` (enum)
- `[ESPHOME/API] port` (integer)
- `[POLL] interval` (float)
- `[LOGGING/ESPHOME] log_level` (known level)
