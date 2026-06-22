# Test Infrastructure — Mock Geberit Alba

## Hardware

| Component | Details |
|-----------|---------|
| Machine | UTM virtual machine (Ubuntu), hostname `anneubuntuqtm` |
| Host OS | macOS (Apple Silicon) |
| BT adapter | Sabrent USB Bluetooth dongle, passed through to UTM VM |
| BT address | `00:1A:7D:DA:71:13` |
| Chip vendor | CONWISE Technology / CSR family (OUI `00:1A:7D`) |

## Software

- Ubuntu in UTM VM on Apple Silicon Mac
- Python 3.12 in `/home/jens/venv`
- `bluez_peripheral 0.1.7` — requires Python 3.12 patch before first use:
  ```bash
  sudo /home/jens/venv/bin/python3 tools/patch_bluez_peripheral_py312.py
  ```

## Running the Mock

```bash
# mock machine (UTM Ubuntu VM):
sudo /home/jens/venv/bin/python ./mock-geberit-alba.py --mode ble20

# probe machine — direct Bleak, no ESP32 proxy (WORKS):
/home/jens/venv/bin/python ./alba-ble20-probe.py --device 00:1A:7D:DA:71:13

# probe machine — via ESPHome proxy (CURRENTLY BROKEN — see below):
/home/jens/venv/bin/python ./alba-ble20-probe.py --device 00:1A:7D:DA:71:13 --esphome-host 192.168.0.114
```

---

## ESPHome Proxy Path — Status: UNRESOLVED (paused 2026-05-08)

### What works

Direct Bleak (`--device` only, no `--esphome-host`) successfully reaches the mock's
WriteValue D-Bus method and initiates the AriendiSecurity SABM handshake.

### What fails

The ESPHome proxy path (`--esphome-host 192.168.0.114`) fails with:

```
[bluetooth_proxy.connection:353] Error writing char/descriptor for handle 0x3A, status=1
[bluetooth_proxy.connection:353] Error writing char/descriptor for handle 0x3C, status=1
```

`status=1` = `GATT_INVALID_HANDLE`. BlueZ returns ATT Error 0x01 (Invalid Handle) for
both writes. The mock's WriteValue is never called; the probe times out.

### Root cause

The ESP32 (ESPHome `bluetooth_proxy`, NimBLE-based) caches the mock's GATT handle table
in NVS flash, keyed by BDA (`00:1A:7D:DA:71:13`). After any BlueZ restart on the mock
machine, GATT handles are reassigned. The cached handles (0x3A = CCCD for sig_notify,
0x3C = sig_write value) become stale — they no longer exist in BlueZ's current ATT
database.

ESPHome's V3_WITHOUT_CACHE connection type is supposed to clear this cache. Empirically,
it does **not** do so on NimBLE-based ESPHome builds: handles were always 0x3A/0x3C
regardless of how many BlueZ restarts occurred. This was confirmed via `btmon` showing
the ESP32 sending ATT Write Request to 0x3A and 0x3C, and BlueZ responding with ATT
Error: Invalid Handle.

### What was tried

| Attempt | Result |
|---------|--------|
| Python 3.12 `bluez_peripheral` 0.1.7 WRITE flag bug — fixed with `tools/patch_bluez_peripheral_py312.py` | ✅ Fixed; didn't unblock ESPHome path |
| `response=True` in `BluetoothLeConnector.py` for Alba write | ✅ Committed (commit 618e230) |
| Multiple BlueZ restarts on mock machine | ✗ Handles changed; ESP32 kept using cached values |
| ESP32 restart via ESPHome restart button | ✗ NimBLE NVS survives software reset |
| `btmgmt public-addr` on mock machine | ✗ Not supported (Sabrent dongle, status 0x0c) |
| `btmgmt static-addr C0:1A:7D:DA:71:14` | ✗ Set successfully but `bluez_peripheral` still reads public address via D-Bus |
| `bccmd` / `bdaddr` CSR vendor HCI command | Not attempted — tools not packaged in Ubuntu; require BlueZ source compilation |
| `esp32.clear_nvs` OTA | **Not attempted** — identified as correct fix; work paused |

### How to resume

The one untried fix is `esp32.clear_nvs` OTA on the C3 proxy:

1. Add to `esphome/aquaclean-proxy-c3.yaml`:
   ```yaml
   esphome:
     on_boot:
       priority: 800
       then:
         - esp32.clear_nvs:
   ```
2. Flash OTA (`esphome run esphome/aquaclean-proxy-c3.yaml`). Safe — WiFi credentials
   are compiled into the firmware image, not stored in NVS.
