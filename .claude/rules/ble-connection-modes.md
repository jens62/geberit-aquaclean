# BLE Connection Modes

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

## ESPHome API connection mode (`esphome_api_connection`)

Relevant only when `[ESPHOME] host` is configured (ESP32 proxy in use).

> **Note:** The `persistent` ESP32 API connection mode was removed after proving
> unstable in production. Only `on-demand` remains in the current codebase.
>
> **`esphome-persistent-api` tag:** marks the last commit before the persistent TCP
> code was abandoned — **not** a working version.
>
> **"Only one API subscription is allowed at a time"** — ESP32 firmware limit: only
> one API client may hold an active BLE advertisement subscription at a time across
> ALL TCP connections to that ESP32. Two known causes:
> 1. **Log streaming + ESPHome proxy**: `_start_esphome_log_streaming()` opens a
>    second TCP connection. Fix: log streaming is disabled when ESPHome proxy is active.
>    See debugging trap 12.
> 2. **SIGTERM mid-scan**: subscription not released; ESP32 holds it until ping timeout
>    (~60–90 s). See debugging trap 10.

### `on-demand` (only mode that remains)

A fresh `BluetoothLeConnector` is created per on-demand BLE request.
Each request opens a new TCP connection to the ESP32, fetches `device_info`,
scans for the Geberit MAC, connects BLE, does the work, unsubscribes from
advertisements, disconnects BLE, and closes the TCP connection.

### `persistent` (cached connector)

One `BluetoothLeConnector` and one `AquaCleanClient` are created once and cached
as `ApiMode._esphome_connector` and `ApiMode._esphome_client`. The ESP32 API TCP
connection stays alive between BLE cycles via `disconnect_ble_only()`.

**Critical**: `_esphome_client` must be created alongside `_esphome_connector` (in
`_get_esphome_connector()`) and reused — never re-created per poll. Creating a new
`AquaCleanClient` per poll causes `data_received_handlers` to accumulate on the
shared connector (see debugging trap 8).

---

## MQTT reconnect

`MqttService.on_disconnect` calls `self.reconnect()` via `asyncio.run_coroutine_threadsafe`.
`reconnect()` calls `self.mqttc.reconnect()` and logs the result.

**Latent bug (now fixed)**: previously `on_disconnect` called `asyncio.create_task(self.reconnect())` — but `reconnect()` was not defined, causing a silent `AttributeError`. paho's own network thread was reconnecting anyway (via `loop_start()`), masking the bug.

Guard: only fires if `self.aquaclean_loop` is set and running.
