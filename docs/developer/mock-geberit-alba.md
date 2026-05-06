# Mock Geberit AquaClean Alba BLE Peripheral

**File:** `tools/mock-geberit-alba.py`

Simulates the AquaClean Alba BLE peripheral on Linux (BlueZ). Supports two
modes:

| Mode | Purpose |
|------|---------|
| `--mode unsupported` (default) | Advertises the Alba GATT profile but ignores all writes — triggers the unsupported-device detection path in the HACS config flow |
| `--mode handshake` | Implements the full server-side Arendi Security protocol — use this to test end-to-end encryption without a physical Alba |

For a purely in-process crypto test (no BLE hardware at all) see
**`tests/test_arendi_security.py`** below.

---

## Requirements

- Linux with BlueZ (Bluetooth daemon running)
- Python packages: `dbus-next`, `bluez_peripheral`
- Sufficient D-Bus privileges (run as root or with appropriate group membership)
- BlueZ experimental features may be required (`Experimental=true` in `/etc/bluetooth/main.conf`)

```bash
pip install dbus-next bluez_peripheral
sudo /home/jens/venv/bin/python tools/mock-geberit-alba.py --mode handshake
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

`classify_services()` in `GattDiscovery.py` identifies this as the Alba
candidate profile (`is_standard=False`).

---

## Device MAC address

The mock's BLE address is the Linux BT adapter's own MAC — whatever
`hci0` reports (e.g. `88:A2:9E:2C:EA:F7` on the dev Raspberry Pi).
The mock prints it at startup as **"Adapter BLE address"**.
Use this MAC in all test invocations.

---

## Mode: `--mode handshake` — testing Alba decryption

### What it tests

Verifies that the bridge's `AriendiSecurity.py` implementation can:
1. Complete the full Arendi Security handshake against a live BLE peer
2. Encrypt outgoing Geberit frames correctly
3. Decrypt incoming encrypted frames correctly

The mock generates fresh ephemeral X25519 keys and random nonces on every
run, so each session is cryptographically independent.

### How it works

`_AriendiServerSide` in `mock-geberit-alba.py` implements the device role of
the protocol, importing the crypto primitives directly from
`aquaclean_console_app/bluetooth_le/LE/AriendiSecurity.py` (no duplication).

Handshake sequence (device side):

| Step | Receives | Sends |
|------|----------|-------|
| 1 | SABM (U-frame) | UA (U-frame) |
| 2 | VERSION_REQ (0x00) | VERSION_RESP (0x01) — proto v2 |
| 3 | EP_REQ (0x10) | EP_RESP (0x11) — fresh nonce1 + nonce2 |
| 4 | KE_REQ (0x12) — client pubkey + CMAC | KE_RESP (0x13) — server pubkey + CMAC |
| 5 | S-RR ACK | — |

After the handshake the mock loops on incoming encrypted frames: it decrypts
each one, prints the plaintext hex, and sends back a fake `GetDeviceIdentification`
OK response (encrypted).

Key derivation (device perspective):
- `key_material = HKDF(shared_secret, salt=nonce1, length=32)`
- `tx_key = key_material[0:16]` — device encrypts outgoing with this
- `rx_key = key_material[16:32]` — device decrypts incoming with this

(Reversed from the client perspective in `AriendiSecurity.perform_handshake`.)

### Step-by-step

**Step 1 — Start the mock on the Raspberry Pi:**
```bash
sudo /home/jens/venv/bin/python tools/mock-geberit-alba.py --mode handshake
```
Wait for `--- Mock Device Active (mode=handshake) ---` and note the printed
adapter MAC address.

**Step 2 — Point the bridge at the mock MAC.**
Edit `config.ini` on the Pi:
```ini
[BLE]
device_id = 88:A2:9E:2C:EA:F7   # ← replace with your adapter MAC
```

**Step 3 — Start the bridge:**
```bash
/home/jens/venv/bin/python -m aquaclean_console_app --mode service
```

**Expected mock output when the handshake succeeds:**
```
[Mock] BLE client connected:    XX:XX:XX:XX:XX:XX
[MockServer] ← SABM
[MockServer] → UA
[MockServer] ← VERSION_REQ
[MockServer] → VERSION_RESP (proto v2)
[MockServer] ← EP_REQ
[MockServer] → EP_RESP  nonce1=<32 hex chars>  nonce2=<32 hex chars>
[MockServer] ← KE_REQ
[MockServer] client CMAC verified ✓
[MockServer] → KE_RESP  server_pub=<16 hex chars>...
[MockServer] *** HANDSHAKE COMPLETE — session keys established ***
[MockServer] ← encrypted frame DECRYPTED: <hex of Geberit request>
[MockServer] → fake GetDeviceIdentification response (encrypted)
```

The line `client CMAC verified ✓` confirms the `aquacleanBridgeId` in
`AriendiSecurity.py` is correct. If it says `CMAC verification FAILED`, the
key stored in the bridge does not match what the real device expects.

### HACS config flow with `--mode handshake`

When `arendi_handshake_done = True`, the HACS coordinator and config flow no
longer abort with E0010 / unsupported-device — the Alba is treated as a
supported device and polling proceeds normally. The first poll will send a
`GetSystemParameterList` request; the mock decrypts it and responds with the
fake `GetDeviceIdentification` blob. The bridge may log a parse warning on the
fake response, but the decryption layer is confirmed working.

---

## Mode: `--mode unsupported` (default) — testing HACS unsupported-device detection

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

1. Apply the prerequisite above.

2. Start the mock:
   ```bash
   sudo /home/jens/venv/bin/python tools/mock-geberit-alba.py
   ```
   Wait for `--- Mock Device Active (mode=unsupported) ---`.

3. **Immediately** go to HA: Settings → Integrations → **"Eintrag hinzufügen"**
   (German UI) / **"Add entry"** (English UI) → search "Geberit AquaClean" → select it.
   - BLE MAC: `<adapter MAC printed by mock>`
   - ESPHome host: `<ESP32 IP>` (or leave empty for local BLE)
   - Port: `6053`

   **Do not** use the existing integration's Configure button — that reconfigures the
   real device. "Add entry" adds a second instance pointing to the mock.

4. Click Submit. Wait up to 30 seconds.

5. Expected result: "unsupported device" abort screen with the GATT UUIDs and a
   link to open a GitHub issue.

   Mock terminal should print:
   ```
   [Mock] BLE client connected:    XX:XX:XX:XX:XX:XX
   [Mock] BLE client disconnected: /org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX
   ```

### Confirming via HA logs

```yaml
logger:
  default: warning
  logs:
    custom_components.geberit_aquaclean: info
