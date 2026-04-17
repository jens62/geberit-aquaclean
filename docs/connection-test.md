# Geberit AquaClean — ESPHome Proxy Connection Test

This guide helps you run `tools/aquaclean-connection-test.py` to diagnose
why the bridge cannot connect to your Geberit toilet via the ESPHome BLE proxy.

The script tests each layer of the connection stack in order and prints a
**PASS / WARN / FAIL** result with a specific fix for every problem it finds.

---

## What the script tests

| Step | What it checks |
|------|---------------|
| 1 | TCP port 6053 reachable on the ESP32 |
| 2 | ESPHome API handshake (encryption key, firmware) |
| 3 | BLE advertisement subscription (detects HA conflict) |
| 4 | BLE scan — is the Geberit device advertising? |
| 5 | Full BLE connect and disconnect via the proxy |

---

## Prerequisites

You need **Python 3.11 or later** and the `aioesphomeapi` library.
The bridge itself does **not** need to be installed.

---

## Installation

### Windows

1. **Install Python** (if not already installed):
   - Download from https://www.python.org/downloads/
   - During installation, check **"Add Python to PATH"**
   - Verify: open Command Prompt and run `python --version`

2. **Install aioesphomeapi**:
   ```
   pip install aioesphomeapi
   ```

3. **Download the script** — one of:
   - Clone the repository: `git clone https://github.com/jens62/geberit-aquaclean`
   - Or download the file directly from GitHub:
     `https://github.com/jens62/geberit-aquaclean/raw/main/tools/aquaclean-connection-test.py`

### macOS

```bash
pip3 install aioesphomeapi
# Script is in tools/ if you already cloned the repo
```

### Linux / Raspberry Pi

```bash
pip install aioesphomeapi
# Or, if using a virtualenv:
source /opt/aquaclean/venv/bin/activate
pip install aioesphomeapi
```

---

## Running the script

### Basic scan — list all BLE devices near the ESP32

```bash
python tools/aquaclean-connection-test.py --host 192.168.0.160
```

Windows:
```
python tools\aquaclean-connection-test.py --host 192.168.0.160
```

This scans for 20 seconds and lists every BLE device the ESP32 can see.
Look for a device with a name starting with **HB** — that is your Geberit toilet.

### Test a specific MAC address