3. Start mock: `sudo /home/jens/venv/bin/python ./mock-geberit-alba.py --mode ble20`
4. Probe: `/home/jens/venv/bin/python ./alba-ble20-probe.py --device 00:1A:7D:DA:71:13 --esphome-host 192.168.0.114`
5. If mock logs `[Write→sig_write]` → AriendiSecurity handshake proceeds.
6. After confirming, remove `on_boot: esp32.clear_nvs` and reflash.

---

## Known Issues — MAC Address Change on Sabrent / CONWISE Dongle

For future reference: the Sabrent USB dongle (OUI `00:1A:7D` = CONWISE Technology,
CSR chip family) does not support public address change via standard `btmgmt` commands.

| Command | Result |
|---------|--------|
| `btmgmt public-addr` | `Not Supported` (status 0x0c) |
| `btmgmt static-addr C0:1A:7D:DA:71:14` | Succeeds in btmgmt, but `bluez_peripheral` reads `org.bluez.Adapter1.Address` (public/BD address) via D-Bus — unchanged |
| `bccmd` (CSR-specific NVM tool) | Would work for CSR chips; not packaged on Ubuntu; needs compilation from BlueZ source extras |
| `bdaddr` (BlueZ test utility, sends vendor HCI command) | Same — needs BlueZ source compilation |

---

## Mock Geberit Mera — Adapter

| Component | Details |
|-----------|---------|
| BT adapter | ASUS USB-BT500 |
| BT address | `A0:AD:9F:72:C4:0F` |
| Chip vendor | Realtek (OUI `A0:AD:9F`) |
| Advertising MAC | Real public MAC (not RPA) — confirmed 2026-06-15 |

---

## Trap: BlueZ Bond Record Causes ATT Error 0x05 on All App-Registered GATT CCCDs

### Symptom

Every CCCD read or write for application-registered NOTIFY/INDICATE characteristics returns
ATT Error 0x05 (Insufficient Authentication), even on an unencrypted connection and with
no Service Changed outstanding. BlueZ's own internal service CCCDs (Generic Attribute:
SC CCCD at handle 0x000B, Client Supported Features) are **not** affected — only CCCDs
registered by the Python application via `bluez_peripheral` show Error 0x05.

The iOS/iPad app retries the connection identically each time (no visible change in app
behaviour), because the CCCD enable fails on every attempt for the same reason.

### Root cause

If the iOS/iPad test device previously bonded with the Linux VM adapter (for any reason —
Bluetooth audio, BR/EDR pairing, etc.), BlueZ stores a full bond in:

```
/var/lib/bluetooth/{adapter_mac}/{device_mac}/info
```

The `info` file contains `[IdentityResolvingKey]` (IRK) and `[PeripheralLongTermKey]`
(LTK). When the device reconnects unencrypted, BlueZ:

1. Resolves the RPA to the public address using the stored IRK
2. Finds the bond record → determines the link should be encrypted
3. Enforces bond-level security on **all application-registered GATT attributes**
4. Returns ATT Error 0x05 on any CCCD read/write for those attributes

Additionally, if the bond record contains `[ServiceChanged] CCC_LE=2`, BlueZ has a stored
SC CCCD subscription for this device. After any `bluetoothd` restart (which reassigns GATT
handles), BlueZ will send an SC indication on Connection 1, causing iOS to redo GATT
discovery and delaying or disrupting the onboarding flow.

### How to detect

```bash
# Check for bond records on the adapter:
sudo ls -la /var/lib/bluetooth/A0:AD:9F:72:C4:0F/

# Inspect a specific device's bond:
sudo cat /var/lib/bluetooth/A0:AD:9F:72:C4:0F/88:66:5A:EF:F7:BC/info
# A real bond has [IdentityResolvingKey] and [PeripheralLongTermKey] sections.
# A plain connection cache has only [General] / [ConnectionParameters].
```

Confirmed instance (2026-06-21): iPad `88:66:5A:EF:F7:BC` had bonded with the Linux VM
adapter for Bluetooth audio (BR/EDR). `Authenticated=2` (LE Secure Connections). The
Geberit mock never paired with this iPad; the bond pre-existed from a different use of
the same adapter.

### Fix

**No iOS action needed.** If iOS has already forgotten the device (bond visible on BlueZ
side but absent from iPad Settings → Bluetooth → "Meine Geräte"), iOS will not try to
re-establish encryption. Only the BlueZ side needs to be cleared.

**On the mock (code change — v1.25.9):** clear the bond directory before restarting
`bluetoothd` (the daemon must start with no bond data loaded), and set the adapter
non-pairable immediately after adapter discovery:

