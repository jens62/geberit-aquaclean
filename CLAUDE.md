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
| `__main__.py` | Entry point for `aquaclean-bridge` command; **has its own argparse parser** |
| `RestApiService.py` | FastAPI routes + SSE broadcast queue |
| `MqttService.py` | paho-mqtt client; fires asyncio events into the main loop |
| `bluetooth_le/LE/BluetoothLeConnector.py` | BLE connector; handles ESPHome proxy path |
| `bluetooth_le/LE/ESPHomeAPIClient.py` | aioesphomeapi wrapper; owns notify callbacks |
| `aquaclean_core/Clients/AquaCleanClient.py` | High-level Geberit API; `start_polling()` |
| `ErrorCodes.py` | All error codes as `ErrorCode` NamedTuples; `ErrorManager` formatters |
| `config.ini` | Runtime config (not committed with real values) |

**Two parsers — keep in sync (MANDATORY):**
`main.py` (`if __name__ == "__main__":`) and `__main__.py` (`entry_point()`) each define
a full `JsonArgumentParser`. Any change to either must be mirrored in the other:
- New `--command` choice → add to both `choices=[...]` lists
- New `add_argument(...)` flag → add to both parsers
- Epilog examples → keep consistent

`main.py`'s parser is used by `python main.py`; `__main__.py`'s parser is used by the
installed `aquaclean-bridge` command. Updating only one silently breaks the other.

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
> limit: only one API client may hold an active BLE advertisement subscription
> (`api_connection_` field) at a time across ALL TCP connections to that ESP32.
> Two known causes:
> 1. **Log streaming + ESPHome proxy**: `_start_esphome_log_streaming()` opens a
>    second TCP connection to the same ESP32. The ESP32 assigns `api_connection_`
>    to the first connection that subscribes (or connects, in newer ESPHome/aioesphomeapi
>    versions). The BLE connector's subscribe attempt on the second TCP connection is
>    then permanently rejected. **Fix: log streaming is disabled when ESPHome proxy
>    is active.** See debugging trap 12.
> 2. **SIGTERM mid-scan**: if the bridge is killed while waiting for a BLE
>    advertisement, the subscription is not released and the ESP32 holds it until
>    its ping timeout (~60–90 s). See debugging trap 10 for root cause and fix.
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
`_check_config_errors()` validates: `[BLE] device_id` (MAC format), `[SERVICE] ble_connection`
and `[ESPHOME] esphome_api_connection` (enum), `[ESPHOME/API] port` (integer), `[POLL] interval`
(float), `[LOGGING/ESPHOME] log_level` (known level).

---

## On-demand polling: circuit breaker

`ApiMode._polling_loop` tracks `_consecutive_poll_failures` (local var).

- Incremented in every except block (all error types).
- Reset to `0` on first success. When reset from a non-zero value, `_identification_fetched` is also reset so the next BLE session re-fetches identification (device may have been power-cycled).
- **Threshold = 3**: at exactly 3 consecutive failures, logs "Circuit open" and triggers `_trigger_esphome_restart()`.
- **Probe interval = 60s**: when circuit is open (`failures >= 3`), an extra `asyncio.sleep(60)` runs before each attempt, on top of the normal `_poll_interval` sleep.
- On recovery: logs "Poll recovered after N failures".

Constants are named locals at the top of `_polling_loop`:
```python
_CIRCUIT_OPEN_THRESHOLD = 3    # failures before circuit opens (3 × 10s scan = 30s max lag)
_CIRCUIT_OPEN_SLEEP     = 60
```

**BLE scan timeout: 10s** (`asyncio.wait_for(found_event.wait(), timeout=10.0)` in
`BluetoothLeConnector._connect_via_esphome()`).  The Geberit appears in 110–314 ms
when the ESP32 scanner is healthy; 10s is generous without blowing up recovery time.
Not exposed in config.ini — it's an internal engineering constant, not a user knob.
With threshold=3 and timeout=10s: **30 seconds maximum lag before ESP32 auto-restart**.

**Why `_identification_fetched` is reset on recovery**: if the device was power-cycled during the outage its identification data is unchanged in practice, but resetting ensures a clean re-fetch rather than serving potentially stale cached values.

**Note on exception routing**: `_on_demand_inner()` converts all exceptions to
`HTTPException(503, ...)` at line ~1671.  The specific `except ESPHomeDeviceNotFoundError`
/ `except ESPHomeConnectionError` handlers in `_polling_loop` are therefore dead code
— every error arrives as `Exception` and is caught by the generic handler.  The counter
is incremented correctly regardless; the specific handlers are harmless dead code.

**Confirmed production behavior (2026-02-23):** A real incident log (`local-assets/aquaclean.log.1-part.txt`) confirmed full auto-recovery with no bridge restart:

| Time | Event |
|------|-------|
| 2026-02-22 20:17 | Bridge started, polling normally |
| 2026-02-23 04:22 | First E0002 — ESP32 BLE scanner stuck |
| 04:22 → 08:00 | 134 consecutive E0002 failures; circuit breaker at 60 s probe interval |
| 08:01:39 | `aioesphomeapi: Connection reset by peer` — ESP32 rebooted after power-cycle |
| 08:02:02 | Trap 9 fix: `"ESP32 API connection lost (ping timeout?); clearing stale client and reconnecting"` |
| 08:02:05 | `"Poll recovered after 134 consecutive failure(s)"` — **automatic, no restart** |

The bridge does NOT need to be restarted after an ESP32 power-cycle. The circuit breaker + trap 9 dead-connection detection handle it automatically.

**Second confirmed incident (2026-02-24):** `local-assets/aquaclean_2026-02-23-part.log`
— ESP32 BLE scanner stuck after a BLE disconnect (within ~8 s of a successful poll),
**not** after a long overnight outage:

| Time | Event |
|------|-------|
| 07:15:57 | Successful poll; BLE disconnects, TCP kept alive (persistent API) |
| 07:16:05 | Next poll — scanner subscribed, **no advertisements received** (scanner stuck) |
| 07:16:35 | POST /command/toggle-lid → 503 (lock held by hanging 30s scan) |
| 07:17:05 | failure #1 (scan timed out) |
| 07:17:45 | failure #2 |
| 07:18:26 | failure #3 |
| 07:18:36 | User manually pressed "Restart AquaClean Proxy" on the ESP32 web UI → TCP drops |
| 07:18:37 | Bridge reconnects; BLE device found in 186 ms |
| 07:18:41 | `"Poll recovered after 3 consecutive failure(s)"` |

