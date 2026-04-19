# Geberit AquaClean — Connection Test

`tools/aquaclean-connection-test.py` diagnoses the full connection stack from
your machine to the Geberit toilet and prints a **PASS / WARN / FAIL** result
with a specific fix for every problem it finds.

---

## What the script tests

| Step | What it checks |
|------|---------------|
| 0 | ESPHome proxy discovered via mDNS (auto-discovery, no `--host` needed) |
| 1 | TCP port 6053 reachable on the ESP32 |
| 2 | ESPHome API handshake (encryption key, firmware version) |
| 3 | BLE advertisement subscription (detects HA ESPHome integration conflict) |
| 4 | BLE scan — is the Geberit device advertising? |
| 5 | Full BLE connect and disconnect via the proxy |
| 6 | Device Identification — reads Description, Serial, SAP Number, Production Date, Initial Operation Date, Firmware via bridge stack |
| 7 | ESP32 log stream — optional, streams colorised device logs for N seconds (`--stream-logs`) |

---

## Prerequisites

You need **Python 3.11 or later** and the `aioesphomeapi` library.

```bash
pip install aioesphomeapi        # ESPHome mode (default)
pip install aioesphomeapi bleak  # also needed for --local-ble
```

The bridge package itself must be installed (or the script run from the repo
root) for **Step 6** (Device Identification).  Steps 1–5 work without it.

---

## Installation

### Windows

1. Download from https://www.python.org/downloads/ — check **"Add Python to PATH"**
2. `pip install aioesphomeapi`
3. Clone or download the repo: `git clone https://github.com/jens62/geberit-aquaclean`

### macOS / Linux / Raspberry Pi

```bash
pip install aioesphomeapi
# or inside the project venv:
source /opt/aquaclean/venv/bin/activate && pip install aioesphomeapi
```

---

## Running the script

### Auto-discovery (no --host needed)

```bash
python tools/aquaclean-connection-test.py
```

The script scans the local network for ESPHome devices via mDNS and
auto-selects the one whose name contains "aquaclean".  If multiple ESPHome
devices are found, a warning is shown — use `--host` to be explicit.

### Test a specific MAC address

```bash
python tools/aquaclean-connection-test.py --mac 38:AB:41:2A:0D:67
```

Runs the full 6-step sequence including device identification.

### Explicit ESPHome host

```bash
python tools/aquaclean-connection-test.py --host 192.168.0.160 --mac 38:AB:41:2A:0D:67
```

### With API encryption key

```bash
python tools/aquaclean-connection-test.py \
    --host 192.168.0.160 \
    --noise-psk "your+base64+key==" \
    --mac 38:AB:41:2A:0D:67
```

### Local BLE adapter (no ESPHome proxy)

```bash
python tools/aquaclean-connection-test.py --local-ble
python tools/aquaclean-connection-test.py --local-ble --mac 38:AB:41:2A:0D:67
```

Skips all ESPHome steps and uses the local Bluetooth adapter (bleak) instead.

### Stream ESP32 logs for debugging

```bash
# Stream for 30 seconds after all other steps (default)
python tools/aquaclean-connection-test.py --mac 38:AB:41:2A:0D:67 --stream-logs

# Stream for 60 seconds at verbose level
python tools/aquaclean-connection-test.py --stream-logs --stream-duration 60 --log-level verbose
```

