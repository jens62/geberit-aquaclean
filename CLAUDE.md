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
| `ErrorCodes.py` | All error codes as `ErrorCode` NamedTuples; `ErrorManager` formatters |
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

## ESPHome API connection mode

> **Note:** The `persistent` ESP32 API connection mode was removed after proving
> unstable in production. Only `on-demand` remains in the current codebase.
>
> **`esphome-persistent-api` tag:** marks the last commit before the persistent TCP
> code was abandoned — **not** a working version. The tag was created to preserve
> that state. The code at that tag had trap 7 fixes applied but the overall
> persistent TCP implementation was still failing; the exact remaining failure was
> never pinpointed before the approach was reverted.
>
> **"Only one API subscription is allowed at a time"** — this is an ESP32 firmware
> limit: only one API client may hold an active BLE advertisement subscription at a
> time. It is NOT a TCP connection limit. It only occurs when two API clients compete
> (e.g. the main app and a probe script running simultaneously). In production the
> main app is the only client, so this limit is never hit.
>
> **Persistent TCP IS achievable** — proven by `esphome-aioesphomeapi-probe-v4.py`:
> all 3 cycles succeeded with TCP reused (0 ms overhead on cycles 2+) when run in
> isolation (no competing API client). The right path is a clean re-implementation
> in the current `esphome-on-demand-stable` codebase, not testing the old broken tag.

## ESPHome API connection mode (`esphome_api_connection`)

Relevant only when `[ESPHOME] host` is configured (ESP32 proxy in use).

### `on-demand` (default)

A fresh `BluetoothLeConnector` is created per on-demand BLE request.
Each request opens a new TCP connection to the ESP32, fetches `device_info`,
scans for the Geberit MAC, connects BLE, does the work, unsubscribes from
advertisements, disconnects BLE, and closes the TCP connection.

### `persistent`

One `BluetoothLeConnector` and one `AquaCleanClient` are created once and cached
as `ApiMode._esphome_connector` and `ApiMode._esphome_client`. The ESP32 API TCP
connection stays alive between BLE cycles via `disconnect_ble_only()` (which tears
down BLE + unsub_adv but skips `api.disconnect()`). Each poll reuses both objects.