**Finding**: the ESP32 `bluetooth_proxy` component can get stuck after a BLE disconnect,
stopping advertisement forwarding even though the API TCP connection is still alive.
"Restart AquaClean Proxy" (component restart, not full reboot) is sufficient to recover.
With the old defaults (threshold=5, timeout=30s) the user had to wait 90s (3 failures)
before manually intervening — auto-restart would have fired at failure #5 (150s).
With the new defaults (threshold=3, timeout=10s) auto-restart fires at 30s.

---

## MQTT reconnect

`MqttService.on_disconnect` calls `self.reconnect()` via `asyncio.run_coroutine_threadsafe`.
`reconnect()` calls `self.mqttc.reconnect()` and logs the result.

**Latent bug (now fixed)**: previously `on_disconnect` called `asyncio.create_task(self.reconnect())` — but `reconnect()` was not defined, causing a silent `AttributeError` in the task. paho's own network thread was reconnecting anyway (via `loop_start()`), so MQTT kept working, masking the bug.

Pattern matches all other MQTT callbacks: `run_coroutine_threadsafe(coro, self.aquaclean_loop)`.
Guard: only fires if `self.aquaclean_loop` is set and running (disconnect before `start_async` completes is safe).

---

## Roadmap — next steps

### Wire remaining API-layer procedures from `tmp.txt`

Two procedures identified in the thomas-bingel C# repo are not yet implemented:

| Procedure | Call | Priority | Notes |
|-----------|------|----------|-------|
| `0x51` | `GetStoredCommonSetting(storedCommonSettingId)` → 2-byte int | High | Potentially bridges API layer to `BLE_COMMAND_REFERENCE.md` DpIds (water hardness, descaling intervals etc.); `storedCommonSettingId` mapping unknown — needs BLE sniffing or trial |
| `0x56` | `SetDeviceRegistrationLevel(registrationLevel: int)` | Low | Purpose unclear; value 257 mentioned in `tmp.txt` |

**Suggested approach for `0x51`:**
1. Migrate `GetStoredCommonSetting` CallClass (same pattern as `GetStatisticsDescale`)
2. Trial-and-error `storedCommonSettingId` values (0–N) while logging responses — correlate with known DpIds from `BLE_COMMAND_REFERENCE.md`
3. Once mapping is known: expose via REST API, CLI, MQTT, and HA Discovery following the "all interfaces" rule

### Filter counter — read status + expose reset command

The device tracks a ceramic honeycomb filter counter. Two things are needed:

1. **Read filter status** — no getter exists yet. Most likely accessible via `GetStoredCommonSetting` (0x51, see above). BLE sniffing the official app while it shows the filter reminder would confirm the exact `storedCommonSettingId` or DpId.
2. **Wire `ResetFilterCounter` command** — `ResetFilterCounter = 47` is already defined in `Commands.py` but not exposed on any interface. Once the read side is understood, expose both read and reset via REST API, CLI, MQTT, and HA Discovery (same "all interfaces" rule).

### Auto-restart ESP32 when BLE scanner is stuck (E0002 circuit breaker) — IMPLEMENTED

**Status: done** on `feature/esphome-auto-restart` (commit 7cf2d97, refined in 916b39b).

The ESP32's `bluetooth_proxy` component can get stuck after a BLE disconnect, stopping
advertisement forwarding while the API TCP connection stays alive.  `api: reboot_timeout:`
in ESPHome does **not** help — it only watches the API connection, not the BLE scanner.

**How it works:**
1. `button: platform: restart` added to both ESPHome YAML files (commit 05ab035).
   Flash the ESP32 once; the button then appears as `ButtonInfo` via `list_entities_services`.
2. `ApiMode._polling_loop` tracks `_consecutive_poll_failures` (incremented on every exception).
3. At exactly `_CIRCUIT_OPEN_THRESHOLD = 3` failures, `_trigger_esphome_restart()` is called.
4. `_trigger_esphome_restart()` opens a **fresh** APIClient (never touches the BLE path),
   discovers the restart button via `list_entities_services`, presses it via `button_command`.
5. After restart, `_CIRCUIT_OPEN_SLEEP = 60s` gives the ESP32 time to reboot.

**Recovery time:** 3 × 10s scan timeout = **30 seconds** from scanner stuck to restart fired.

**Requirements:** the ESPHome proxy YAML must contain:
```yaml
button:
  - platform: restart
    name: "Restart AquaClean Proxy"
```
If the button is absent, `_trigger_esphome_restart()` logs a warning and returns False —
the circuit breaker still slows down polling but cannot auto-recover without the button.

### Wire `GetStoredProfileSetting` / `SetStoredProfileSetting`

The CallClasses (`0x53` / `0x54`) are already migrated but not yet wired into any interface (REST API, CLI, MQTT, web UI). Blocked on knowing which `ProfileSettings` enum values map to useful device features.

---

## TODO

- **BLE sniffing needed — unimplemented procedures from thomas-bingel tmp.txt.**
  Two procedures cannot be implemented without first capturing their wire format
  from the official Geberit Home iOS app.  **When asked "What should I sniff?",
  start here.**

  | Proc | Name | What to trigger in the app | What to look for in the capture |
  |------|------|----------------------------|---------------------------------|
  | `0x08` | `SetActiveProfileSetting(settingId, value)` | Change a profile setting (pressure, temperature, position) during an active shower session — this proc sets a value *live*, distinct from the stored profile setting (proc 0x0A/0x0B) | Outgoing write to WRITE_0/WRITE_1 after the slider moves; note payload bytes: how many, which byte is the settingId, which bytes encode the value and in what endianness |
  | `0x56` | `SetDeviceRegistrationLevel(registrationLevel)` | App startup / first connect — the iPhone may send this once per session, value 257 (0x0101) mentioned in C# tmp.txt | Any outgoing ctx=0x01 proc=0x56 write; note full payload (expected ≥ 2 bytes for value 257) |

  **Tools:** use `tools/ble-session-replay.py --dry-run` to verify the proc
  appears in a captured log.  Use `tools/geberit-ble-probe.py --proc 0xNN`
  to test a candidate payload on the live device after identifying the format.

