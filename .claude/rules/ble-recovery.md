# BLE Recovery Protocol

## `wait_for_device_restart`

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
