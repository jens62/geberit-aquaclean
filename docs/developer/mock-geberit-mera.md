# Mock Geberit AquaClean Mera Comfort BLE Peripheral

**File:** `tools/mock-geberit-mera.py`

Simulates the AquaClean Mera Comfort BLE peripheral on Linux (BlueZ). Supports the
full Geberit Home App "Connection 1" onboarding flow — button-press ceremony, A6
InfoFrame burst, and procedure responses (GetDeviceIdentification, GetFirmwareVersionList,
GetSystemParameterList, etc.).

No encryption, no SMP — the Mera Comfort BLE link layer is unencrypted (confirmed from
real-device packet capture).

---

## Requirements

- Linux with BlueZ ≥ 5.50 (Bluetooth daemon running)
- Python packages: `dbus-next`, `bluez_peripheral`, `aiohttp`
- Run as root (D-Bus system bus access)
- BlueZ experimental features enabled (`Experimental=true` in `/etc/bluetooth/main.conf`)

```bash
pip install dbus-next bluez_peripheral aiohttp
sudo /home/jens/venv/bin/python tools/mock-geberit-mera.py [--port 8766]
```

---

## Test session setup

### Step 1 — Start btmon (mock machine)

Captures all BLE HCI events to a timestamped btsnoop file. Run in a dedicated terminal
**before** starting the mock:

```bash
sudo btmon -w ~/mock-geberit-mera_btmon_$(date +%F_%H-%M).btsnoop
```

### Step 2 — Start the mock (mock machine)

```bash
sudo /home/jens/venv/bin/python tools/mock-geberit-mera.py 2>&1 \
  | tee ~/mock-geberit-mera_$(date +%F_%H-%M).log
```

Wait for `--- Mera Comfort Mock Active ---` and note the adapter MAC address.

### Step 3 — Trigger Connection 1

1. Open the Geberit Home App (iOS or Android).
2. Wait for the device to appear in the scan list (mock advertises with `IsButtonPressed=False`).
3. Open the mock web UI at `http://<vm-ip>:8766/` and press **"Press Button"**.
4. The advertisement updates to `IsButtonPressed=True` — the app detects this and connects.

---

## GATT profile

Single vendor service `3334429d-90f3-4c41-a02d-5cb3a03e0000`, 7 characteristics:

| UUID suffix | Properties | Role |
|-------------|-----------|------|
| `...a13e0000` | WRITE_WITHOUT_RESPONSE | Procedure requests (app → mock) |
| `...a23e0000` | WRITE_WITHOUT_RESPONSE | Multi-frame continuation writes |
| `...a53e0000` | NOTIFY | A5 — primary response channel |
| `...a63e0000` | NOTIFY | A6 — CONS continuation + Connection 1 trigger |
| `...a73e0000` | NOTIFY | A7 — CONS continuation |
| `...a83e0000` | NOTIFY | A8 — CONS continuation |
| `00003a2b-...` | READ | Button-state probe — returns `b"ro"` |

The real Mera Comfort handle map (from nRF52840 capture) is at
`docs/developer/mera-home-app-onboarding.md`.

---

## Connection 1 flow

The Geberit Home App "Connection 1" onboarding requires this exact sequence:

1. App scans BLE advertisements, detects `IsButtonPressed=True` in manufacturer data.
2. App connects and performs GATT discovery — finds all 7 characteristics.
3. App writes CCCD on A6 (enables notify).
4. **Mock sends 9× A6 InfoFrame burst** (`800130140c030003000000003130001200b70800`) —
   this is the Connection 1 trigger; the app will not call GetDeviceIdentification until
   it receives at least one A6 notify.
5. App calls GetDeviceIdentification (proc `0x82`), GetFirmwareVersionList (`0x0E`), and
   the standard polling procedures.

The burst fires automatically 4 seconds after connection when `IsButtonPressed` was `True`
at connect time. The 4 s delay avoids a race where the mock sends before the app has
written the A6 CCCD.

**Source:** nRF52840 capture of iOS app v2.14.1 against real Mera Comfort
(`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/`).

---

## Battery plugin behavior (iOS only)

Two independent mechanisms interact when an iOS device connects for the first time in
a bluetoothd session:

1. **BlueZ battery plugin (GATT client)** — BlueZ immediately reads Battery Level
   (handle `0x001B`) from the *connected iOS device's* GATT server. iOS returns
   `0x05` (Insufficient Authentication). BlueZ then tries to initiate pairing.
   - `pairable=on` (correct state): pairing fails gracefully — "Pairing Not Supported"
     in ~5 ms (no agent registered) — **connection continues**.
   - `pairable=off` (wrong state): BlueZ cannot start pairing → immediately disconnects
     with HCI reason `0x05` → **first connection killed at ~3 s**.