- ~~**Investigate E0002 / E0003 failure after iPhone app closes BLE connection.**~~
  **RESOLVED (2026-04-16, commit 36844ec).**
  Root cause confirmed: the Geberit device only accepts one BLE connection and stops advertising
  while the Geberit Home App is connected. Bridge gets E0002 during the iPhone session (expected).
  After the app closes, the device resumes advertising — bridge auto-recovers in 1-2 polls.
  The old E0003-after-iPhone pattern (ec6186bb log) was caused by a GetSPL parameter count bug:
  in stuck state the device accepts a maximum of 8 parameters; the bridge was sending 9
  ([0,1,2,3,4,5,6,7,9]) → ACK but no data → E0003. Removing index 7 reduced the count to 8
  and restored normal operation. Param 7 itself is not toxic — any 9th param causes the same
  timeout (confirmed by probe: [0,1,2,3,4,5,6,0,9] also fails).
  E0002/E0003 hints updated to instruct users to close the Geberit Home App.
  **Confirmed in:** `local-assets/geberit-aquaclean-logs/standalone/aquaclean-36844ec779b6ddcb8dd1f45eb4f42286b1158f90-TRACE-E0002-andE0003-overcome-inbetween-i-worked-with-iphone-App-and-it-recoverd-automatically.log`

- **Fix `send_request()` — `call_count` not decremented on `asyncio.CancelledError`.**
  **File:** `aquaclean_core/Clients/AquaCleanBaseClient.py`, `send_request()`, lines ~315–334.

  **The bug:** `call_count` is decremented on `asyncio.TimeoutError` and on success, but the
  `except asyncio.TimeoutError` block does NOT catch `asyncio.CancelledError`. When the asyncio
  task running `send_request()` is cancelled externally (e.g. by ServiceMode's inner loop when
  `_poll_interval_event` fires), `CancelledError` propagates out of
  `await asyncio.wait_for(self._transaction_event.wait(), ...)` and `call_count` stays at 1
  permanently. Every subsequent `send_request()` call blocks forever on
  `while self.call_count > 0: await asyncio.sleep(0.1)`.

  **Why this matters:** ServiceMode's inner polling loop cancels the running `polling_task`
  whenever `_poll_interval_event` is set (e.g. from an MQTT retained `pollInterval` message or
  a REST `POST /config/poll-interval` call during startup). If the cancellation lands while
  `polling_task` is inside `send_request()` at `await asyncio.wait_for(...)`, `call_count`
  freezes at 1. All polling dies permanently until restart. The bridge appears alive (REST,
  SSE still work) but never polls again.

  **Fix:** replace the two separate `call_count -= 1` sites with a single `finally` block:
  ```python
  with self.lock:
      self.call_count += 1
  try:
      # ... build frame, send, sleep ...
      await asyncio.wait_for(self._transaction_event.wait(), timeout=timeout_seconds)
  except asyncio.TimeoutError:
      raise BLEPeripheralTimeoutError(...)
  finally:
      with self.lock:
          self.call_count -= 1   # always decrement — TimeoutError, CancelledError, success
  ```

  **Confirmed in:** `aquaclean-silly_2026-03-06_16-17-48.log` (v2.4.59rc0, Ubuntu 24.04,
  `ble_connection=persistent`). See `docs/developer/polling-architecture.md` for full analysis.

- **Fix `wait_for_info_frames_async` — add absolute timeout to prevent indefinite blocking.**
  **File:** `aquaclean_core/Frames/FrameService.py`, `wait_for_info_frames_async()`.

  On some devices, the InfoFrame flood after a BLE reconnect (not first connect) lasts up to
  ~3 minutes. `wait_for_info_frames_async` currently exits only when the count has been stable
  for 20 polls (2s) or reached ≥ 10. If the device floods continuously for minutes, this blocks
  `connect_async()` and therefore all of ServiceMode, causing every `_polling_loop` attempt to
  time out during the wait.

  **Why this matters:** The 2m50s block is also the window during which an MQTT retained message
  or REST call can set `_poll_interval_event`, triggering the `send_request()` race above. A
  hard cap (e.g. 60s) would:
  1. Reduce the window during which the trigger can fire
  2. Allow polling to start sooner (with some InfoFrame noise, which the code already ignores)

  **Suggested fix:** add an absolute wall-clock deadline to `wait_for_info_frames_async`:
  ```python
  deadline = asyncio.get_event_loop().time() + 60.0   # 60s hard cap
  while True:
      await asyncio.sleep(0.1)
      if asyncio.get_event_loop().time() >= deadline:
          logger.warning("wait_for_info_frames_async: hard timeout (60s), proceeding")
          break
      # existing stability check...
  ```
  The 60s cap is a safe margin: on healthy first connects it finishes in ~1s; on problem
  reconnects it currently blocks up to 3 minutes.

  **Confirmed in:** same log as above. See `docs/developer/polling-architecture.md`.

- **HACS: Download button for Performance Statistics panel.**
  HA's Lovelace markdown card has no built-in download mechanism. Three options:

  1. **`custom:button-card` + inline JS** (least effort): add a button card below the
     markdown card that constructs a `data:text/plain` URI from the same Jinja template
     and triggers an `<a download>` via a JS action. Works in browsers; does NOT work
     in the HA companion app. Fragile — inline JS in YAML config.

  2. **HA `downloader` integration + REST sensor** (no custom frontend): expose the
     stats string via a template sensor or REST endpoint, then use the `downloader`
     integration to save it to disk on demand. Roundabout and not user-triggered
     from the dashboard directly.

  3. **Custom Lovelace card** (proper solution): a purpose-built JS module that renders
     the stats table and adds a native download button. Full control, works in all
     clients including the companion app. Requires writing and hosting a JS module
     (or publishing via HACS Frontend).

  Option 1 is the quickest path; option 3 is the cleanest long-term solution.

- ~~**HACS: Dynamic signal-strength visualization for BLE Signal and WiFi Signal.**~~
  **Done in dashboard (ceefaaf).** Replaced static entity rows with a dedicated
  `custom:bar-card` ("Signal Strength" card) showing a color bar per sensor:
  red (< −80 dBm) → orange → yellow-green → green (> −50 dBm), with name outside
  left and value outside right. Requires `custom:bar-card` via HACS Frontend.

- ~~**HACS: Add Geberit BLE connection status sensors (Rule 7 — Interface Parity).**~~
  **Done in v2.4.29–30.** `binary_sensor.geberit_aquaclean_ble_connected` and
  `sensor.geberit_aquaclean_ble_connection` are implemented. See MEMORY.md.

