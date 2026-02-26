# Bluetooth Troubleshooting — Local BLE Path

This document covers what we learned diagnosing a severe BLE performance regression
on a Raspberry Pi (Kali Linux) after `apt upgrade`. It applies whenever the standalone
bridge uses the **local bleak path** (no ESPHome proxy).

---

## TL;DR — Root Cause and Fix

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| BLE connect takes 25 s instead of ~1.5 s | `bleak 2.1.1` regresses `le-connection-abort-by-local` retry behaviour on BCM4345C0 | Pin bleak to `>=2.0.0,<2.1` in `pyproject.toml` |

**ESPHome proxy is always the recommended production path.** It bypasses the local
BLE stack entirely, gives consistent <1 s polls, and is immune to BlueZ/bleak/firmware
regressions. Use the local bleak path only when an ESP32 proxy is unavailable.

---

## Hardware — Raspberry Pi BT Chip

The Raspberry Pi (3B+, 4, 5) uses the **Cypress/Infineon CYW43455 (BCM4345C0)**
Bluetooth chip. All diagnostics and firmware notes below apply to this chip.

---

## How to Check Versions

### BlueZ (system Bluetooth daemon)
```bash
bluetoothd --version
```

### bleak (Python library — check the venv, NOT system Python)
```bash
# In the bridge's venv:
pip show bleak

# Or with explicit interpreter:
/home/kali/venv/bin/python -m pip show bleak

# System Python bleak (may differ from venv):
apt show python3-bleak | grep Version
/usr/bin/python3 -c "from importlib.metadata import version; print(version('bleak'))"
```

> **Important:** The bridge installs into a venv. The system `python3-bleak` package
> and the venv's `bleak` are independent. Always check the venv version when
> diagnosing bridge behaviour.

### BT chip and firmware — at runtime
```bash
# Chip manufacturer and BT spec version
hciconfig -a | grep -i "BD Address\|Manufacturer"
sudo btmgmt info

# Firmware file loaded at boot (check dmesg)
dmesg | grep -i "BCM4345\|hcd\|brcm\|bluetooth.*firm"

# Firmware version string inside the .hcd file
strings /lib/firmware/brcm/BCM4345C0.hcd | grep -i "BCM\|version"
```

### Firmware packages
```bash
dpkg -l firmware-brcm80211 linux-firmware pi-bluetooth 2>/dev/null | grep ^ii
dpkg -l 'firmware-*' | grep ^ii
```

---

## The Regression Diagnosed (Feb 2026)

### Symptoms
- `apt upgrade` updated BlueZ to **5.84** and bleak (in venv) to **2.1.1**
- BLE connection time jumped from ~1.5 s → ~25 s
- Log showed rapid `le-connection-abort-by-local` retries inside `BleakClient.connect()`
- Bridge still worked but was very slow; poll interval of 10.5 s was marginal

### Investigation

| Check | Result |
|-------|--------|
| `bluetoothd --version` | 5.84 (updated by apt) |
| venv `pip show bleak` | 2.1.1 (updated by apt via pip) |
| `strings BCM4345C0.hcd \| grep BCM` | `BCM43455 37.4MHz Raspberry Pi 3+-0190` (v0190, ~2018) |
| `hciconfig -a \| grep Manufacturer` | Cypress Semiconductor (305) |
| `dmesg` BT firmware line | `BCM4345C0 'brcm/BCM4345C0.hcd' Patch` → build 0382 |
| WiFi firmware date | Nov 2021 (from `brcmfmac` driver log) |

### Red herrings ruled out

**Firmware v0190** — suspected first because it is old (~2018). Attempted:
- `sudo apt install firmware-brcm80211` → already at `20251111-1`, firmware unchanged
- `sudo apt install pi-bluetooth` → pulls `bluez-firmware` (RPi-Distro), still v0190
- `strings BCM4345C0.hcd` confirmed v0190 before and after

**Conclusion on firmware:** v0190 is the latest available from all official sources
(Debian, RPi-Distro). It is NOT the cause — proven by the confirmation test below.

**`/etc/bluetooth/main.conf` tuning** — `MinConnectionInterval` /
`MaxConnectionInterval` were commented out (defaults). These control post-connection
parameter negotiation, not link establishment. Tuning them would not fix
`le-connection-abort-by-local` which happens before the connection is established.

### Confirmation test

Cloned and ran `v2.0.0` tag **on the same hardware** (same BlueZ 5.84, same firmware v0190):

```bash
git clone -b v2.0.0 https://github.com/jens62/geberit-aquaclean.git
```

