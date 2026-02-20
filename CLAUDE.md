# Geberit AquaClean — Developer Context for Claude

This file gives Claude the architectural facts needed to work on this codebase
efficiently. Update it whenever something non-obvious is changed.

---

## What the app does

Python bridge between a Geberit AquaClean toilet (BLE peripheral) and the rest
of a home-automation stack. It:
- Connects to the toilet over BLE (directly or via an ESP32 ESPHome proxy)
- Polls toilet state and publishes it to MQTT / SSE
- Exposes a REST API + web UI for control and status

Entry point: `aquaclean_console_app/main.py`

---

## Key files

| File | Role |
|---|---|
| `main.py` | Everything: config, `ServiceMode`, `ApiMode`, REST wiring, startup |
| `RestApiService.py` | FastAPI routes + SSE broadcast queue |
| `MqttService.py` | paho-mqtt client; fires asyncio events into the main loop |
| `bluetooth_le/LE/BluetoothLeConnector.py` | BLE connector; handles ESPHome proxy path |
| `bluetooth_le/LE/ESPHomeAPIClient.py` | aioesphomeapi wrapper; owns notify callbacks |
| `aquaclean_core/Clients/AquaCleanClient.py` | High-level Geberit API; `start_polling()` |
| `config.ini` | Runtime config (not committed with real values) |

---

## Two BLE connection modes (`ble_connection`)

This is the single most important architectural fact.

### `persistent` (default in config.ini)

- **Owner**: `ServiceMode.run()` — a recovery loop that keeps BLE connected.
- **Polling**: `AquaCleanClient.start_polling(interval)` called directly.
  `interval` is read from `device_state["poll_interval"]` on each reconnect.
- **Poll interval changes**: signalled via `ServiceMode._poll_interval_event`.
  The inner while loop inside `ServiceMode.run()` reacts without BLE disconnect.
- **`ApiMode._polling_loop`** skips entirely (`continue`) in this mode.

### `on-demand`

- **Owner**: `ApiMode._on_demand_inner()` — connect → action → disconnect per request.
- **Polling**: `ApiMode._polling_loop` background task; uses `ApiMode._poll_interval`.
- **Poll interval changes**: `set_poll_interval()` sets `ApiMode._poll_wakeup` event.
- **Serialization**: `ApiMode._on_demand_lock` — all BLE ops are serialized.
  **If this lock is held indefinitely, all REST calls hang forever.**
- **ServiceMode** is started but stuck waiting on `_connection_allowed`
  (cleared for on-demand); it never reaches the BLE connect code.

### Switching modes at runtime

`set_ble_connection("persistent")` → `service.request_reconnect()`
`set_ble_connection("on-demand")` → `service.request_disconnect()`

---

## Polling control: `set_poll_interval(value)`

Affects **both** modes:
- Sets `ApiMode._poll_interval` and `device_state["poll_interval"]`
- Sets `ApiMode._poll_wakeup` → wakes on-demand `_polling_loop`
- Sets `ServiceMode._poll_interval_event` → wakes persistent inner loop
- `value = 0` disables polling; `value > 0` re-enables

**Trap**: before the `_poll_interval_event` mechanism was added, changing the
interval in persistent mode had no effect (ServiceMode used a local config
variable). Now it reads `device_state["poll_interval"]` each reconnect.

---

## Two ESPHome API connection modes (`esphome_api_connection`)

Relevant only when `[ESPHOME] host` is configured (ESP32 proxy in use).

### `persistent` (recommended)

- `ApiMode._esphome_connector` (a `BluetoothLeConnector`) is kept alive.
- Per on-demand request: only BLE is connected/disconnected via `disconnect_ble_only()`.
- TCP to ESP32 is reused → much faster (~50 ms vs ~1 s per request).

### `on-demand`

- A fresh `BluetoothLeConnector` is created per request.
- Full TCP connect + `device_info` fetch every time.

---

## `device_state` — single source of truth

`ServiceMode.device_state` dict, broadcast to all SSE clients via
`rest_api.broadcast_state(device_state.copy())`.