- **HACS: Add `sensor.geberit_aquaclean_ble_state` for intra-poll BLE cycle tracking.**
  `binary_sensor.geberit_aquaclean_ble_connected` reflects only the last poll outcome
  (`last_update_success`). In on-demand mode it is always `on` when polling is healthy —
  it does NOT cycle through `disconnected → connecting → connected → disconnected` within
  each poll as the webapp does. Users expect to see the live connection phase.

  **Implementation:**
  - Add `self.ble_state: str = "disconnected"` to `AquaCleanCoordinator.__init__`
  - Update it at key points in `_do_poll()`:
    - Before `connect_ble_only()` → `"connecting"`
    - After successful `connect_ble_only()` → `"connected"`
    - In `finally` block → `"disconnected"`
    - In error paths → `"error"`
  - Add `sensor.geberit_aquaclean_ble_state` as a `SensorEntity` with
    `device_class: SensorDeviceClass.ENUM`,
    `options: ["disconnected", "connecting", "connected", "error"]`
  - The sensor reads `coordinator.ble_state` and calls `self.async_write_ha_state()`
    directly — **do NOT call `coordinator.async_update_listeners()`** mid-poll.
    That pushes ALL entities, which read `coordinator.data` in a transitional state
    and caused "Unbekannt" (Unknown/grey) in v2.4.29–33. Only this one sensor
    needs to self-push.

  **Constraint:** in on-demand mode the connected phase lasts ~5–10 s out of a 30 s
  interval. The state will visibly cycle in HA — `connecting` for ~2 s, `connected`
  for ~5 s, then `disconnected` for ~23 s. This is correct and mirrors the webapp.

- **Add poll countdown to HACS integration.** The standalone webapp shows a countdown
  bar to the next poll via `poll_epoch` + `poll_interval` from the SSE stream. The
  HACS coordinator (`custom_components/geberit_aquaclean/coordinator.py`) should expose
  a `next_poll` timestamp sensor (or `time_remaining` as a `SensorEntity` with
  `device_class: timestamp` / `unit_of_measurement: "s"`), computed from
  `coordinator.data["poll_epoch"]` + `coordinator.data["poll_interval"]`.
  This lets users show a "Next poll in X s" badge in their HA dashboard.

- **Log error codes to the Python log file.** When an exception is mapped to an
  error code in `_on_demand_inner`'s finally block, only MQTT and SSE receive the
  code (e.g. E7002). The Python log file only shows the raw message from
  `AquaCleanBaseClient` (`logger.error("No response from BLE peripheral ...")`
  at line 471) with no error code. Add a `logger.error(f"BLE error {ec.code} — {e}")`
  (or similar) at the point of mapping in `main.py` so the log file is
  self-contained and error codes can be correlated without cross-referencing MQTT.

- **system-info: distinguish config.ini values from runtime values.**
  `get_system_info()` currently reads `ble_connection`, `esphome_api_connection`,
  and `poll_interval` from `config.ini` only. But these can all be changed at
  runtime via REST API, MQTT, or the webapp — and the runtime values diverge from
  the file immediately after any such change.

  The fix: split the `config` block in the returned dict into two sub-sections:

  ```json
  "config": {
    "from_file": {
      "ble_connection": "persistent",
      "poll_interval": "10.5",
      ...
    },
    "runtime": {
      "ble_connection": "on-demand",   ← may differ after a runtime toggle
      "poll_interval": 30.0,
      "esphome_api_connection": "on-demand"
    }
  }
  ```

  The `from_file` values come from `config.ini` (current `get_system_info()`
  behaviour). The `runtime` values come from `device_state` (or `ApiMode`
  attributes).

  **Implementation note:** `get_system_info()` is a module-level function with
  no access to the running `ApiMode` or `ServiceMode` instance. Two options:
  1. Pass `device_state` as an optional parameter: `get_system_info(device_state=None)`.
  2. Add a separate `ApiMode.get_system_info_data()` that merges the static dict
     with live `device_state` fields before returning (already exists as a stub —
     extend it).
  Option 2 is cleaner: the REST endpoint (`/info/system`) already calls
  `self._api_mode.get_system_info_data()`, so runtime values can be merged there
  without changing the pure module-level function. The CLI `system-info` command
  (which has no running `ApiMode`) would still show only `from_file` values, which
  is correct behaviour for a CLI invocation.

- **Investigate toggle anal shower / toggle lady shower not working.**
  The `ToggleAnalShower` (code 0) and `ToggleLadyShower` (code 1) commands are exposed
  via the HACS integration (`button.py`) and the standalone bridge (`MqttService.py`,
  REST API) but do not appear to work in practice. This is a core BLE protocol issue,
  not specific to Home Assistant. Buttons removed from `lovelace/dashboard.yaml` Controls
  section until the root cause is identified.
  Suggested investigation: BLE-sniff the official Geberit Home app while triggering the
  shower manually to confirm the correct command code and procedure ID. Compare against
  the current `SetCommandAsync(Commands.ToggleAnalShower)` implementation.

- **Add RSSI tracking to performance statistics (all interfaces).**
  BLE RSSI and WiFi RSSI are correlated with connect/poll timing: weak BLE signal → more
  link-layer retries → higher `ble_ms`; weak WiFi → TCP retransmits → higher `esphome_api_ms`.
  Tracking min/avg RSSI alongside timing enables signal-quality diagnosis of slow polls.

  **Standalone bridge (`PollStats.py`):** add `_MetricStats` for `ble_rssi` and `wifi_rssi`;
  record them alongside timing in `_on_demand_inner()` and `_on_persistent_poll_complete()`.
  Publish via `centralDevice/performanceStats` JSON (extend `to_dict()`).

  **HACS coordinator:** add `_ble_rssi_total`, `_ble_rssi_min`, `_ble_rssi_count`,
  `_wifi_rssi_total`, `_wifi_rssi_min`, `_wifi_rssi_count` in `__init__`; accumulate in
  `_async_update_data()` from `connector.rssi` / `connector.esphome_wifi_rssi`; include
  `avg_ble_rssi`, `min_ble_rssi`, `avg_wifi_rssi`, `min_wifi_rssi` in return dict.

  **HACS sensor.py:** add `AquaCleanProxyAvgBleRssiSensor`, `AquaCleanProxyMinBleRssiSensor`,
  `AquaCleanProxyAvgWifiRssiSensor`, `AquaCleanProxyMinWifiRssiSensor` on AquaClean Proxy device.

  See `memory/hacs-todos.md` for full rationale.