```

Expected log lines:
```
INFO  [AquaClean] Config flow: BLE connected but non-standard GATT profile
      detected after init failure — svc=0000fd48... write=[559eb001...] notify=[559eb002...]
WARNING [AquaClean] Config flow: unsupported GATT profile — svc=0000fd48...
```

If you see `connection test failed` with `0 total BLE advertisement packet(s)`:
the `aquaclean-proxy` ESPHome integration is still enabled or another client
holds the subscription slot.

---

## Known behaviour / gotchas

### 1. May stop re-advertising after a connect/disconnect cycle

BlueZ peripheral advertising can pause after a client connects and disconnects.
**Always restart the mock fresh before each test session.**

### 2. HA ESPHome integration must be disabled

See prerequisite above — this is the most common cause of `0 packets received`.

### 3. Connection test Step 5 may fail even if Step 4 finds the device

The re-subscription gap between Steps 4 and 5 in `aquaclean-connection-test.py`
plus the advertising pause can cause Step 5 to miss the mock. Step 6 (GATT
discovery via direct connect) still works. This is a timing artifact in the
test script, not a mock defect.

### 4. Notification sending (`--mode handshake`)

The mock sends BLE notifications by calling
`emit_properties_changed({'Value': Variant('ay', data)})` on the notify
characteristic's dbus-next `ServiceInterface`. BlueZ intercepts this D-Bus
`PropertiesChanged` signal and converts it to a BLE ATT Handle Value
Notification. This relies on bluez_peripheral storing characteristic objects
in `Service._chars` in declaration order (`_chars[0]` = sig_write,
`_chars[1]` = sig_notify) — an implementation detail that may change across
bluez_peripheral versions. If notifications are not received by the bridge,
check the mock's `"Notify characteristic interface wired."` startup line; if
it prints `"notifications disabled"` instead, the `_chars` layout has changed
and the index needs updating in `mock-geberit-alba.py:main()`.

---

## In-process unit test — `tests/test_arendi_security.py`

No BLE hardware required. Runs on any machine with the project venv.

### What it tests

| Test | What it verifies |
|------|-----------------|
| `test_handshake_completes` | Both client and server reach `handshake_done = True` |
| `test_client_to_server_encryption` | Client encrypts; server decrypts to original plaintext |
| `test_server_to_client_encryption` | Server encrypts; client decrypts to original plaintext |
| `test_round_trip_multiple_frames` | 5 frames in each direction all decrypt correctly |
| `test_tampered_frame_dropped` | A byte-flipped frame is rejected by the CRC check |
| `test_wrong_auth_key_fails_cmac` | Wrong key causes KE_REQ CMAC verification to fail |

### How to run

```bash
# Standalone
/Users/jens/venv/bin/python tests/test_arendi_security.py

