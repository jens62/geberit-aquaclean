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