- **HACS: Poll countdown gauge — improve accuracy and smoothness.**
  The current template sensor (trigger: time_pattern seconds: "/30") is a best-effort
  approximation. Known rough edges: the gauge may not drain perfectly smoothly across
  all HA versions; `unavailable` state briefly shows on HA startup before the first poll.
  Investigate whether a native `SensorEntity` with `device_class: timestamp` (exposing
  `next_poll` directly) renders better in the gauge card than the percentage template sensor.
  Consider replacing the template approach with a computed sensor in `coordinator.py` /
  `sensor.py` that HA can render natively.

- **HACS: Add integration version sensor.**
  The standalone bridge's `system_info` reports app version, OS, Python, library versions,
  and BLE adapter details — all properties of the bridge *process*. In the HACS integration
  there is no bridge process; the code runs inside HA, which already surfaces OS, Python,
  and library info natively (Settings → System). The only field worth exposing is the
  **integration version** (from `manifest.json`), so users can confirm which version is
  running without navigating to Settings → Integrations.
  Implementation: read version from `manifest.json` at setup time, expose as a single
  diagnostic `SensorEntity` with `entity_category: EntityCategory.DIAGNOSTIC`.
  See memory/hacs-todos.md for rationale.

- **HACS: Add multilingual support (EN / DE / FR / IT).**
  Reference document: [`local-assets/Gemini-Home Assistant Dashboards mit Sprachauswahl.md`](../local-assets/Gemini-Home%20Assistant%20Dashboards%20mit%20Sprachauswahl.md)

  HA uses `strings.json` (master) + `translations/de.json`, `fr.json`, `it.json`.
  When a user's HA language is set, entity names, config flow labels, and state strings
  render in that language automatically.

  **Already translated by HA automatically (zero work needed):**
  - Binary sensor state values — "Frei"/"Erkannt", "Aus"/"An" from HA's device class translations
  - Timestamp formatting — "in 25 Sekunden", "Vor 8 Sekunden" — HA does this natively
  - Units like "s", "%", "d"

  **Needs explicit translation strings (~35 strings × 4 languages = ~140 entries):**
  - All ~20 entity names (`sensor.py`, `binary_sensor.py`, `button.py`) — currently hardcoded as `_attr_name = "User Sitting"` etc.
  - ~10 config flow field labels ("BLE MAC Address", "ESPHome Proxy Host", …)
  - ~5 config flow descriptions and error messages

  **Stays untranslated regardless:**
  - Lovelace card titles ("Live Status", "Descale", "Controls", etc.) — hardcoded in `dashboard.yaml`
  - Lovelace `name:` overrides — removing these lets HA show the translated entity name instead
  - Gauge title ("AquaClean Poll Countdown") — hardcoded in dashboard YAML
  - Template sensor name in `configuration.yaml` — set by the user

  **Files to create/change:**

  | File | Change |
  |------|--------|
  | `custom_components/geberit_aquaclean/strings.json` | New — master string definitions |
  | `custom_components/geberit_aquaclean/translations/en.json` | New |
  | `custom_components/geberit_aquaclean/translations/de.json` | New |
  | `custom_components/geberit_aquaclean/translations/fr.json` | New |
  | `custom_components/geberit_aquaclean/translations/it.json` | New |
  | `sensor.py`, `binary_sensor.py`, `button.py` | Replace `_attr_name = "..."` with `_attr_translation_key = "..."` |
  | `lovelace/dashboard.yaml` | Remove `name:` overrides so translated entity names show through |

  **Effort:** moderate code changes + translation content (machine translation is fine for a first pass; native speaker review recommended). ~1 focused session.

- **install.sh: show progress during slow pip steps.** On a Raspberry Pi, the
  `pip install --upgrade pip setuptools wheel` and `pip install --force-reinstall ...`
  steps can take several minutes with no output — users assume it has hung and cancel.
  Fix options (pick one or combine):
  1. Add a spinner/progress indicator running in the background while pip runs.
  2. Print a "This may take several minutes on Raspberry Pi…" warning before each
     slow step.
  3. Add `--progress-bar on` explicitly to the pip commands (pip defaults to `on`
     for TTYs but the curl-pipe context may suppress it).
  4. Add `--timeout 60` so a genuine network hang fails fast with a clear error
     instead of silently blocking forever.
  The simplest effective fix is option 2 + 4 combined: one-liner warning +
  timeout guard. Option 1 (spinner) is the most user-friendly but requires a
  background job and `trap` cleanup.

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

10. **"Only one API subscription is allowed at a time" on bridge restart**
    → `self._esphome_unsub_adv` was only stored in `_connect_via_esphome()` at line 244
    — AFTER the BLE GATT connect succeeded. When the bridge receives SIGTERM while
    waiting for a BLE advertisement (the 30-second scan window), asyncio cancels the
    task via `CancelledError`. The `finally` block in `_on_demand_inner` calls
    `disconnect_ble_only()`, but `self._esphome_unsub_adv` is still `None` — so the
    BLE advertisement subscription is never released. The ESP32 holds it until its ping
    timeout (~60–90 s). If the new bridge polls within that window, it gets the "Only
    one subscription" rejection.
    **Fix**: store `self._esphome_unsub_adv = unsub_adv` immediately after calling
    `api.subscribe_bluetooth_le_raw_advertisements()`, before the `asyncio.wait_for`.
    In the `TimeoutError` handler, clear `self._esphome_unsub_adv = None` after calling
    it to prevent a double-unsubscribe in `disconnect_ble_only()`.

12. **"Only one API subscription" on every BLE scan — two-TCP conflict AND stale ref**
    Two independent bugs combine to break every BLE scan:
    **Bug A (fresh start / across sessions):** `_start_esphome_log_streaming()` creates
    a SECOND TCP connection to the same ESP32 (`self._esphome_log_api`). The ESP32's
    `api_connection_` allows only ONE BLE advertisement subscription across all TCP
    connections. The log streaming connection connects ~10 s before the first BLE poll
    and claims the slot. Every `SubscribeBluetoothLEAdvertisementsRequest` from the BLE
    connector's TCP is permanently rejected. Sending `UnsubscribeBluetoothLEAdvertisementsRequest`
    from the BLE connector is useless — it never owned the subscription.
    **Fix A:** `_start_esphome_log_streaming()` returns immediately (with a WARNING) when
    `esphome_host` is configured. Log streaming and the ESPHome BLE proxy are mutually
    exclusive on the same ESP32.
    **Bug B (within-session, between BLE cycles):** `disconnect_ble_only()` was calling
    and nulling `_esphome_unsub_adv` after each BLE cycle. The defensive cleanup at the
    start of `_connect_via_esphome()` always found it `None` and skipped. The old
    subscription on the ESP32 was never released before the next subscribe attempt →
    "Only one API subscription" again. This was the regression introduced in commit
    `8c52b2d` when `disconnect_ble_only()` was written with the unsub+null included.
    The correct pattern (from commit `927e953`): `disconnect_ble_only()` must **keep**
    `_esphome_unsub_adv` alive so `_connect_via_esphome()` can call it at the very start
    of the next request (before any BLE connection is active), then
    `_ensure_esphome_api_connected()` reconnects TCP if the unsubscribe closes it.
    **Fix B:** Remove unsub+null from `disconnect_ble_only()` — the reference stays
    intact until `_connect_via_esphome()` uses it at the start of the next request.
    **Bug C (Home Assistant conflict):** If the `aquaclean-proxy` ESPHome integration
    is **enabled** in Home Assistant, HA opens its own persistent TCP connection to the
    ESP32 and permanently holds the BLE subscription slot. Every bridge connection is
    rejected. **Fix C:** Disable (not delete) the `aquaclean-proxy` integration in
    HA → Settings → Integrations. It must remain disabled while the standalone bridge
    is in use. See `docs/esphome-troubleshooting.md`.