Key fields:
```
ble_status          connecting | connected | disconnected | error
ble_connection      persistent | on-demand
poll_interval       float (seconds); 0 = disabled
poll_epoch          Unix timestamp of last poll start (for countdown)
last_connect_ms     total connect time in ms
last_esphome_api_ms portion: ESP32 TCP connect (None=local BLE, 0=reused)
last_ble_ms         portion: BLE scan + handshake
last_poll_ms        duration of last GetSystemParameterList
esphome_api_connection  persistent | on-demand
sap_number / serial_number / production_date / description  (cached after first poll)
initial_operation_date
```

**`_set_ble_status()` semantics** (non-obvious):
- `"connecting"` → clears timing values (fresh values incoming)
- `"disconnected"` → clears connection metadata only; **does NOT clear timing**
  (last-op timing stays visible in webapp until next connect)
- `"error"` → clears everything including `poll_epoch`

---

## Identification data (on-demand mode)

In on-demand mode, `connect()` is never called (only `connect_ble_only()`), so
`DeviceIdentification` events never fire. Instead:
- The **first poll** calls `_fetch_state_and_info()` (state + identification in
  one BLE session), controlled by `_identification_fetched` flag.
- Results cached in `device_state` and broadcast via SSE.
- Subsequent REST calls to `/data/identification` etc. return cached data
  without a BLE connect.

---

## ESPHomeAPIClient: GATT notify callbacks

`ESPHomeAPIClient` registers GATT notify callbacks via `aioesphomeapi`.
Each `start_notify()` call stores `(stop_notify_fn, remove_cb)` in `_notify_unsubs`.

**Critical**: `disconnect()` must call `remove_cb()` for every entry before
disconnecting. Otherwise each BLE reconnect adds another handler to
aioesphomeapi's dispatch table (never removed), causing frame duplication:
cycle N delivers N copies of every notification → O(N) slowdown.

This is now done in `ESPHomeAPIClient.disconnect()`.

---

## Common debugging traps

1. **Polling stops after a REST query (webapp hangs)**
   → `_on_demand_lock` is stuck. Usually caused by an unhandled exception
   inside `_on_demand()` that prevents `finally` from running, or a
   mid-frame second BLE connect via a concurrent `_fetch_info` call.

2. **`set_poll_interval(0)` has no effect**
   → Check `ble_connection`. In persistent mode, only `_poll_interval_event`
   stops polling. In on-demand mode, only `_poll_wakeup` does.

3. **MQTT retained message resets poll interval at startup**
   → MqttService subscribes to `centralDevice/config/pollInterval`.
   The broker may deliver a retained message triggering `set_poll_interval`.

4. **Timing values static in webapp after a REST query**
   → `_set_ble_status("disconnected")` must NOT clear timing keys.
   Only `"connecting"` should clear them.

5. **Duplicate GATT frames growing each BLE reconnect**
   → Missing `remove_cb()` in `ESPHomeAPIClient.disconnect()`.

6. **Identification not shown in webapp on first page load (on-demand)**
   → Identification arrives via SSE after the first poll, not via `/info`.
   `onStateReceived()` calls `onIdentification()` when SSE state has
   `sap_number != null`.

---

## Config sections

```ini
[SERVICE]  ble_connection = persistent | on-demand
[POLL]     interval = float (seconds)
[ESPHOME]  host, port, noise_psk, persistent_api = true|false
[BLE]      device_id = BLE MAC address
[MQTT]     server, port, topic, username, password
[API]      host, port
```

---

## Branch: `feature/persistent-esphome-api`

Adds on top of `main`:
- Persistent ESPHome API TCP connection (`persistent_api` config)
- Split connect timing (ESP32 API ms vs BLE ms)
- Runtime toggle for `ble_connection` and `esphome_api_connection`
- On-demand polling loop with `set_poll_interval` support (both modes)
- First-poll identification fetch + SSE caching
- `disconnect_ble_only()` — keeps ESP32 TCP alive, only drops BLE
- `_poll_interval_event` in ServiceMode for persistent-mode interval changes
- `_on_poll_done()` resets connect timing to 0 (persistent mode reuses connection)
- `_persistent_query()` helper — wraps persistent-mode BLE calls with timing metadata
  so `updateQueryTiming()` in the webapp receives `_connect_ms=0`, `_query_ms=N`
