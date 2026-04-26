# Mock Geberit AquaClean Alba BLE Peripheral

**File:** `tools/mock-geberit-alba.py`

Simulates the AquaClean Alba BLE peripheral on Linux (BlueZ) for testing the
unsupported-device detection in the HACS config flow and the connection test
script — without requiring a physical Alba device.

---

## Requirements

- Linux with BlueZ (Bluetooth daemon running)
- Python packages: `dbus-next`, `bluez_peripheral`
- Sufficient D-Bus privileges (run as root or with appropriate group membership)
- BlueZ experimental features may be required (`Experimental=true` in `/etc/bluetooth/main.conf`)

```bash
pip install dbus-next bluez_peripheral
sudo python tools/mock-geberit-alba.py
# or with a venv that has the required packages:
sudo /home/jens/venv/bin/python ./mock-geberit-alba.py
```

---

## GATT profile advertised

The mock exposes two services that match the real Alba device profile
(confirmed from iPhone BLE capture of `E4:85:01:CD:B0:08`):

| Service UUID | Description |
|---|---|
| `559eb100-2390-11e8-b467-0ed5f89f718b` | Vendor service A (write + read only, no notify) |
| `0000fd48-0000-1000-8000-00805f9b34fb` | BT SIG-registered service with vendor characteristics |

Characteristics on `0000fd48`:

| UUID | Properties | Role |
|---|---|---|
| `559eb001-2390-11e8-b467-0ed5f89f718b` | WRITE_NO_RESP | Command channel (write) |
| `559eb002-2390-11e8-b467-0ed5f89f718b` | NOTIFY | Notification channel (read) |

The `0000fd48` service + `559eb001`/`559eb002` characteristics are what
`classify_services()` in `GattDiscovery.py` identifies as the Alba candidate
profile (non-standard, `is_standard=False`).

---

## What the mock does NOT implement

The mock does not respond to any Geberit protocol frames. Writing to
`559eb001` is accepted at the GATT level but produces no notification on
`559eb002`. This causes `subscribe_notifications_async()` to time out with
`BLEPeripheralTimeoutError` — which is the correct trigger for the
unsupported-device detection path added in `config_flow.py` (v2.4.81-pre).

---

## Device MAC address

The mock's BLE address is the Linux BT adapter's own MAC — whatever
`hci0` reports (e.g. `88:A2:9E:2C:EA:F7` on the dev Raspberry Pi).
Use this MAC in all test invocations.

---

## Known behaviour / gotchas

### 1. May stop re-advertising after a connect/disconnect cycle

BlueZ peripheral advertising can pause or stop after a client connects and
then disconnects. **Always restart the mock fresh before each test session.**
Do not rely on a long-running mock instance for back-to-back tests.

### 2. HA ESPHome integration must be disabled

If the `aquaclean-proxy` ESPHome integration is enabled in Home Assistant, it
silently holds the BLE advertisement subscription slot on the ESP32. The
connection test and the HACS config flow both get "0 packets received" even
though the subscription is accepted. Disable (not delete) the integration in
HA → Settings → Integrations before testing.

### 3. Connection test Step 5 may fail even if Step 4 finds the device

The connection test script runs a 20-second passive scan in Step 4, then
re-subscribes for Step 5. The re-subscription gap plus possible advertising
pause causes Step 5 to miss the mock. Step 6 (GATT discovery via direct
connect) still works correctly. This is a test-script timing artifact, not a
mock defect.

---

## Testing the HACS unsupported-device detection

### Goal
Verify that the HACS config flow shows the `unsupported_device` abort screen
(with GATT UUID details) instead of the generic `cannot_connect` error.

### Prerequisite — free the ESP32 subscription slot

The ESP32 can only serve one BLE advertisement subscription at a time. Two things
compete for that slot and must both be cleared before testing:

1. **`aquaclean-proxy` ESPHome integration** — disable it in HA → Settings →
   Integrations → aquaclean-proxy → Disable. This is the primary slot thief.

2. **Existing Geberit AquaClean HACS integration** — do **not** delete or disable it;
   you will add the mock as a *second* instance alongside it. But its polling holds
   the slot briefly on each cycle. To minimise the race:
   - Settings → Integrations → Geberit AquaClean → Configure → set Poll Interval
     to `300` seconds. This leaves a 5-minute gap between polls.
   - Or disable it temporarily (three dots → Disable) — HA remembers the config;
     re-enable after the test.