13. **`asyncio.TimeoutError` from `BleakClient.connect()` not caught — wrong error code or crash**

    When `BleakClient.connect()` times out (e.g. after many `le-connection-abort-by-local`
    retries on a local BlueZ adapter), it raises `asyncio.TimeoutError` — which is **not**
    a subclass of `BleakError`. Three places each had a gap:

    | Location | Symptom without fix |
    |---|---|
    | `ServiceMode.run()` | Falls to `handle_exception()` → `sys.exit(1)` — **bridge crashes** in persistent mode |
    | `_on_demand_inner` finally | `isinstance(_exc, BleakError)` fails → `_ec = E7002` ("Poll loop error") instead of E0003 — **wrong error shown in webapp** |
    | `_polling_loop` | Falls to `except Exception` → publishes E7002 to MQTT instead of E0003 |

    **Observed symptom (on-demand mode, local BlueZ):** webapp cycles
    "connecting… → error (no message) → connecting…" because E7002's hint says
    "An error occurred in the background polling loop" — unhelpful for a BLE timeout.
    In persistent mode the same timeout would crash the bridge entirely.

    **Root of the timeout itself (`le-connection-abort-by-local`):** BlueZ connects at
    the link layer (`Connected: True` D-Bus signal) but immediately aborts with reason
    `0x16`. Bleak retries automatically within the `connect()` call. After the default
    10-second timeout, `asyncio.TimeoutError` is raised. On the Raspberry Pi 5 with
    the built-in adapter this retry loop can take 25+ seconds before eventually
    succeeding — or always timeout, depending on BlueZ state.

    **Fix applied:** Added explicit `except asyncio.TimeoutError` handlers in all three
    locations, treating them identically to `BleakError` — mapped to E0003, with the
    correct hint, no crash.


