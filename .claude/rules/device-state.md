# Device State

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