### Steps

1. Apply the prerequisite above (ESPHome integration disabled, poll interval raised
   or existing Geberit integration disabled).

2. Start the mock fresh on the Linux host (Raspberry Pi or similar):
   ```bash
   sudo /home/jens/venv/bin/python ./mock-geberit-alba.py
   ```
   Wait for `--- Mock Device Active ---` before proceeding.

3. **Immediately** go to HA: Settings → Integrations → blue "+" button (bottom-right)
   → search "Geberit AquaClean" → Add Integration.
   - BLE MAC: `<adapter MAC printed by mock, e.g. 88:A2:9E:2C:EA:F7>`
   - ESPHome host: `<ESP32 IP>` (or leave empty for local BLE if HA host is in BLE
     range of the mock)
   - Port: `6053`

   Do not use the existing integration's Configure button — that reconfigures the
   real device. The "+" button adds a new second instance pointing to the mock.

4. Click Submit. Wait up to 30 seconds.

5. Expected result: the "unsupported device" abort screen appears with:
   - GATT service UUID: `0000fd48-0000-1000-8000-00805f9b34fb`
   - Write characteristic: `559eb001-2390-11e8-b467-0ed5f89f718b`
   - Notify characteristic: `559eb002-2390-11e8-b467-0ed5f89f718b`
   - Link to open a GitHub issue

   The mock terminal should print:
   ```
   [Mock] BLE client connected:    XX:XX:XX:XX:XX:XX
   [Mock] BLE client disconnected: /org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX
   ```
   If the mock prints nothing, the ESP32 never found the device (see troubleshooting
   below).

### Confirming via HA logs

Add to `configuration.yaml` to see the INFO trace:

```yaml
logger:
  default: warning
  logs:
    custom_components.geberit_aquaclean: info
```

Expected log lines (in order):

```
INFO  [AquaClean] Config flow: BLE connected but non-standard GATT profile
      detected after init failure — svc=0000fd48... write=[559eb001...] notify=[559eb002...]
WARNING [AquaClean] Config flow: unsupported GATT profile — svc=0000fd48...
```

If instead you see `ERROR [AquaClean] Config flow: connection test failed`, check
the preceding line for the `ESPHomeDeviceNotFoundError` detail:

```
ESPHomeDeviceNotFoundError: AquaClean device ... not found via ESPHome proxy
  (received 0 total BLE advertisement packet(s) during 10 s scan —
   scanner may be stuck or subscription slot in use)
```
→ 0 packets: `aquaclean-proxy` ESPHome integration is still enabled, or another
  client holds the slot. Disable it and retry.

```
  (received 847 packet(s) during 10 s scan — device not advertising)
```
→ N > 0 packets: scanner is healthy but the mock wasn't advertising at the right
  moment. Restart the mock and submit the form immediately.

---

## How the detection works (v2.4.81-pre)

`connect_ble_only()` partially succeeds for the Alba device:
1. BLE connects → `connector.client` is set with full GATT service table
2. `subscribe_notifications_async()` times out (mock ignores Geberit frames) → exception

Before v2.4.81-pre the exception was caught as `cannot_connect` immediately.
The fix in `config_flow._test_connection()` catches the exception, calls
`connector.get_gatt_profile()` (which reads `connector.client.services`), and
if the profile is non-standard returns it to the caller — which then aborts
with `reason="unsupported_device"` instead of showing the generic error.

---

## Connection test usage

```bash
# Via ESPHome proxy
/Users/jens/venv/bin/python tools/aquaclean-connection-test.py \
  --mac 88:A2:9E:2C:EA:F7 --dynamic-uuids --host 192.168.0.114

# Via local BLE (HA host in BLE range of mock)
/Users/jens/venv/bin/python tools/aquaclean-connection-test.py \
  --mac 88:A2:9E:2C:EA:F7 --dynamic-uuids
```

Expected result: Step 6 GATT discovery succeeds and shows the `0000fd48`
service; protocol probe fails with `BLEPeripheralTimeoutError` (expected).
The GATT profile FAIL / candidate detection confirms `classify_services()`
correctly identifies the Alba profile as non-standard.