v2.0.0 uses system `python3-bleak` **2.0.0**:

```bash
apt show python3-bleak | grep Version   # → 2.0.0-3
/usr/bin/python3 -c "from importlib.metadata import version; print(version('bleak'))"  # → 2.0.0
```

| Metric | bleak 2.0.0 | bleak 2.1.1 |
|--------|------------|------------|
| BLE connect time | ~1.8 s | ~25 s |
| Poll time | ~500 ms | ~500 ms (if connected) |
| `le-connection-abort-by-local` retries | few / none | many (~100+) |

**Root cause confirmed: bleak 2.1.1 regresses retry behaviour for this chip + BlueZ combination.**

---

## Fix

Pin bleak in `pyproject.toml`:

```toml
"bleak>=2.0.0,<2.1",  # 2.1.x regresses le-connection-abort-by-local on BCM4345C0 + BlueZ 5.84
```

Downgrade in existing venv:

```bash
pip install "bleak>=2.0.0,<2.1"
```

---

## Recovery Steps (when BLE connect is slow or failing)

Try in this order:

1. **Check bleak version first** — this is the most common cause after an upgrade:
   ```bash
   pip show bleak   # must be 2.0.x, not 2.1.x
   ```

2. **Clear stale BlueZ device cache:**
   ```bash
   bluetoothctl remove 38:AB:41:2A:0D:67   # use your device_id from config.ini
   ```
   BlueZ caches connection state per device. Stale entries cause extra retries.

   **Live recovery confirmed (2026-02-26):** The bridge shows a BLE error in the webapp
   (E0003 hint). Running `bluetoothctl remove <MAC>` from a terminal while the bridge
   is running causes immediate recovery — **no bridge restart needed**. The polling loop
   retries and succeeds on the next cycle.

   **Why this is not a UI button:** Only relevant for the local BLE path (no ESPHome proxy).
   In the typical ESPHome proxy setup, BlueZ on the host is not involved and this command
   does nothing useful. It is also Linux/BlueZ-specific and would require running a
   privileged subprocess with the device MAC address. For ESPHome proxy users, the
   "Restart AquaClean Proxy" button (ESP32 reboot) is the equivalent recovery action.

3. **Restart Bluetooth:**
   ```bash
   sudo systemctl restart bluetooth
   ```

4. **Reset the adapter:**
   ```bash
   sudo hciconfig hci0 down && sudo hciconfig hci0 up
   ```

5. **Power cycle the Geberit** (unplug for 10 seconds).

6. **Switch to ESPHome proxy** — always <1 s, no BlueZ dependency.

---

## When to Revisit the bleak Pin

The pin `bleak>=2.0.0,<2.1` in `pyproject.toml` should be revisited when a new bleak
minor version (2.2.x, 2.3.x, …) is released. GitHub Dependabot will open a PR automatically.

**Before merging any bleak upgrade PR:**

1. Install the candidate version in the venv:
   ```bash
   pip install "bleak==X.Y.Z"
   pip show bleak   # confirm version
   ```

2. Start the bridge in on-demand mode with local Bluetooth (no ESPHome proxy):
   ```bash
   python -m aquaclean_console_app.main --mode service
   ```

3. Trigger a poll and measure BLE connect time in the log:
   ```
   BLE connection: last_connect_ms = ???
   ```
   Or time it directly:
   ```bash
   time curl -s http://localhost:8080/data/state | python -m json.tool
   ```

4. **Pass criteria:** BLE connect time consistently **< 3 seconds**.
5. **Fail criteria:** Connect time > 10 s, or repeated `le-connection-abort-by-local`
   messages in the log → stay on the current pin and close the Dependabot PR.

6. If the new version passes, widen the pin in `pyproject.toml` and update
   this document.

---

## Key Facts for Future Reference

- **BCM4345C0 firmware v0190** is the latest available from official packages — no
  update path exists through standard Debian/Kali/RPi package management.
- **BlueZ 5.84 + bleak 2.0.0 + firmware v0190** = fast and stable (~1.8 s connect).
- **BlueZ 5.84 + bleak 2.1.1 + firmware v0190** = slow (25 s connect, many retries).
- The `le-connection-abort-by-local` retry loop is inside `BleakClient.connect()` —
  it is not visible at the application layer until the overall timeout fires.
- `MinConnectionInterval`/`MaxConnectionInterval` in `/etc/bluetooth/main.conf`
  affect parameter negotiation **after** connection — irrelevant to link establishment failures.
- ESPHome proxy path never touches BlueZ for the BLE connection itself — immune to
  all of the above.