2. **iOS GATT client reads mock Battery Level** — iOS reads the mock's own Battery
   Level characteristic during GATT discovery. The mock registers a `BatteryService`
   (UUID `0x180F`) that returns `bytes([100])` without authentication, so iOS sees a
   clean value and does not disconnect.

**Required setup (handled by mock at startup):** `btmgmt pairable on` (v1.29.0+).
Older mock versions called `btmgmt pairable off`, and that state persisted across
restarts because the mock no longer restarts bluetoothd. Without explicitly resetting
to `pairable=on`, the battery plugin kills the first connection and iOS caches a
partial GATT table (missing A6–A8) that prevents CCCD-A6 from ever being written.

**Second connect with the same RPA**: battery plugin skips already-probed devices →
fully benign, connection proceeds normally.

Do **not** add `DisablePlugins = battery` — it is not needed.

---

## Known issues

### bluez_peripheral 0.1.7 — self-include bug (fixed in mock v1.27.0)

**Symptom:** GATT discovery finds only the first 2 characteristic declarations (3a2b +
A5); A6, A7, A8, A1, A2 have no declarations — they are invisible to
`ATT Read By Type uuid=0x2803`. The app cannot find A6 to write its CCCD and proceeds
to disconnect within 2 seconds.

**Root cause:** `bluez_peripheral.gatt.service.Service.Includes` property unconditionally
appended `self._path` to the return list:

```python
# bluez_peripheral 0.1.7 — BUGGY
def Includes(self) -> "ao":
    paths = []
    for service in self._includes:
        if not service._path is None:
            paths.append(service._path)
    paths.append(self._path)   # ← always appends own path → self-include
    return paths
```

BlueZ creates an Include Declaration (ATT type `0x2802`) at handle `0x00A6` for the
self-include. A handle consumed by an Include Declaration cannot also be a Characteristic
Declaration (`0x2803`), so BlueZ only generates declarations for the first 2 chars;
the remaining 5 get value attributes and auto-CCCDs but are invisible to char discovery.

**Diagnosis confirmed from btsnoop** (Android capture, 2026-06-22): `ATT Read By Type
uuid=0x2802` returns value `a5 00 b8 00` at handle `0x00A6` (service's own range —
start `0x00A5`, end `0x00B8`). `ATT Read By Type uuid=0x2803` with MTU=517 starting at
`0x00A8` returns only the A5 declaration at `0x00A9` — no further char decls exist.

**Fix (mock v1.27.0):** `MeraService` overrides `Includes` to return an empty list:

```python
@dbus_property(PropertyAccess.READ)
def Includes(self) -> "ao":  # type: ignore
    # bluez_peripheral 0.1.7 bug: base class unconditionally appended self._path,
    # creating a self-include declaration that displaces A6–A8/A1/A2 char declarations.
    return []
```

This override is in the mock source and survives venv reinstalls without any manual
library patching.

**Emergency VM patch** (if running an older mock version without the override):

```bash
# Remove the unconditional self._path append from the installed library
sudo sed -i '/paths.append(self._path)/d' \
  /home/jens/venv/lib/python3.12/site-packages/bluez_peripheral/gatt/service.py

# Verify: only the line inside the for-loop remains (contains 'service._path')
grep 'paths.append' \
  /home/jens/venv/lib/python3.12/site-packages/bluez_peripheral/gatt/service.py
```

---

## btmon correlation tool

`tools/analyze-btmon-mock.py` correlates a btmon btsnoop capture with a mock log
to produce a unified timeline. Auto-detects the clock offset between btmon and mock
by matching ATT Write Command payloads.

```bash
/Users/jens/venv/bin/python tools/analyze-btmon-mock.py \
  path/to/capture.btsnoop path/to/mock.log
```

Flags: `--att-only`, `--no-color`, `--summary-only`, `--gap MS`, `--offset-ms FLOAT`

Always use this tool for btsnoop analysis — do not write ad-hoc decoders.

---

## Current status — mock v1.29.0 (2026-06-22)

| Feature | Status |
|---------|--------|
| BLE advertising with `IsButtonPressed` toggle | ✅ |
| All 7 char declarations visible (Includes fix) | ✅ v1.27.0 |
| A6 InfoFrame burst (Connection 1 trigger) | ✅ v1.26.0 |
| Battery plugin survives first connection | ✅ v1.25.11 + v1.29.0 |
| `IsButtonPressed` latched until burst sent | ✅ v1.28.0 |
| GetDeviceIdentification (proc `0x82`) | ✅ |
| GetFirmwareVersionList (proc `0x0E`) | ✅ |
| GetSystemParameterList (proc `0x0D`) | ✅ |
| GetDeviceInitialOperationDate (proc `0x86`) | ✅ |
| GetFilterStatus (proc `0x59`) | ✅ |
| Web UI button press + live state | ✅ |
| Full Connection 1 → GetDeviceIdentification flow | ⏳ pending iOS/Android test on v1.29.0 |