11. **ANSI escape codes (\033[1;31m …) visible in log file from aioesphomeapi**
    → At `SILLY` log level, `main.py` skips suppressing the `aioesphomeapi` loggers
    (lines 103–105 check `if log_level not in ('TRACE', 'SILLY')`). The
    `aioesphomeapi.connection` logger at DEBUG level logs raw protobuf payloads, which
    include the ANSI color codes the ESP32 embeds in its own log messages. These appear
    as literal escape sequences (`\033[1;31m`) in the log file.
    **Fix**: add a `logging.Filter` subclass (`_AnsiFilter`) to the `aioesphomeapi`
    logger at module level in `main.py`. The filter calls `record.getMessage()`,
    strips ANSI codes with the same regex used in `_on_esphome_log_message`, and
    replaces `record.msg` / clears `record.args`. Applied unconditionally — harmless
    at INFO level, essential at DEBUG/SILLY.

---

## BLE protocol — Commands, ProfileSettings, and BLE_COMMAND_REFERENCE.md

### Two-layer protocol

The code uses reverse-engineered C# enum codes, NOT the DpIds from `BLE_COMMAND_REFERENCE.md`.
The DpIds (e.g. 563 = anal shower) are Geberit's device-level data point IDs — useful as a
conceptual reference for what the device supports, but not directly callable from the code.

**Layer 1 — `SetCommandAsync(Commands.X)`** (`aquaclean_core/Clients/Commands.py`)
Sends Procedure=0x09 with a 1-byte command code. All are toggles/triggers:

| Command | Code | Exposed in app? |
|---|---|---|
| `ToggleAnalShower` | 0 | ✅ |
| `ToggleLadyShower` | 1 | ✅ |
| `ToggleDryer` | 2 | ❌ |
| `StartCleaningDevice` | 4 | ❌ |
| `ExecuteNextCleaningStep` | 5 | ❌ |
| `PrepareDescaling` | 6 | ❌ |
| `ConfirmDescaling` | 7 | ❌ |
| `CancelDescaling` | 8 | ❌ |
| `PostponeDescaling` | 9 | ❌ |
| `ToggleLidPosition` | 10 | ✅ |
| `ToggleOrientationLight` | 20 | ❌ |
| `StartLidPositionCalibration` | 33 | ❌ |
| `LidPositionOffsetSave` | 34 | ❌ |
| `LidPositionOffsetIncrement` | 35 | ❌ |
| `LidPositionOffsetDecrement` | 36 | ❌ |
| `TriggerFlushManually` | 37 | ❌ |
| `ResetFilterCounter` | 47 | ❌ |

**Layer 2 — `GetStoredProfileSettingAsync` / `SetStoredProfileSettingAsync`** (`aquaclean_core/Clients/ProfileSettings.py`)
Reads/writes stored user settings by index. Getters already in `AquaCleanClient`, most not in REST:

| Setting | Index | Getter | Setter |
|---|---|---|---|
| `OdourExtraction` | 0 | ✅ | ✅ |
| `OscillatorState` | 1 | ✅ | ❌ |
| `AnalShowerPressure` | 2 | ✅ | ❌ |
| `LadyShowerPressure` | 3 | ❌ | ❌ |
| `AnalShowerPosition` | 4 | ✅ | ❌ |
| `LadyShowerPosition` | 5 | ✅ | ❌ |
| `WaterTemperature` | 6 | ✅ | ❌ |
| `WcSeatHeat` | 7 | ✅ | ❌ |
| `DryerTemperature` | 8 | ✅ | ❌ |
| `DryerState` | 9 | ✅ | ❌ |
| `SystemFlush` | 10 | ✅ | ❌ |

**Layer 3 — `GetSystemParameterList([0,1,2,3,4,5,7,9])`**
Reads live device state (what's happening right now). Indices 0–9 are the only ones known.
These are NOT DpIds — separate index space.

**SPL parameter semantics — confirmed from BLE log (2026-04-15):**
The code comment labels are misleading. Actual semantics confirmed from toilet-use session log:
- **Index 0** — labelled `userIsSitting` in code, actually fires on **user approach/proximity detection** (lid opens ~17s after this becomes 1, before user actually sits)
- **Index 1** — labelled `analShowerIsRunning`, actually tracks **user sitting**
- **Index 3** — labelled `dryerIsRunning`, actually tracks **anal shower running** (confirmed: changes when anal shower started by pressing device button)
- Dryer state: not visible in any SPL parameter change in the captured session — may be at an index not polled by the bridge, or polled differently by the iPhone

**Proc 0x55 — fixed init-sequence call (2026-04-15):**
- Sent by iPhone once per session, at the END of the init sequence (after all GetStoredCommonSetting reads), before any user action
- Payload: `[0x01]` (1 byte), response: `0x00`
- NOT approach-triggered — present in Connect-Toggle-Lid log too (no approach in that session)
- Purpose unknown — possibly "session ready" / "enable remote control"
- The lid opening 17s after Proc(0x55) in the Stuhlgang log was coincidental: the device's own proximity sensor opened the lid independently; the app just happened to connect while the user was approaching

### Quick-win new commands (zero new protocol code needed)
All unexposed Commands enum entries just need REST endpoints + web UI wiring.
No new protocol code needed — `SetCommandAsync(Commands.X)` already handles all of them.

**Procedure codes confirmed in aquaclean-SILLY.log:**
- `0x0D` — GetSystemParameterList (batched state poll)
- `0x09` — SetCommand (toggle/trigger)
- `0x82` — GetDeviceIdentification
- `0x86` — GetDeviceInitialOperationDate
- `0x81` — GetSOCApplicationVersions

**Discrete DpId Procedure ID (for BLE_COMMAND_REFERENCE.md DpIds directly): UNKNOWN.**
Not observed in any log. To find it: BLE-sniff the official Geberit Home app.
Needed only for things not in Commands/ProfileSettings (water hardness, flush volumes,
error status registers, descaling schedule). Not needed for the quick-win commands.

### `tmp.txt` — unimplemented API-layer procedures (thomas-bingel C# repo)

`aquaclean-core/Api/CallClasses/tmp.txt` is a scratch file of procedures that were
planned but never implemented as full CallClasses in the C# repo. It is the missing link
**within the API layer** (same layer as GetSystemParameterList, SetCommand etc.) but it
does NOT reveal the discrete DpId procedure code Gemini called the "missing link".

**Two layers remain separate:**
- `tmp.txt` / CallClasses = structured API procedures (codes known, see table)
- `BLE_COMMAND_REFERENCE.md` DpIds = discrete data point access (procedure code still UNKNOWN)

**Procedures revealed by `tmp.txt`:**

| Procedure | Call | Status |
|-----------|------|--------|
| `0x45` | `GetStatisticsDescale()` → returns `StatisticsDescale` struct | ✅ implemented |
| `0x51` | `GetStoredCommonSetting(storedCommonSettingId)` → 2-byte int | ❌ not yet implemented |
| `0x56` | `SetDeviceRegistrationLevel(int registrationLevel)` // 257 | ❌ not yet implemented |

**`GetStoredCommonSetting` (0x51)** is potentially the bridge between the API layer and the
BLE_COMMAND_REFERENCE.md layer: it may be able to read device settings (water hardness,
descaling intervals etc.) that also appear as DpIds in BLE_COMMAND_REFERENCE.md.
The `storedCommonSettingId` parameter meaning is unknown — needs BLE sniffing or trial.

### BLE_COMMAND_REFERENCE.md
Located at `operation_support/BLE_COMMAND_REFERENCE.md`. Verified against `DpId.cs` source.
Use it to understand WHAT the device supports conceptually, but do NOT try to map its DpIds
directly to Commands enum codes — they are different numbering systems.
For 90% of useful functionality, Commands.py + ProfileSettings.py are sufficient.
For the remaining 10% (water hardness, error status, etc.), BLE sniffing is required.

---

## Related repositories

**haggis dependency removed (2026-02-23):** `haggis` was used only for `add_logging_level`.
The patched fork became incompatible with Python 3.13 (`_acquireLock` removed after being
added as a Python 3.11 workaround for the upstream Python 3.12-only API).
Replaced with a 10-line inline `_add_logging_level()` in `main.py` — no external dependency,
works on all Python versions.

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
- Startup version logging: `importlib.metadata.version("geberit-aquaclean")` logged as
  INFO before the config dump; falls back to `"unknown"` if package metadata is missing
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

### Release checklist (MANDATORY)

**Do not tag and create a release until all of the following docs are up to date:**

| File | What to check |
|------|--------------|
| `README.md` | Install steps, curl commands, feature list, documentation table |
| `docs/configuration.md` | New config keys documented in table and example block |
| `docs/cli.md` | New CLI flags or commands documented |
| `docs/home-assistant.md` | HA-facing changes reflected |
| `docs/hacs-integration.md` | HACS integration changes, version-specific notes |
| `homeassistant/SETUP_GUIDE.md` | Install steps, discovery, upgrading section |

Only bump `pyproject.toml` and run `gh release create` once all affected docs are updated in the same commit (or in a preceding commit on the same push).

#### Git tag vs GitHub Release — HACS will NOT see a bare git tag

**Pushing a git tag is not enough.** HACS exclusively reads GitHub Releases.
A bare `git push --tags` leaves the version invisible to HACS users.

**Mandatory release sequence:**
```bash
# 1. Bump versions in pyproject.toml + manifest.json, commit, push
git tag vX.Y.Z
git push origin main --tags

# 2. Create the GitHub Release — this is what HACS actually reads
gh release create vX.Y.Z --title "vX.Y.Z" --notes "- change 1\n- change 2"
```

Confirmed root cause (2026-02-24): v2.4.15 and v2.4.16 were pushed as git tags only;
neither appeared in HACS until `gh release create` was run for both.

### MQTT ↔ HA Discovery dependency (MANDATORY)

**Any change to an outbound MQTT topic requires a matching update in two places:**

1. `get_ha_discovery_configs()` in `main.py` — the auto-discovery path
   (`--command publish-ha-discovery` / `--command remove-ha-discovery`)
2. `homeassistant/configuration_mqtt.yaml` — the manual config alternative

Both must stay in sync with every `send_data_async(topic, ...)` call.
The comment in `get_ha_discovery_configs()` says the same: *"HOW TO KEEP THIS IN SYNC:
when you add a new send_data_async() call elsewhere in this file, add the corresponding
HA entity here."*

Similarly, adding a new MQTT-published feature should also be reflected in:
- `homeassistant/dashboard_button_card.yaml`
- `homeassistant/dashboard_simple_card.yaml`

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

---

## External references

| Resource | URL |
|----------|-----|
| Geberit AquaClean Mera Comfort — Service Manual (PDF) | https://cdn.data.geberit.com/documents-a6/972.447.00.0_00-A6.pdf |
| thomas-bingel C# reference repo | https://github.com/thomas-bingel/geberit-aquaclean |

---

## Planned: `--scan` CLI command

`aquaclean-bridge --scan` (and `--scan --esphome-host <ip>`) for BLE device discovery
at first-time setup.  Auto-selects local bleak or ESPHome path.  Scan logic belongs
inside the package (`BluetoothLeConnector.scan()` or similar) — `ble-scan.py` becomes
a thin wrapper or is retired.  DRY: one scan implementation, two consumers (CLI + ble-scan.py).
See `docs/roadmap.md` for full spec.

## Planned: HACS custom integration (Home Assistant, no MQTT)

**Goal:** native HA integration installable via HACS.  No MQTT broker required.
Standalone bridge + MQTT fully preserved alongside.

**Structure (both options):**
- `hacs.json` at repo root (`"category": "integration"`)
- `custom_components/geberit_aquaclean/` — thin HA adapter only
- `manifest.json` `requirements` points to this same repo's pip package → zero protocol code duplicated
- `config_flow.py` replaces `config.ini` for the HA context (MAC, optional ESPHome host)
- `coordinator.py` (`DataUpdateCoordinator`) replaces MQTT — calls `AquaCleanClient` directly
- Entity files (`sensor.py`, `switch.py`, etc.) — wrappers around coordinator data

---

### Option A — bypass HA BLE, use `BluetoothLeConnector` directly (recommended first)

**How:** `coordinator.py` instantiates `BluetoothLeConnector` exactly as the standalone
bridge does.  HA's `bluetooth` domain is not involved.

**Pros:**
- Same battle-tested code path as standalone bridge — already proven
- Low risk: no new infrastructure, no HA BLE stack integration
- Straightforward: ~740 lines of new glue code

**Cons:**
- If HA itself is also using the local BLE adapter, adapter-conflict possible
  (same root cause as two TCP connections to ESP32)
- Not HA-native: device won't appear in HA's Bluetooth integration panel
- No automatic BLE device discovery flow in HA UI

**Estimated cost:** ~25–40K tokens total (write + debug).  1–2 sessions.

---

### Option B — integrate with HA's `bluetooth` domain

**How:** register as a `bluetooth` passive scanner consumer.  HA delivers
`BLEDevice` objects via scan callbacks; a new adapter layer maps them to
`AquaCleanClient`.  ESPHome proxy path uses `bleak-esphome` + `habluetooth`
inside HA's runtime (which IS available in HA, unlike standalone).

**Pros:**
- Fully HA-native: device appears in Bluetooth panel, auto-discovery flow
- No BLE adapter conflict — HA manages the adapter
- ESPHome proxy via `bleak-esphome` works inside HA (habluetooth is initialized)

**Cons:**
- ~4× more effort: ~1,500–2,500 lines including adapter layer
- `habluetooth` inside HA behaves differently than standalone — needs re-validation
  (see CLAUDE.md trap re: bleak-esphome requiring habluetooth)
- Discovery flow in `config_flow.py` is fiddly
- Higher risk of subtle bugs at the HA BLE abstraction boundary

**Estimated cost:** ~80–150K tokens total.  3–5 sessions, higher debugging risk.

---

**Recommendation:** implement Option A first.  If HA-native BLE experience becomes
important, migrate to Option B as a follow-on.  The coordinator/entity structure is
identical — only the connector layer changes.

**Why Option A first is the only sensible order:**
The coordinator + entity layer (bulk of the HA integration work) is identical in both
options.  The only difference is what sits behind `coordinator.py` as the transport.
Option A gives a fully working HA integration; Option B is then a single-layer swap.
Doing Option B first means solving two problems simultaneously ("make a working HA
integration" AND "integrate with HA's BLE stack") — if something breaks, you don't
know which layer caused it.

**BLE adapter conflict is moot for this setup:** the conflict (HA also using the local
adapter) only matters when running the bridge on the same machine as HA with local BLE
and no ESPHome proxy.  With the ESPHome proxy in use, Option A has zero adapter
conflict risk.

See `docs/roadmap.md` for the full spec.

---

## Before every new release tag — check standalone install compatibility

Before creating any new git tag (whether for a fix, HACS update, or any other reason),
verify that the standalone `curl | bash -s -- latest` install still works:

1. `gh api repos/jens62/geberit-aquaclean/releases/latest --jq '.tag_name,.prerelease'`
   → must be non-prerelease (`false`) and point to the intended tag
2. The tag must include the correct `pyproject.toml` version — `aquaclean-bridge --version`
   must match the tag name
3. `custom_components/` is ignored by pip (`pyproject.toml` only includes
   `aquaclean_console_app*`) — safe to have on main, does not affect standalone installs
4. Pre-release tags (e.g. `v2.4.13-hacs-beta`) are excluded from `releases/latest`
   automatically — no risk from HACS beta tags

**Common mistake:** tagging before bumping `pyproject.toml` — the tag then reports the
old version via `--version`. Always bump `pyproject.toml` (and `manifest.json`) BEFORE
tagging, commit, then tag that commit.

---


## After every fix — test install curl

After committing a fix, always supply this curl command for the user to test on raspi-5.
Use the **full commit SHA** (not the branch name) in both the raw URL and the `bash -s --` argument:

```bash
curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/<FULL_SHA>/operation_support/install.sh | bash -s -- <FULL_SHA>
```

Get the full SHA with `git rev-parse HEAD` after committing.

---

## Communication style

### Markdown with brackets in terminal
When the user asks for markdown text containing `[]()` links or `![]()` image embeds
(e.g. draft GitHub issue comments), always wrap the entire response in a fenced
code block (` ``` ` ... ` ``` `). Square brackets are interpreted by zsh/bash and
will be stripped or cause errors if output as plain text in the terminal.