**Critical**: `_esphome_client` must be created alongside `_esphome_connector` (in
`_get_esphome_connector()`) and reused — never re-created per poll. Creating a new
`AquaCleanClient` per poll causes `data_received_handlers` to accumulate on the
shared connector (see debugging trap 8).

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
last_esphome_api_ms portion: ESP32 TCP connect (None=local BLE)
last_ble_ms         portion: BLE scan + handshake
last_poll_ms        duration of last GetSystemParameterList
sap_number / serial_number / production_date / description  (cached after first poll)
initial_operation_date
ble_error_hint       user-facing resolution hint or None (cleared on non-error transitions)
```

**`_set_ble_status()` semantics** (non-obvious):
- `"connecting"` → clears timing values (fresh values incoming); clears `ble_error_hint`
- `"disconnected"` → clears connection metadata only; **does NOT clear timing**
  (last-op timing stays visible in webapp until next connect); clears `ble_error_hint`
- `"error"` → clears everything including `poll_epoch`; **sets** `ble_error_hint`

---

## Identification data (on-demand mode)

In on-demand mode, `connect()` is never called (only `connect_ble_only()`), so
`DeviceIdentification` events never fire. Instead:
- The **first poll** calls `_fetch_state_and_info()` (state + identification in
  one BLE session), controlled by `_identification_fetched` flag.
- Results cached in `device_state` and broadcast via SSE.
- Subsequent REST calls to `/data/identification` etc. return cached data
  without a BLE connect.

**Cached path timing**: `get_identification()` and `get_initial_operation_date()`
include `_connect_ms=0`, `_esphome_api_ms=0`, `_ble_ms=0`, `_query_ms=0` even
when returning cached data. This ensures the webapp updates its timing display
instead of showing stale values from a previous BLE operation.

**`AquaCleanClient.soc_application_versions`**: must be initialised to `None` in
`__init__` (not only set in `connect()`). In on-demand mode `connect()` is never
called; persistent-mode REST path reads `client.soc_application_versions` directly.
`get_soc_versions()` reads the **data attribute** (`soc_application_versions`),
not the **event handler** (`SOCApplicationVersions`).

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

## BLE recovery protocol (`wait_for_device_restart`)

Triggered in `ServiceMode` when a `BLEPeripheralTimeoutError` is caught (i.e.
the Geberit dropped the BLE connection and did not come back within the poll
timeout). The protocol:

1. **Phase 1** — wait for the device to disappear from BLE scans (max 2 min).
   This confirms a power-cycle has happened.
2. **Phase 2** — wait for the device to reappear (max 2 min).
   Once seen, return so the outer loop reconnects.

Status messages go to `{topic}/centralDevice/connected`.
Error codes (E2001–E2004) go to `{topic}/centralDevice/error`.

### ESP32 path vs. local-bleak path

`wait_for_device_restart(device_id, bluetooth_connector)` dispatches:
- **ESP32 configured** → `_wait_for_device_restart_via_esphome()` — scans via the
  ESP32 proxy (no local BT adapter needed).
- **ESP32 not configured** → `_wait_for_device_restart_local()` — scans via local
  bleak adapter.

### Fallback from ESP32 to local BLE (by design, E2005)

If the ESP32 API connection cannot be established during recovery, the code
**deliberately falls back** to local bleak scanning. This is the correct
behavior when the ESP32 itself is unreachable.

The fallback:
- Tries to **reuse the persistent `_esphome_api`** from `bluetooth_connector`
  if it is still alive (avoids a redundant TCP handshake).
- Creates a fresh `APIClient` only if no live connection is available.
- If even the fresh connection fails, falls back to local BLE scanning and
  reports **E2005** (`Recovery: ESP32 proxy connection failed`) to:
  - `{topic}/centralDevice/error` (MQTT)
  - `{topic}/esphomeProxy/error` (MQTT, via `_update_esphome_proxy_state`)
  - SSE / webapp (via `_set_ble_status("error", error_code=E2005.code)`)
- The local bleak path requires a local BT adapter on the host.
- If the ESP32 connection is reused or a fresh connection succeeds, the
  `finally` block skips `api.disconnect()` (`own_api=False`) to avoid
  tearing down the persistent connection.

---

## Error code system (`ErrorCodes.py`)

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

### Hint propagation

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

**CLI**: `--mode cli --command check-config` — validates config, returns JSON
with `{"status": "success"|"error", "data": {"errors": [...]}}`.
`_check_config_errors()` exists (module-level) but currently returns no errors.

---

## On-demand polling: circuit breaker

`ApiMode._polling_loop` tracks `_consecutive_poll_failures` (local var).

- Incremented in every except block (all error types).
- Reset to `0` on first success. When reset from a non-zero value, `_identification_fetched` is also reset so the next BLE session re-fetches identification (device may have been power-cycled).
- **Threshold = 5**: at exactly 5 consecutive failures, logs "Circuit open" once and switches to probe mode.
- **Probe interval = 60s**: when circuit is open (`failures >= 5`), an extra `asyncio.sleep(60)` runs before each attempt, on top of the normal `_poll_interval` sleep.
- On recovery: logs "Poll recovered after N failures".

Constants are named locals at the top of `_polling_loop`:
```python
_CIRCUIT_OPEN_THRESHOLD = 5
_CIRCUIT_OPEN_SLEEP     = 60
```

**Why `_identification_fetched` is reset on recovery**: if the device was power-cycled during the outage its identification data is unchanged in practice, but resetting ensures a clean re-fetch rather than serving potentially stale cached values.

---

## MQTT reconnect

`MqttService.on_disconnect` calls `self.reconnect()` via `asyncio.run_coroutine_threadsafe`.
`reconnect()` calls `self.mqttc.reconnect()` and logs the result.

**Latent bug (now fixed)**: previously `on_disconnect` called `asyncio.create_task(self.reconnect())` — but `reconnect()` was not defined, causing a silent `AttributeError` in the task. paho's own network thread was reconnecting anyway (via `loop_start()`), so MQTT kept working, masking the bug.

Pattern matches all other MQTT callbacks: `run_coroutine_threadsafe(coro, self.aquaclean_loop)`.
Guard: only fires if `self.aquaclean_loop` is set and running (disconnect before `start_async` completes is safe).

---

## TODO

- **Log error codes to the Python log file.** When an exception is mapped to an
  error code in `_on_demand_inner`'s finally block, only MQTT and SSE receive the
  code (e.g. E7002). The Python log file only shows the raw message from
  `AquaCleanBaseClient` (`logger.error("No response from BLE peripheral ...")`
  at line 471) with no error code. Add a `logger.error(f"BLE error {ec.code} — {e}")`
  (or similar) at the point of mapping in `main.py` so the log file is
  self-contained and error codes can be correlated without cross-referencing MQTT.

---

## ESPHome BLE connection — probe results (2026-02-21)

All 4 parameter combinations tested against ESPHome 2026.1.5 from Mac (192.168.0.87):

| has_cache | address_type | Protocol               | Result  |
|-----------|--------------|------------------------|---------|
| False     | 0 PUBLIC     | CONNECT_V3_WITHOUT_CACHE | OK MTU=23 |
| True      | 0 PUBLIC     | CONNECT_V3_WITH_CACHE    | OK MTU=23 |
| False     | 1 RANDOM     | CONNECT_V3_WITHOUT_CACHE | OK MTU=23 |
| True      | 1 RANDOM     | CONNECT_V3_WITH_CACHE    | OK MTU=23 |

**The connection parameters in `ESPHomeAPIClient.py` are correct.**
Current settings (`has_cache=False, address_type=0, feature_flags=<device actual>`) work.

**aioesphomeapi source (client.py) confirms only two code paths:**
- `has_cache=True` → `CONNECT_V3_WITH_CACHE`
- `has_cache=False` + REMOTE_CACHING bit set → `CONNECT_V3_WITHOUT_CACHE`
- Old CONNECT method is fully removed; `feature_flags=0` raises `ValueError`.

**The actual bug — `UnsubscribeBluetoothLEAdvertisementsRequest` while BLE is active:**
`unsub_adv()` is synchronous: it only QUEUES the frame in aioesphomeapi's internal send
buffer. The frame is flushed at the next `await` (e.g. inside `_post_connect()`). So calling
`unsub_adv()` at ANY point while the BLE connection is active will cause the ESP32 to
disconnect the BLE client. The symptom depends on timing:
- Before `bluetooth_device_connect()` send: "Disconnect before connected" (reason 0x16)
- After BLE is connected but during notify setup: immediate disconnect → notify timeout

**Fix:** Never call `unsub_adv()` while BLE is active. Store it as `self._esphome_unsub_adv`
and call it in `disconnect()` AFTER `await self.client.disconnect()` tears down the BLE link.

**Fix verified — Kali production run 2026-02-21 18:01:**
- All BLE connects succeed: `BLE connection successful with address_type=0`
- All disconnects clean: ESP32 reports `Close, reason=0x00, freeing slot`
- Full data flow confirmed: `GetSystemParameterList`, `GetDeviceIdentification`,
  `GetDeviceInitialOperationDate`, `GetSOCApplicationVersions` all succeed each poll
- REST API serving correctly (`/data/soc-versions`, `/data/initial-operation-date`)
- MQTT publishing all topics; session ends with PINGREQ/PINGRESP — stable
- One pre-existing non-blocking INFO: `GetSOCApplicationVersions: Not yet fully implemented`

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

7. **ESP32 proxy shows stale "Cannot reach…" error hint after recovery**
   → `_update_esphome_proxy_state` only updates `error_hint` when explicitly passed.
   If a previous `ESPHomeConnectionError` stored E1002.hint, subsequent successful
   polls call `_update_esphome_proxy_state(error_code="E0000")` without `error_hint`,
   so the stale hint persists in `esphome_proxy_state` and the webapp keeps showing it.
   Fix: when `error_code="E0000"` is set, `error_hint` is auto-cleared to `""`.
   (Added to `_update_esphome_proxy_state` logic.)

9. **App permanently stuck returning E7002 "Not connected to aquaclean-proxy" after ~90 min**
   → aioesphomeapi has a built-in 90-second ping timeout. When the TCP link goes quiet
   (e.g. Geberit unreachable for 3 consecutive 30s scans = 90s), aioesphomeapi logs
   "Ping response not received after 90.0 seconds" and internally sets `api._connection = None`.
   `self._esphome_api` stays non-None on `BluetoothLeConnector`, so
   `_ensure_esphome_api_connected()` keeps returning the dead client on every poll.
   Every call to `subscribe_bluetooth_le_raw_advertisements` then fails with
   "Not connected" → E7002 → circuit breaker opens → app probes every 60s forever.
   **Fix** (in `_ensure_esphome_api_connected`): before returning the cached client,
   check `getattr(self._esphome_api, '_connection', None) is not None`.
   If `None`, log a warning, clear `self._esphome_api = None` and
   `self.esphome_proxy_connected = False`, then fall through to reconnect.
   The fast path (healthy connection, `_connection` not None) is unaffected.

8. **Queries get slower with each poll in persistent ESPHome API mode**
   → `AquaCleanBaseClient.__init__` always does:
   `self.bluetooth_le_connector.data_received_handlers += self.frame_service.process_data`
   If `_on_demand_inner` creates a NEW `AquaCleanClientFactory(connector).create_client()`
   on every poll while reusing the same persistent connector, handlers accumulate.
   After N polls there are N handlers; each BLE GATT notification fires N times →
   N "receive complete" log lines per request, N-fold slowdown.
   **Fix**: in persistent mode, `_get_esphome_connector()` also creates one
   `AquaCleanClient` (stored as `self._esphome_client`) and `_on_demand_inner` reuses
   it. The client is reset to `None` alongside `_esphome_connector` whenever the
   persistent connection is torn down (e.g., switching to on-demand).
   **Do NOT call `AquaCleanClientFactory(connector).create_client()` on every poll
   when the connector is persistent.**

---

## Related repositories

| Repo | Local path | Purpose |
|---|---|---|
| `jens62/haggis-patched` | `/Users/jens/develop/haggis-patched` | Patched fork of `haggis` (logging utils); branch: `master` |

**haggis-patched patch**: `src/haggis/logs.py` — replaced `logging._prepareFork()` / `logging._afterFork()` (Python 3.12+ only) with `logging._acquireLock()` / `logging._releaseLock()` so the package works on Python 3.11 (Debian). Source: https://gitlab.com/madphysicist/haggis/-/issues/2#note_2355044561

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

## Feature summary (merged from `feature/persistent-esphome-api`)

Key additions vs. the original `main`:
- Split connect timing (ESP32 API ms vs BLE ms)
- Runtime toggle for `ble_connection` at runtime
- On-demand polling loop with `set_poll_interval` support (both modes)
- First-poll identification fetch + SSE caching
- `_poll_interval_event` in ServiceMode for persistent-mode interval changes
- `_on_poll_done()` resets connect timing to 0 (persistent BLE mode reuses connection)
- `_check_config_errors()` — startup config validation stub (currently empty)
- `--command check-config` CLI command — returns JSON
- Recovery fallback fixes: `wait_for_device_restart` now passes `bluetooth_connector`
  so the persistent `_esphome_api` is reused; MQTT topic bug fixed (was sending
  to `.../centralDevice/connected/centralDevice/error`); E2005 now surfaces via
  MQTT + webapp SSE; E2003/E2004 now published to correct error topic
- Error code hints: all `ErrorCode` definitions carry user-facing `hint` text;
  `doc_url` field reserved for future doc links; hints propagate through
  `_set_ble_status` / `_update_esphome_proxy_state` → `device_state` → SSE → webapp
- `soc_application_versions = None` initialised in `AquaCleanClient.__init__`;
  `get_soc_versions()` reads data attribute, not EventHandler
- Cached-path timing: `get_identification()` / `get_initial_operation_date()` include
  timing zeros when returning from cache so webapp doesn't show stale timing
- Circuit breaker in `_polling_loop`: after 5 consecutive failures switches to 60s
  probe interval; resets `_identification_fetched` on recovery
- MQTT `reconnect()` latent bug fixed: `on_disconnect` now uses `run_coroutine_threadsafe`
  and calls a defined `reconnect()` method on `MqttService`
- On-demand poll errors now surface to webapp via SSE (`_set_ble_status("error")` in
  `_on_demand_inner` finally block — DRY, covers all current and future error types)
- All connection button labels consistent: `PREFIX: Action` pattern throughout
- `esphome_proxy_error_hint` stale-hint fix: `_update_esphome_proxy_state` auto-clears
  `error_hint` when `error_code="E0000"` so a previous failure hint doesn't persist

---

## Naming conventions (MANDATORY — do not change without explicit instruction)

Consistent naming is a hard requirement across config, code, MQTT, REST, and webui.
**Always check existing names before introducing a new one.**

### Toggle values
All two-state config/runtime options use `persistent` | `on-demand` as values (not
`true`/`false`, not `enabled`/`disabled`):
- `ble_connection = persistent | on-demand` (`[SERVICE]`)
- `esphome_api_connection = persistent | on-demand` (`[ESPHOME]`)

### Config keys
- `[SERVICE] ble_connection`
- `[ESPHOME] esphome_api_connection`

### REST endpoints
- `POST /config/ble-connection`        body: `{"value": "persistent"|"on-demand"}`
- `POST /config/esphome-api-connection` body: `{"value": "persistent"|"on-demand"}`
- `POST /config/poll-interval`          body: `{"value": <float>}`

### MQTT topics (inbound config)
- `<topic>/centralDevice/config/bleConnection`
- `<topic>/centralDevice/config/pollInterval`
- `<topic>/esphomeProxy/config/apiConnection`

### Webui button labels — `PREFIX: Switch to <OTHER>` pattern
- BLE connection toggle: `BLE: Switch to On-Demand` / `BLE: Switch to Persistent`
- ESP32 API connection toggle: `ESP32: Switch to On-Demand` / `ESP32: Switch to Persistent`
- Other buttons: `BLE: Reconnect` / `BLE: Disconnect`, `ESP32: Connect` / `ESP32: Disconnect`

### Python identifiers
- Module-level: `esphome_api_connection` (string `"persistent"` | `"on-demand"`)
- `ApiMode` instance: `self.esphome_api_connection`
- `device_state` key: `"esphome_api_connection"`
- Runtime toggle method: `set_esphome_api_connection(value: str)`
- MQTT event: `SetEsphomeApiConnection`