```python
# In mock startup — between systemctl stop and systemctl start bluetooth:
import shutil
_bt_dir = Path(f"/var/lib/bluetooth/{adapter_mac}")
for e in _bt_dir.iterdir():
    if e.is_dir() and len(e.name) == 17 and e.name.count(':') == 5:
        shutil.rmtree(e, ignore_errors=True)

# After adapter is ready:
subprocess.run(["btmgmt", "-i", hci_iface, "pairable", "off"], capture_output=True)
```

The real Mera Comfort has no bonding — the mock must match this behaviour.

**Note — bond clearing also eliminates SC on Connection 1:** The bond record's
`[ServiceChanged] CCC_LE=2` entry is a stored SC CCCD subscription. After any
`bluetoothd` restart (which reassigns GATT handles), BlueZ sends an SC indication to
this device on its next connection. Clearing the bond directory removes the subscription
record — SC is never sent, and the SC flush mechanism in the mock becomes a no-op.

**Note — bond clearing also eliminates the SC flush workaround:** The SC flush
coroutine (force-disconnect Connection 1 at 700 ms) was removed in v1.25.9. With bond
clearing, BlueZ starts with no stored `[ServiceChanged] CCC_LE=2` entry — SC is never
sent, so the workaround is a no-op and can be safely removed.

**Related — the SC flush was a red herring (2026-06-21 investigation):** The SC
flush approach was investigated over several sessions because SC always fired on
Connection 1. The btsnoop proved iOS sent ATT_CONFIRMATION within 60 ms of the SC
indication (clearing the BlueZ "changed" flag), yet Error 0x05 still appeared on
0x001B in Connection 2 (54 ms after the ACK) and persisted in Connection 3 with no
SC at all. SC was not the cause — the pre-existing bond was. The SC fired *because*
of the bond's stored SC subscription, not the other way around.

---

## Trap: BlueZ Battery Plugin Reads iOS Battery Level — ATT Error 0x05 → Disconnect

### Symptom

After the bond is cleared, iOS connects and GATT discovery proceeds normally — both
sides exchange service and characteristic lists, CCCDs are written successfully — but
then BlueZ disconnects with HCI reason=0x05 (Authentication Failure).

The btsnoop sequence is always:
```
ATT Read Req  att_handle=0x001B          ← mock/BlueZ reading FROM iOS
ATT Error Resp  handle=0x001B  error=0x05
HCI_CMD Disconnect  reason=0x05          ← local host terminates
```

### Root cause

BlueZ acts as a **GATT client** to iOS (the remote device) and discovers iOS's full
service table — including iOS's Battery Service (0x180F) at handles 0x0019–0x001C.
BlueZ's battery plugin reads the Battery Level at handle 0x001B on iOS. iOS requires
authentication for this characteristic and returns ATT Error 0x05 (Insufficient
Authentication). BlueZ then disconnects.

This happens on **every** mock start if the `bluetoothd` daemon was restarted —
a fresh daemon clears the battery plugin's per-session device cache. The plugin has
no record of the previous failure for this iOS RPA, so it retries the battery read
unconditionally.

The `mock-geberit-alba.py` mock is unaffected because it **never restarts `bluetoothd`**:
the battery plugin's cache persists across mock restarts, and after the first failed
read for a given iOS RPA the plugin skips all subsequent reads for that RPA within
the same daemon session.

### Fix (v1.25.12)

**Do not restart `bluetoothd`** in mock startup. Use `btmgmt unpair` to clear bond
records from BlueZ memory and disk without a daemon restart. The battery plugin cache
is preserved, so after the first failed battery read (if any) for a given iOS RPA,
all subsequent connections in the same `bluetoothd` session proceed without the
battery read:

```python
subprocess.run(["btmgmt", "-i", "0", "unpair", device_mac], capture_output=True)
```

**First connection after a system reboot** (fresh `bluetoothd` with empty cache) may
still drop immediately — the battery plugin retries once. Pressing "Connection 1" a
second time in the app succeeds. All subsequent connection attempts within the same
daemon session succeed without retry.

**BatteryService is still registered** (added v1.25.9) as a precaution in case iOS
reads the mock's own battery level later in the connection, though this direction has
not been observed as the cause of disconnect.

### Apply the descriptor.py Python 3.12 patch

`tools/patch_bluez_peripheral_py312.py` now patches **both** `characteristic.py` and
`descriptor.py`. Run it after every `bluez_peripheral` reinstall:

```bash
sudo /home/jens/venv/bin/python3 tools/patch_bluez_peripheral_py312.py
```

The `descriptor.py` patch fixes the same Python 3.12 `Flag.__and__` regression in
the CCCD descriptor's `Flags` D-Bus property — without it BlueZ may apply incorrect
security policy to app-registered CCCDs.