# With pytest
/Users/jens/venv/bin/python -m pytest tests/test_arendi_security.py -v
```

Expected output:
```
PASS test_handshake_completes
PASS test_client_to_server_encryption
PASS test_server_to_client_encryption
PASS test_round_trip_multiple_frames
PASS test_tampered_frame_dropped
PASS test_wrong_auth_key_fails_cmac

6 passed, 0 failed
```

### How it works

The test instantiates a real `AriendiSecurity` (client) and a `_ServerSide`
(device role, same logic as `_AriendiServerSide` in the mock) and pipes ATT
bytes between them via `asyncio.Queue` — no BLE stack involved. Each test runs
a fresh handshake with random nonces and ephemeral keys, so the session keys
differ on every run.

**Run this before any hardware test.** If any test fails here, both the mock
and a real Alba will fail for the same reason — fix the crypto layer first.

---

## How the unsupported-device detection works (v2.4.81-pre)

`connect_ble_only()` partially succeeds for the Alba device:
1. BLE connects → `connector.client` is set with full GATT service table
2. `subscribe_notifications_async()` times out (mock ignores Geberit frames) → exception

The exception handler in `config_flow._test_connection()` calls
`connector.get_gatt_profile()`, and if the profile is non-standard returns it
to the caller — which aborts with `reason="unsupported_device"` instead of the
generic error.

## How Alba support works when the handshake succeeds

`_post_connect()` detects Variant A, creates `AriendiSecurity`, and calls
`perform_handshake()`. If the handshake completes, `connector.arendi_handshake_done`
is `True`. The coordinator and config flow check `is_variant_a and not arendi_handshake_done`
before raising E0010 — so a successful handshake falls through to normal polling.

---

## Connection test usage

```bash
# Via ESPHome proxy
/Users/jens/venv/bin/python tools/aquaclean-connection-test.py \
  --mac 88:A2:9E:2C:EA:F7 --dynamic-uuids --host 192.168.0.114

# Via local BLE
/Users/jens/venv/bin/python tools/aquaclean-connection-test.py \
  --mac 88:A2:9E:2C:EA:F7 --dynamic-uuids
```

Expected result: Step 6 GATT discovery succeeds and shows the `0000fd48`
service; protocol probe fails with `BLEPeripheralTimeoutError` (expected for
`--mode unsupported`). With `--mode handshake` the probe completes the
handshake and the connection test reports success.
