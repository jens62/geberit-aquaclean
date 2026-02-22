# ESPHome Bluetooth Proxy — Troubleshooting

Issues encountered during setup of the ESP32-POE-ISO-16MB as a BLE proxy.

---

## Quick pre-flight check — is port 6053 open?

Before running any script, verify the ESP32's API port is reachable:

```bash
nc -zw1 192.168.0.160 6053 && echo open || echo closed
```

`nc` (netcat) with `-z` (scan only, no data) and `-w1` (1 s timeout).
Works on macOS and Linux. Returns in under 1 s either way.

Alternative using the bash `/dev/tcp` built-in (Linux only, no external tool needed):

```bash
timeout 1 bash -c '</dev/tcp/192.168.0.160/6053' && echo open || echo closed
```

If the port is closed, the ESP32 is either still booting, has crashed, or is not running
ESPHome.  Wait ~60 s and retry, or power-cycle the ESP32.

---

## ESPHome 2026.1 breaking changes

### `api: password:` has been removed

```
api: … The 'password' option has been removed in ESPHome 2026.1.0.
Password authentication was deprecated in May 2022.
```

**Fix — option A (recommended for home LAN, no auth):**

```yaml
api:
```

**Fix — option B (encrypted):**

```yaml
api:
  encryption:
    key: !secret api_encryption_key
```

Generate the key once with `openssl rand -base64 32` and store it in `secrets.yaml` as
`api_encryption_key`.  Mirror the same value in `config.ini` as `noise_psk`.

> The placeholder `"change-me"` in `secrets.yaml` is **not** valid base64 and will fail
> with `Invalid key format, please check it's using base64`.

---

### `clk_mode:` has been removed

```
WARNING [ethernet] The 'clk_mode' option is deprecated and will be removed in ESPHome 2026.1.
```

Old syntax (no longer valid):

```yaml
ethernet:
  clk_mode: GPIO17_OUT
```

New syntax (ESPHome 2026.1+) — `clk` is now a dictionary:

```yaml
ethernet:
  clk:
    pin: GPIO17
    mode: CLK_OUT
```

---

## `esphome logs` fails with `Address in use` (macOS)

```
ERROR Address in use when binding to ('10.37.129.2', 5353); On BSD based systems …
```

macOS's `mDNSResponder` permanently holds port 5353.  ESPHome's zeroconf cannot bind it to
resolve `.fritz.box` / `.local` hostnames.  Intermittent — sometimes works, sometimes fails.

**Fix:** use the IP address directly instead of the hostname:

```bash
# instead of:
esphome logs esphome/aquaclean-proxy-wifi.yaml --device 192.168.0.160

# use:
esphome logs esphome/aquaclean-proxy-wifi.yaml --device 192.168.0.160
```

Same applies to `ble-scan.py` and `config.ini` when the mDNS error appears.

---

## GPIO12 strapping pin warning

```
WARNING GPIO12 is a strapping PIN and should only be used for I/O with care.
```

GPIO12 is wired to the LAN8720 `power_pin` on the ESP32-POE-ISO board — it cannot be
changed.  The warning is **harmless and expected**.  No action needed.

---

## `esphome logs` validates the YAML before connecting

`esphome logs --device <host>` validates the config locally first.  Even though no flashing
occurs, all YAML errors must be resolved before the log stream can start.  You do **not**
need to flash first just to stream logs.

---

## Log looks quiet after startup — no BLE activity visible

The proxy YAMLs use `logger: level: INFO` by default.  BLE scan events
(`gap_scan_result`, advertisement discoveries, proxy connections) are logged at
DEBUG or VERBOSE level and are invisible at INFO.

This is **normal and not a fault**.  The firmware is scanning; it just isn't logging it.

To see BLE activity in the log, temporarily set the log level to DEBUG, OTA reflash,
then rerun `esphome logs`:

```yaml
# in aquaclean-proxy-eth.yaml or aquaclean-proxy-wifi.yaml
logger:
  level: DEBUG   # change back to INFO for permanent use
```

```bash
esphome run esphome/aquaclean-proxy-eth.yaml --device 192.168.0.154
esphome logs esphome/aquaclean-proxy-eth.yaml --device 192.168.0.154
```