Connects a **separate** API client after all BLE steps have completed and
streams the ESP32's log output.  Requires `logger: level: DEBUG` (or lower)
in `aquaclean-proxy.yaml`; see [below](#esp32-logger-level).

---

## All flags

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | auto (mDNS) | ESPHome proxy IP or hostname |
| `--port` | 6053 | ESPHome API port |
| `--noise-psk` | none | Base64 API encryption key |
| `--mac` | none | Geberit BLE MAC address |
| `--scan-duration` | 20s | BLE scan / connect timeout |
| `--local-ble` | off | Use local Bluetooth adapter instead of ESPHome |
| `--identify-by` | `any` | `any` \| `name` \| `uuid` — Geberit device detection strategy |
| `--dump-ads` | off | Print raw BLE advertisement AD structures for every device |
| `--stream-logs` | off | Stream ESP32 device logs after all BLE steps |
| `--stream-duration` | 30s | How long to stream logs |
| `--log-level` | `debug` | ESP32 log level: `error` \| `warn` \| `info` \| `debug` \| `verbose` |

---

## Device detection strategies (`--identify-by`)

| Value | Detection method | Use case |
|-------|-----------------|----------|
| `any` (default) | Name prefix `HB` OR `geberit` in name OR 16-bit UUID `0x3EA0` | Most robust — works on all known models |
| `name` | Name only (`HB…` prefix or `geberit` in name) | Fast, no UUID parsing |
| `uuid` | 16-bit service UUID `0x3EA0` in advertisement | Testing UUID detection |

The confirmed 16-bit UUID `0x3EA0` is present in Geberit AC PRO advertisements
(and expected on all models).  The Mera Comfort advertises as `HB2304EU…`.

---

## Auto-dump on detection failure

When no Geberit device is found in the BLE scan, the script **automatically
dumps the raw advertisement AD structures** (ESPHome path) or parsed service
UUIDs and manufacturer data (local BLE path) for every device that was seen.
This lets you identify your device without re-running with `--dump-ads`.

---

## Auto GATT diagnostic on identification failure

When Step 5 (BLE connect) succeeds but Step 6 (Device Identification) fails,
the script automatically runs a **GATT service discovery** to diagnose whether
the device exposes the expected Geberit AquaClean GATT profile:

```
Service  3334429d-90f3-4c41-a02d-5cb3a03e0000  ✅ Geberit AquaClean Service
  Char   3334429d-90f3-4c41-a02d-5cb3a13e0000  [WRITE]   ← WRITE_0
  Char   3334429d-90f3-4c41-a02d-5cb3a23e0000  [WRITE]   ← WRITE_1
  Char   3334429d-90f3-4c41-a02d-5cb3a53e0000  [NOTIFY]  ← READ_0
```

- **Service found → protocol failure**: device GATT profile is correct; likely a
  transient state — power-cycle the toilet and retry.
- **Service NOT found**: wrong device, unsupported model, or device in an
  unusual firmware state.

---

## Common failure scenarios

### FAIL — Port 6053 not reachable

```
[FAIL]  Port 6053 on 192.168.0.160
         → The ESP32 is not reachable or port 6053 is blocked.
```

1. Ping the ESP32: `ping 192.168.0.160`
2. Check your router's connected-devices list for the correct IP.
3. If you used a hostname (`aquaclean-proxy.local`), try the IP instead
   — mDNS resolution can fail on Windows and some networks.
4. Flash the ESP32 with `aquaclean-proxy.yaml` if not done yet.

---

### FAIL — API encryption handshake failed

```
[FAIL]  API connect
         Encryption handshake failed
```

Find `api_encryption_key` in your `secrets.yaml` and pass it:
```bash
python aquaclean-connection-test.py --host 192.168.0.160 --noise-psk "abc123=="
```

---

### FAIL — BLE subscription rejected ("Only one API subscription")

```
[FAIL]  BLE subscription
         Rejected: Only one API subscription is allowed at a time
```

**This is the most common issue when running the bridge alongside Home Assistant.**

The ESP32 allows only **one** active BLE advertisement subscription at a time.
The Home Assistant ESPHome integration permanently holds this slot.

**Fix:**
1. Home Assistant → **Settings → Devices & Services → ESPHome**
2. Find your `aquaclean-proxy` entry → ⋮ → **Disable**
3. Wait 60–90 seconds for the ESP32 to release the slot
4. Run the test again

> **Important:** Disable, do not delete — deleting removes your HA automations.

---

### FAIL — Target device not found in scan

```
[FAIL]  Target device found
         MAC AA:BB:CC:DD:EE:FF not seen during 20s scan
```

| Cause | Fix |
|-------|-----|
| Geberit Home app is open | Force-close the app on every device — the toilet allows only one BLE connection at a time |
| Another bridge instance connected | Stop it, wait 30s, retry |
| Toilet off or in deep sleep | Approach the toilet (proximity sensor wakes it) or power-cycle |
| Wrong MAC address | Check the scan output for `HB…` or `Geberit…` names |
| Scan too short | `--scan-duration 40` |

The script automatically dumps advertisement data for all seen devices when
no Geberit is detected, so you can check if your device is there under a
different name.

---

### FAIL — Disconnected before connected (reason 0x16)

```
[FAIL]  BLE connect
         Disconnected before connection established (reason 0x16)
```

The Bluetooth link layer connected but the Geberit device immediately
disconnected.

1. **Close the Geberit Home app completely** — most common cause.
2. **Unsupported model** — some models may require a different handshake.
   Open an issue at https://github.com/jens62/geberit-aquaclean/issues and
   paste the full output.

---

### FAIL — Device Identification timeout (Step 6)

```
[FAIL]  Device identification
         BLEPeripheralTimeoutError: No response from BLE peripheral
         → BLE connected but GATT protocol failed — running GATT diagnostic below.
```

BLE connected successfully but the device did not respond to the identification
request.  The script automatically runs the GATT diagnostic (see above).

Most likely causes:
- Device busy or in an unusual state → **power-cycle the toilet** (30s off at
  the wall switch) and retry.
- Geberit Home app connected between steps → close it completely.

---

## ESP32 logger level

`--stream-logs` requires the ESP32 firmware to be compiled with
`logger: level: DEBUG` or lower.  Without it, only INFO-level messages
(ESPHome version, chip model) are received; BLE scanner activity is silent.

Add to your `aquaclean-proxy.yaml`:

```yaml
logger:
  level: DEBUG
```

Then reflash the ESP32 once.  After that, `--stream-logs` shows live events:

```
[D] [bluetooth_proxy] Subscribed to raw advertisements
[D] [esp32_ble_tracker] Found device HB2304EU298413 RSSI=-65
[D] [bluetooth_proxy] Connecting to 38:AB:41:2A:0D:67
[D] [bluetooth_proxy] Connected, MTU=23
```

---

## Example output (all passing)

```
Geberit AquaClean — Connection Test

Step 0 — ESPHome Discovery (mDNS)
  ───────────────────────────────────
  Scanning for ESPHome devices on the network (8s) …
  [PASS]  mDNS scan
           Found 1 ESPHome device: aquaclean-proxy-c3 at 192.168.0.114:6053

Step 1 — TCP Reachability
  ──────────────────────────
  [PASS]  Port 6053 on 192.168.0.114

Step 2 — ESPHome API Connection
  ────────────────────────────────
  [PASS]  API connect
           Connected to 'aquaclean-proxy-c3' (ESPHome 2026.1.5)
  [PASS]  ESP32 MAC
           94:A9:90:68:B0:E0  model: esp32-c3-devkitm-1

Step 3 — BLE Advertisement Subscription
  ─────────────────────────────────────────
  [PASS]  BLE subscription
           Receiving BLE packets (124 total, first arrived in 6ms)

Step 4 — BLE Scan
  ───────────────────
  [PASS]  Geberit device(s) found (identify-by: any)
           Use --mac <MAC> to verify the correct device and test BLE connection.

Step 5 — BLE Connect Attempt
  ─────────────────────────────
  [PASS]  Device advertisement
           Seen MAC 38:AB:41:2A:0D:67  address_type=0
  [PASS]  BLE connect
           Successfully connected to 38:AB:41:2A:0D:67 via ESP32 proxy!

Step 6 — Device Identification
  ────────────────────────────────
  [PASS]  BLE connect
           Connected to 38:AB:41:2A:0D:67

    Description             AquaClean Mera Comfort
    Serial Number           HB2304EU298413
    SAP Number              146.21x.xx.1
    Production Date         11.04.2023
    Initial Operation       31.05.2024
    Firmware                RS10.0 TS18

  [PASS]  Device identification

Summary
  ─────────
  All checks passed.  The connection stack is healthy.
```

---

## Getting help

If the output does not resolve your issue, open a GitHub issue and paste the
**full script output**:

https://github.com/jens62/geberit-aquaclean/issues
