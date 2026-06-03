# HACS Integration — Planned Architecture

## Goal

Native HA integration installable via HACS. No MQTT broker required.
Standalone bridge + MQTT fully preserved alongside.

**Structure (both options):**
- `hacs.json` at repo root (`"category": "integration"`)
- `custom_components/geberit_aquaclean/` — thin HA adapter only
- `manifest.json` `requirements` points to this same repo's pip package → zero protocol code duplicated
- `config_flow.py` replaces `config.ini` for the HA context
- `coordinator.py` (`DataUpdateCoordinator`) replaces MQTT — calls `AquaCleanClient` directly
- Entity files (`sensor.py`, `switch.py`, etc.) — wrappers around coordinator data

---

## Option A — bypass HA BLE, use `BluetoothLeConnector` directly (recommended first)

`coordinator.py` instantiates `BluetoothLeConnector` exactly as the standalone bridge does.

**Pros:** same battle-tested code path; low risk; ~740 lines of new glue code.
**Cons:** adapter conflict possible if HA also uses local BLE adapter; not HA-native.
**Estimated cost:** ~25–40K tokens. 1–2 sessions.

## Option B — integrate with HA's `bluetooth` domain

Register as a `bluetooth` passive scanner consumer. HA delivers `BLEDevice` objects via
scan callbacks; new adapter layer maps them to `AquaCleanClient`.

**Pros:** fully HA-native; no BLE adapter conflict; auto-discovery flow.
**Cons:** ~4× more effort (~1,500–2,500 lines); `habluetooth` inside HA behaves differently.
**Estimated cost:** ~80–150K tokens. 3–5 sessions.

**Recommendation:** implement Option A first. The coordinator/entity structure is identical
in both options — the only difference is the transport behind `coordinator.py`.

---

## Planned: zeroconf/mDNS service discovery for ESPHome BLE proxies

**Goal:** auto-discover ESPHome BLE proxies via mDNS so `[ESPHOME] host` does not need
to be hardcoded.

Discovery logic already exists in `aquaclean-connection-test.py` (Step 0) — must reuse it (DRY).

| Topic | Detail |
|-------|--------|
| Protocol | ESPHome native API advertises `_esphomelib._tcp.local` via mDNS |
| Library | `zeroconf` (already a transitive dependency via aioesphomeapi) |
| Multiple proxies | Use first matching `esphome_name_filter`; warn and use first alphabetically if multiple |
| Config changes | `[ESPHOME] host` becomes optional; add optional `[ESPHOME] name_filter` key |
| Runtime failover | On E2005, re-run mDNS discovery and retry with next available proxy |

Implementation order:
1. Extract mDNS scan logic from `aquaclean-connection-test.py` into `BluetoothLeConnector`
2. Wire into `_ensure_esphome_api_connected()`: if `esphome_host` is blank, call helper
3. Add `name_filter` config key + validation
4. Add runtime failover path in E2005 handler
5. Update `aquaclean-connection-test.py` Step 0 to call the shared helper

### HACS config flow integration (zero-config installation)

Connection-test tool steps map to a HACS config flow wizard:

```
Step 1: auto-discover ESPHome proxies via mDNS
  → found 1:        pre-fill host, skip to step 3
  → found multiple: dropdown "Select ESPHome proxy"
  → found none:     show manual host field (local BLE path)

Step 2 (if ESPHome host): test API connection + BLE adapter
  → spinner → pass/fail with inline hint

Step 3: scan for Geberit device (10s spinner)
  → found 1:        pre-fill MAC → continue to Step 3b
  → found multiple: dropdown
  → not found:      "Ensure device is powered and not connected to the Geberit app"

Step 3b: GATT discovery + UUID probe (5s spinner)
  → standard UUIDs confirmed / non-standard saved / unsupported → open issue

Step 4: GetDeviceIdentification
  → success: confirmation screen showing device name + serial
```

---

## Dynamic UUID support

Different Geberit models may advertise a different Service UUID.
The `--dynamic-uuids` flag in the connection test already handles this via
instance-attribute shadowing on `BluetoothLeConnector`.

**HACS implementation:** run GATT discovery during config flow Step 3b, store discovered
UUIDs in the config entry alongside the MAC. Coordinator injects them when constructing
`BluetoothLeConnector` before each poll.

```python
config_entry.data = {
    "mac": "38:AB:41:2A:0D:67",
    "esphome_host": "aquaclean-proxy.local",
    "uuid_service":  "3334429d-...",
    "uuid_write_0":  "...",
    "uuid_read_0":   "...",
}
```

| Piece | Status |
|---|---|
| GATT service table reading | ✅ `check_gatt_services()` in connection-test tool |
| UUID injection into connector | ✅ `_probe_via_bridge_stack()` in connection-test tool |
| `BluetoothLeConnector` instance UUID override | ✅ Python attr shadowing already works |
| Config flow running GATT discovery | ❌ new work |
| Config entry storing discovered UUIDs | ❌ new work |
| Coordinator injecting stored UUIDs | ❌ new work |

---

## Planned: `--scan` CLI command

`aquaclean-bridge --scan` (and `--scan --esphome-host <ip>`) for BLE device discovery
at first-time setup. Scan logic belongs inside the package (`BluetoothLeConnector.scan()`).
`ble-scan.py` becomes a thin wrapper or is retired. DRY: one scan implementation.
See `docs/roadmap.md` for full spec.