At DEBUG level you will see BLE scan results, connection events, and proxy activity.
Change back to INFO once confirmed — DEBUG is very chatty at runtime.

To verify the proxy is working without changing the log level, use `ble-scan.py` directly:

```bash
python esphome/ble-scan.py 192.168.0.154
```

---

## `gap_scan_result - event 0` in the logs

```
[VV][esp32_ble_tracker:319]: gap_scan_result - event 0
```

This is normal.  Event 0 is `ESP_GAP_BLE_SCAN_RESULT_EVT` from the ESP-IDF BLE GAP layer —
the scanner received a BLE advertisement.  It confirms BLE scanning is active and working.

The `[VV]` level (very verbose) is set in the firmware currently on the device.
`aquaclean-proxy-wifi.yaml` / `aquaclean-proxy-eth.yaml` set `logger: level: INFO` which suppresses these after flashing.

---

## `ble-scan.py` returns "No devices found" — root cause: raw vs parsed advertisements

**Symptom:** `ble-scan.py` connects successfully but finds no devices, even though the ESP32
is scanning and BLE devices are nearby.

**Root cause (ESPHome 2026.1+):** ESPHome switched from parsed individual advertisements
(`BluetoothLEAdvertisementsResponse`) to batched raw advertisements
(`BluetoothLERawAdvertisementsResponse`).

Old approach (no longer works):
```python
client.subscribe_bluetooth_le_advertisements(callback)  # subscribes to parsed format
```

New approach (ESPHome 2026.1+):
```python
client.subscribe_bluetooth_le_raw_advertisements(callback)  # subscribes to raw batched format
```

The raw format requires parsing AD (Advertisement Data) structures manually to extract device names.
Device names appear in AD type 0x08 (Shortened Local Name) or 0x09 (Complete Local Name).

**Fix:** `ble-scan.py` now uses `subscribe_bluetooth_le_raw_advertisements` with AD structure
parsing. This is the correct approach for ESPHome 2026.1+.

