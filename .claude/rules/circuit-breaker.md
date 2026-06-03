# On-Demand Polling: Circuit Breaker & ESP32 Auto-Restart

## Circuit breaker

`ApiMode._polling_loop` tracks `_consecutive_poll_failures` (local var).

- Incremented in every except block (all error types).
- Reset to `0` on first success. When reset from a non-zero value, `_identification_fetched`
  is also reset so the next BLE session re-fetches identification (device may have been power-cycled).
- **Threshold = 3**: at exactly 3 consecutive failures, logs "Circuit open" and triggers
  `_trigger_esphome_restart()`.
- **Probe interval = 60s**: when circuit is open (`failures >= 3`), an extra
  `asyncio.sleep(60)` runs before each attempt, on top of the normal `_poll_interval` sleep.
- On recovery: logs "Poll recovered after N failures".

Constants at the top of `_polling_loop`:
```python
_CIRCUIT_OPEN_THRESHOLD = 3    # failures before circuit opens (3 × 10s scan = 30s max lag)
_CIRCUIT_OPEN_SLEEP     = 60
```

**BLE scan timeout: 10s** (`asyncio.wait_for(found_event.wait(), timeout=10.0)` in
`BluetoothLeConnector._connect_via_esphome()`). The Geberit appears in 110–314 ms
when the ESP32 scanner is healthy; 10s is generous without blowing up recovery time.
Not exposed in config.ini — internal engineering constant, not a user knob.
With threshold=3 and timeout=10s: **30 seconds maximum lag before ESP32 auto-restart**.

**Note on exception routing**: `_on_demand_inner()` converts all exceptions to
`HTTPException(503, ...)` at line ~1671. The specific `except ESPHomeDeviceNotFoundError`
/ `except ESPHomeConnectionError` handlers in `_polling_loop` are therefore dead code
— every error arrives as `Exception` and is caught by the generic handler. The counter
is incremented correctly regardless.

---

## ESP32 auto-restart (`_trigger_esphome_restart`) — IMPLEMENTED

**Status: done** on `feature/esphome-auto-restart` (commit 7cf2d97, refined in 916b39b).

The ESP32's `bluetooth_proxy` component can get stuck after a BLE disconnect, stopping
advertisement forwarding while the API TCP connection stays alive. `api: reboot_timeout:`
in ESPHome does **not** help — it only watches the API connection, not the BLE scanner.

**How it works:**
1. `button: platform: restart` added to both ESPHome YAML files (commit 05ab035).
   Flash the ESP32 once; the button then appears as `ButtonInfo` via `list_entities_services`.
2. `ApiMode._polling_loop` tracks `_consecutive_poll_failures`.
3. At exactly `_CIRCUIT_OPEN_THRESHOLD = 3` failures, `_trigger_esphome_restart()` is called.
4. `_trigger_esphome_restart()` opens a **fresh** APIClient (never touches the BLE path),
   discovers the restart button via `list_entities_services`, presses it via `button_command`.
5. After restart, `_CIRCUIT_OPEN_SLEEP = 60s` gives the ESP32 time to reboot.

**Requirements:** the ESPHome proxy YAML must contain:
```yaml
button:
  - platform: restart
    name: "Restart AquaClean Proxy"
```
If the button is absent, `_trigger_esphome_restart()` logs a warning and returns False —
the circuit breaker still slows down polling but cannot auto-recover without the button.

---

## Confirmed production behavior

**Incident 1 (2026-02-23):** `local-assets/aquaclean.log.1-part.txt`

| Time | Event |
|------|-------|
| 2026-02-22 20:17 | Bridge started, polling normally |
| 2026-02-23 04:22 | First E0002 — ESP32 BLE scanner stuck |
| 04:22 → 08:00 | 134 consecutive E0002 failures; circuit breaker at 60 s probe interval |
| 08:01:39 | `aioesphomeapi: Connection reset by peer` — ESP32 rebooted |
| 08:02:05 | `"Poll recovered after 134 consecutive failure(s)"` — **automatic, no restart** |

**Incident 2 (2026-02-24):** `local-assets/aquaclean_2026-02-23-part.log`

| Time | Event |
|------|-------|
| 07:15:57 | Successful poll; BLE disconnects, TCP kept alive |
| 07:16:05 | Next poll — scanner subscribed, no advertisements (scanner stuck) |
| 07:17:05 | failure #1 |
| 07:17:45 | failure #2 |
| 07:18:26 | failure #3 |
| 07:18:36 | User pressed "Restart AquaClean Proxy" on ESP32 web UI → TCP drops |
| 07:18:41 | `"Poll recovered after 3 consecutive failure(s)"` |

**Finding**: ESP32 `bluetooth_proxy` can get stuck after a BLE disconnect. With
threshold=3 and timeout=10s, auto-restart fires at 30s.