Once you know the MAC address (from the scan output or your router's device list):

```bash
python tools/aquaclean-connection-test.py --host 192.168.0.160 --mac AA:BB:CC:DD:EE:FF
```

With this option the script also attempts a full BLE connection to verify
the complete path works end-to-end.

### With API encryption key

If your `aquaclean-proxy.yaml` uses `api_encryption`, supply the key:

```bash
python tools/aquaclean-connection-test.py \
    --host 192.168.0.160 \
    --noise-psk "your+base64+key==" \
    --mac AA:BB:CC:DD:EE:FF
```

### Extend the scan duration

If the device is intermittently visible (e.g. Geberit Home app recently closed):

```bash
python tools/aquaclean-connection-test.py --host 192.168.0.160 --scan-duration 40
```

---

## Common failure scenarios and fixes

### FAIL — Port 6053 not reachable

```
[FAIL]  Port 6053 on 192.168.0.160
         → The ESP32 is not reachable or port 6053 is blocked.
```

**Fix:**
1. Make sure the ESP32 is powered and the LED is on.
2. Ping the ESP32: `ping 192.168.0.160`
3. If the IP is wrong, check your router's connected-devices list.
4. If you used a hostname (`aquaclean-proxy.local`), try the IP address instead
   — mDNS resolution can fail on Windows.
5. If the ESP32 has never been flashed with `aquaclean-proxy.yaml`, flash it first
   (see `esphome/aquaclean-proxy.yaml` in the repository).

---

### FAIL — API encryption handshake failed

```
[FAIL]  API connect
         Encryption handshake failed
         → The ESP32 requires an API encryption key
```

**Fix:**
Open your `aquaclean-proxy.yaml` (or `secrets.yaml`) and find the value of
`api_encryption_key`. Pass it to the script:
```
python aquaclean-connection-test.py --host 192.168.0.160 --noise-psk "abc123=="
```

---

### FAIL — BLE subscription rejected ("Only one API subscription")

```
[FAIL]  BLE subscription
         Rejected: Only one API subscription is allowed at a time
         → The ESP32 already has a BLE subscription from another client.
         → FIX: In Home Assistant go to:
         →   Settings → Devices & Services → ESPHome
         →   Find your aquaclean-proxy entry → click ⋮ → Disable
```

**This is the most common issue when running the bridge alongside Home Assistant.**

The ESP32's `bluetooth_proxy` component allows only **one** active BLE
advertisement subscription at a time. When the Home Assistant ESPHome integration
is enabled for your `aquaclean-proxy` device, HA permanently holds this slot —
leaving none for the bridge.

**Fix:**
1. In Home Assistant: **Settings → Devices & Services → ESPHome**
2. Find the entry for your `aquaclean-proxy`
3. Click the three-dot menu (⋮) → **Disable**
4. Wait 60–90 seconds for the ESP32 to release the slot
5. Run the test again

> **Important:** "Disable" keeps the entry in HA but stops it from connecting.
> Do NOT delete the integration — you would lose any HA automations referring to it.

---

### FAIL — Target device not found in scan

```
[FAIL]  Target device found
         MAC AA:BB:CC:DD:EE:FF not seen during 20s scan
         → The Geberit toilet was not advertising during the scan.
         → • The Geberit Home app is open on a phone/tablet nearby.
         →   Close the Geberit Home app completely, then retry.
```

**Causes and fixes:**

| Cause | Fix |
|-------|-----|
| Geberit Home app is open | Force-close the app on every device (phone, tablet). The toilet only allows one BLE connection at a time. |
| Another bridge instance is connected | Stop the other bridge. Wait 30s. Retry. |
| Toilet is off or in deep sleep | Approach the toilet (the proximity sensor may wake it). Try power-cycling (30s off). |
| Wrong MAC address | Look at the scan output for any `HB…` device name and use that MAC. |
| Scan too short | Re-run with `--scan-duration 40`. |

---

### FAIL — Disconnected before connected (reason 0x16)

```
[FAIL]  BLE connect
         Disconnected before connection established (reason 0x16)
```

The ESP32 made the Bluetooth link-layer connection, but the Geberit device
immediately disconnected.

**Causes and fixes:**

1. **Geberit Home app is connected** — close it completely and retry.
2. **Device model not yet supported** — some models (e.g. Geberit Alba) may
   require a different connection handshake. If closing the app does not help,
   please open an issue at https://github.com/jens62/geberit-aquaclean/issues
   and paste the full script output.

---

### PASS on all steps but bridge still fails?

If all 5 steps pass but the bridge still returns errors:

1. Re-run with `--scan-duration 30` to make sure signal strength is adequate.
2. Check the bridge log for the specific error code (E0002, E0003, E7002).
3. See the error code reference in the bridge documentation.
4. Open an issue with:
   - Full output of this script
   - The bridge log excerpt showing the error

---

## Example output (all passing)

```
Geberit AquaClean — ESPHome Proxy Connection Test
  Host:           192.168.0.160:6053
  Noise PSK:      (none)
  Target MAC:     38:AB:12:34:56:78
  Scan duration:  20s

  Step 1 — TCP Reachability
  ──────────────────────────
  [PASS]  Port 6053 on 192.168.0.160
           TCP connection succeeded

  Step 2 — ESPHome API Connection
  ────────────────────────────────
  [PASS]  API connect
           Connected to 'aquaclean-proxy' (ESPHome 2026.1.5)
  [PASS]  ESP32 MAC
           AA:BB:CC:11:22:33  model: ESP32-POE-ISO

  Step 3 — BLE Advertisement Subscription
  ─────────────────────────────────────────
  [PASS]  BLE subscription
           Receiving BLE packets (12 total, first arrived in 180ms)

  Step 4 — BLE Scan
  ──────────────────
  Scanning for 20 seconds …  (Geberit devices advertise as 'HB…')

  MAC Address          RSSI  Name
  ───────────────────────────────────────────────────────
  38:AB:12:34:56:78    -61 dBm  HB2304EU298413   ← TARGET
  AA:BB:CC:11:22:33    -42 dBm  iPhone-Jens
  DD:EE:FF:44:55:66    -78 dBm

  3 device(s) found in 20s.
  [PASS]  Target device found
           MAC 38:AB:12:34:56:78 visible at -61 dBm

  Step 5 — BLE Connect Attempt
  ─────────────────────────────
  Trying to connect to 38:AB:12:34:56:78 via ESP32 BLE proxy …
  [PASS]  Device advertisement
           Seen MAC 38:AB:12:34:56:78  address_type=0
  [PASS]  BLE connect
           Successfully connected to 38:AB:12:34:56:78 via ESP32 proxy!

  The ESP32 proxy and Geberit BLE connection work correctly.

  Summary
  ────────
  [PASS]  Port 6053 on 192.168.0.160
  [PASS]  API connect
  [PASS]  ESP32 MAC
  [PASS]  BLE subscription
  [PASS]  Target device found
  [PASS]  BLE connect

  All checks passed.  The connection stack is healthy.

  aioesphomeapi version: 24.6.2
  Python version:        3.12.3
```

---

## Getting help

If the script output does not resolve your issue, open a GitHub issue and
paste the **full script output**:

https://github.com/jens62/geberit-aquaclean/issues
