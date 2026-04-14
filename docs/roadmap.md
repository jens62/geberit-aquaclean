# Roadmap

Planned features and improvements.  Not prioritised — order does not imply schedule.

---

## `--scan` CLI command — BLE device discovery

**Goal:** Let users discover the Geberit's BLE MAC address without manually scanning with
external tools, as part of first-time setup.

**Usage (proposed):**

```bash
aquaclean-bridge --scan                              # local BLE adapter
aquaclean-bridge --scan --esphome-host 192.168.0.160 # via ESPHome proxy
```

**Behaviour:**
- Scans for BLE devices for ~10 s
- Filters/highlights any device whose name contains "Geberit" or "AC PRO"
- Prints a table (MAC, RSSI, name) — same format as `esphome/ble-scan.py`
- Prints a ready-to-paste `config.ini` snippet for the discovered device
- Exits — no bridge started

**Local BLE path:** uses `bleak.BleakScanner.discover()` — no new infrastructure needed.

**ESPHome path:** reuses the existing `subscribe_bluetooth_le_raw_advertisements` logic
currently in `esphome/ble-scan.py`.  That logic should move into the package
(e.g. `BluetoothLeConnector.scan()`) so both the CLI and `ble-scan.py` call the
same function.  `ble-scan.py` becomes a thin wrapper or is retired.
Auto-selects the ESPHome path when `[ESPHOME] host` is set in `config.ini`, or
when `--esphome-host` is passed on the command line.

**DRY note:** scan logic in one place inside the package; CLI and `ble-scan.py`
are consumers only.

---

## HACS custom integration (Home Assistant, no MQTT)

**Goal:** Native HA integration installable via HACS — no MQTT broker required.

**Approach:** thin `custom_components/geberit_aquaclean/` adapter in this repo.
A `DataUpdateCoordinator` calls `AquaCleanClient` directly from the existing
pip package — zero protocol code duplicated.  The package remains the single
source of truth for BLE communication.

Two options for the BLE transport layer (see `CLAUDE.md` for token-cost analysis):
- **Option A** (recommended first): use `BluetoothLeConnector` directly, bypassing
  HA's `bluetooth` domain.  Simpler, same code path as standalone bridge.
- **Option B**: integrate with HA's `bluetooth` domain and ESPHome proxy infrastructure
  via `bleak-esphome` + `habluetooth`.  Better HA-native experience, ~4× more effort.

Standalone bridge + MQTT path fully preserved alongside the HA integration.