**Historical note:** The original issue was attempting to use `habluetooth.BluetoothManager()`
(Home Assistant's base class) which discards all advertisement data because `_discover_service_info`
is abstract. The solution was to use `aioesphomeapi` directly — but with the RAW subscription, not
the parsed one.

---

## E0002 / "No devices found" — ESP32 BLE scanner stuck

**Symptom:** The application logs repeated E0002 errors ("BLE device not found via ESPHome proxy")
even though the Geberit toilet is powered on and was working fine before.
Running `ble-scan.py` against the ESP32 returns "No devices found" — zero advertisements seen,
despite the ESP32 port being open and the TCP connection succeeding.

```
Checking port 6053 on 192.168.0.160 … OK
Connecting …
Scanning for 10 s …
No devices found.
```

**Cause:** The ESP32's internal BLE scanner occasionally gets into a stuck state where it stops
reporting advertisements. This is an ESP32/ESPHome firmware issue unrelated to the bridge code.
The Geberit device is advertising normally; the ESP32 is simply not forwarding the packets.

**Fix:** Power-cycle the ESP32 (unplug and replug power / PoE). Within ~30 seconds the scanner
recovers and all nearby BLE devices become visible again:

```
MAC Address               RSSI  Name
----------------------------------------------------------
38:AB:41:2A:0D:67      -82 dBm  Geberit AC PRO
…
34 device(s) found.
```

**Diagnostic:** before rebooting the application after an E0002 run, confirm the ESP32 is the
problem (not the Geberit being out of range):

```bash
python esphome/ble-scan.py 192.168.0.160
```

- **"No devices found"** → ESP32 scanner stuck → power-cycle the ESP32
- **Geberit visible in list** → scanner is fine → check the bridge config or Geberit power

**Note:** Port 6053 remains open and TCP connections succeed even when the scanner is stuck.
The port check alone is not sufficient to confirm the ESP32 is healthy.

---

## bleak-esphome v3.x does not work standalone (requires Home Assistant)

**Symptom:**
```
FAILED: BluetoothManager has not been set

Note: bleak-esphome v3.x requires habluetooth infrastructure.
Standalone usage may not be fully supported.
```

**Root cause:** bleak-esphome v3.6.0+ calls `habluetooth.get_manager()` internally, which returns
an uninitialized singleton outside of Home Assistant. The manager is only set up inside HA's
Bluetooth integration.

**What this means:**
- ✅ `aioesphomeapi` works standalone (raw ESPHome protocol)
- ✅ Scanning via `ble-scan.py` works (`aioesphomeapi.subscribe_bluetooth_le_raw_advertisements`)
- ❌ `bleak-esphome` does NOT work standalone (requires HA's habluetooth infrastructure)

**Workarounds:**
1. Use `aioesphomeapi` directly for GATT operations (see `local-assets/esphome-aioesphomeapi-probe.py`)
2. Route through Home Assistant instead of standalone Python scripts
3. Keep using local Bluetooth with `bleak` (no ESP32 proxy)

**For the AquaClean bridge:** The ESP32 proxy works perfectly for diagnostics (`ble-scan.py`),
but integrating it into the bridge requires either:
- Rewriting the bridge to use `aioesphomeapi` instead of `bleak`, or
- Running the bridge inside Home Assistant's environment

---

## `connect_scanner() got an unexpected keyword argument 'host'`

```
TypeError: connect_scanner() got an unexpected keyword argument 'host'
```

The `bleak-esphome` API changed in recent versions.  The old convenience pattern:

```python
# OLD — no longer works
from bleak_esphome import connect, connect_scanner
scanner = connect_scanner(host="...", port=6053)
client  = await connect(device, host="...", port=6053)
```

New pattern — the ESP32 is registered as a backend for standard `bleak.BleakScanner`
via `habluetooth`:

```python
# NEW
import bleak
import habluetooth
from bleak_esphome import APIConnectionManager, ESPHomeDeviceConfig

device: ESPHomeDeviceConfig = {"address": "aquaclean-proxy.fritz.box", "noise_psk": None}
conn = APIConnectionManager(device)

await habluetooth.BluetoothManager().async_setup()
await asyncio.wait([asyncio.create_task(conn.start())], timeout=5.0)

# now use bleak normally — it routes through the ESP32
devices = await bleak.BleakScanner.discover(return_adv=True, timeout=10.0)

await conn.stop()
```

Install the extra dependency if needed:

```bash
pip install habluetooth
```

---

## `ble-scan.py` causes ESP32 to crash / reboot

Symptom sequence:
1. First run: port open, TCP connects, then `Timeout waiting for HelloResponse after 30.0s`
2. Second run immediately after: `UNREACHABLE` — ESP32 is still rebooting

**Cause:** a previous `ConnectionResetError` during disconnect left the ESP32's API stack
in a bad state.  The next connection attempt cannot complete the hello handshake; the
ESP32 crashes and reboots.  The default 30 s hello timeout makes it very slow to fail.

**Fix:** wait ~30–60 s for the ESP32 to finish rebooting, then try again.
`ble-scan.py` now wraps `client.connect()` with a 10 s timeout so it fails fast instead
of hanging for 30 s.

**If it happens repeatedly:** flash `aquaclean-proxy-wifi.yaml` or `aquaclean-proxy-eth.yaml` —
the old firmware may have API stack issues that are fixed in the new config.

---

## `ble-scan.py` shows "No devices found" but `esphome logs` sees BLE events

`subscribe_bluetooth_le_advertisements` returns nothing when the firmware forwards
**raw** advertisements instead of parsed ones.  This depends on the firmware's
`BluetoothProxyFeature` flags.

The old WiFi-based firmware (`local-assets/esphome.yml`) may use a different
advertisement forwarding mode than `aquaclean-proxy-wifi.yaml` / `aquaclean-proxy-eth.yaml`.

**Fix:** flash the appropriate proxy YAML and retry.

---

## Do I need to flash before running `ble-scan.py`?

Only if the currently running firmware does not have `bluetooth_proxy` active.

Check the ESPHome web UI at `http://<ip>/` — if `bluetooth_proxy` appears in the component
list, `ble-scan.py` will work without flashing.  If it is absent, flash first:

```bash
esphome run esphome/aquaclean-proxy-wifi.yaml              # USB first flash (WiFi)
esphome run esphome/aquaclean-proxy-eth.yaml               # USB first flash (Ethernet/PoE)
# or OTA if already running ESPHome:
esphome run esphome/aquaclean-proxy-wifi.yaml --device 192.168.0.160
```