Verify **both** files are patched:
```bash
grep -c "Python 3.12 Flag.__and__ regression" \
  /home/jens/venv/lib/python3.12/site-packages/bluez_peripheral/gatt/characteristic.py
grep -c "Python 3.12 Flag.__and__ regression" \
  /home/jens/venv/lib/python3.12/site-packages/bluez_peripheral/gatt/descriptor.py
# Both must return 1
```

---

## Connection 1 Protocol — Real Mera Comfort BLE Sequence

Source: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-1.md`

Connection 1 is the button-press onboarding flow. iOS detects `IsButtonPressed=True` in
the manufacturer-specific advertisement data and connects. The device must then drive the
session by sending InfoFrames — iOS will not proceed otherwise.

### Complete GATT sequence (relative time, iOS app v2.14.1)

| Rel. time | Direction | Event |
|-----------|-----------|-------|
| +0.0s | iOS → device | Connects (RPA, unencrypted) |
| +0.2–1.5s | iOS → device | GATT discovery: Read By Type UUID 0x2803 (char declarations), ~18 reads |
| +1.3–1.5s | iOS → device | Write CCCD-A5, CCCD-A6, CCCD-A7: `0x0100` (notify enable) |
| **+1.6s** | **device → iOS** | **9× notify on A6**: `800130140c030003000000003130001200b70800` |
| +1.7s | iOS → device | Write CCCD-A8: `0x0100` |
| +1.7s | iOS → device | GetDeviceIdentification (proc 0x82) |
| +1.8s | iOS → device | GetNodeList |
| +1.9s | iOS → device | GetSOCApplicationVersions (proc 0x81) |
| +2.3–2.4s | iOS → device | GetFirmwareVersionList (proc 0x0E): nodes [1–12,14] then [15] |
| +2.5s | device → iOS | notify on A8: `160b303716000c303712000e30371b0000000000` |
| +2.7–3.2s | iOS → device | SubscribeNotif_0x11 (×4) and 0x13 (×4) |
| +5.3s | iOS → device | Read Device Name (UUID 0x2A00) → "ro" |
| +5.3–12.1s | — | **6.8-second silence** — iOS waits, device sends nothing |
| +12.1–13.6s | iOS → device | Proc 0x07 to 10 nodes: [04, 02, 05, 03, 09, 01, 00, 0d, 08, 07] |
| +12.1–13.6s | device → iOS | notify on A5 after each node query (A5 InfoFrames) |
| +13.7s+ | iOS → device | Profile settings init (proc 0x0A), Common settings (proc 0x51), SPL, GetFilterStatus |

### Key trigger: A6 InfoFrame burst

iOS does **not** proceed to GetDeviceIdentification until it receives at least one
notify on A6. The real device sends the burst immediately when CCCD-A6 is written —
on the same BLE connection event. The frame is repeated 9× (6 after CCCD-A5/A6/A7,
3 more after CCCD-A8):

```
80 01 30 14 0c 03 00 03 00 00 00 00 31 30 00 12 00 b7 08 00
```

### The "hold the button" mechanics

The app instructs the user to hold the toilet button. In BLE terms this maps to:

1. **Advertisement with `IsButtonPressed=True`** → iOS connects
2. **iOS writes CCCDs → device sends A6 InfoFrame burst** → iOS proceeds with identification
3. **6.8-second gap** → iOS waits (physical hold confirmation); device is completely silent
4. **Proc 0x07 to 10 nodes** → iOS polls each node for button state; device responds with
   A5 InfoFrames confirming the button is still pressed
5. **iOS proceeds** with profile and common settings init

A single web UI "Button pressed" click is sufficient to start the flow — the physical
hold duration is reflected in the mock by responding correctly to proc 0x07 (if the mock
sends the right A5 InfoFrames, iOS concludes the button is held and proceeds).

### Mock implementation gaps (v1.25.12, 2026-06-22)

| Gap | Symptom | Fix needed |
|-----|---------|------------|
| No A6 InfoFrames after CCCD write | iOS waits indefinitely after GATT setup | Send 9× A6 frame after CCCD-A6 is enabled |
| Wrong InfoFrame format | `_build_info_frame()` returns `0x91 0x01 0x00…`; real frame starts `0x80` | Fix `_build_info_frame()` |
| Proc 0x07 returns empty | iOS gets no A5 InfoFrames; button state unconfirmed | Implement proc 0x07 response with A5 notify |

Current test result (v1.25.12, attempt 2, 2026-06-22): iOS connects, completes GATT
discovery, reads "ro" — then waits 17 s for A6 InfoFrames that never arrive, times out.
