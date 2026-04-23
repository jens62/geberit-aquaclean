# Tested Devices — Compatibility Matrix

This document lists every Geberit AquaClean device that has been confirmed to work
(or not work) with this bridge, along with the GATT profile variant used.

---

## Confirmed Working

| Device | Serial / Prefix | GATT Profile | BLE MAC | Tested By | Notes |
|--------|----------------|--------------|---------|-----------|-------|
| AquaClean Mera Comfort | HB2304EU298413 | Standard (`3334429d`) | `38:AB:41:2A:0D:67` | Developer (primary device) | All features tested; BLE logs in `local-assets/Bluetooth-Logs/` |

---

## Partial / In Progress

| Device | Serial / Prefix | GATT Profile | BLE MAC | GitHub Issue | Status |
|--------|----------------|--------------|---------|--------------|--------|
| AquaClean Alba | Unknown | Variant A (`0000fd48` + `559eb` chars) | `E4:85:01:CD:B0:08` | [#17](https://github.com/jens62/geberit-aquaclean/issues/17) | GATT discovery confirmed; write-type fix applied (commit 7433b6b); **protocol probe fails** (`BLEPeripheralTimeoutError` — writes succeed, no notify response). BLE traffic capture from Geberit Home App needed. |

---

## Notes on GATT Profile Variants

See `docs/developer/gatt-uuid-variants.md` for full GATT table details and UUID mappings.

| Profile | Service UUID | Known devices |
|---------|-------------|---------------|
| Standard | `3334429d-90f3-4c41-a02d-5cb3a03e0000` | AquaClean Mera Comfort (confirmed) |
| Variant A | `0000fd48-0000-1000-8000-00805f9b34fb` | AquaClean Alba (confirmed via issue #17) |

---

## Adding a New Device

If you have a device not listed above and want to test compatibility:

1. Run `tools/aquaclean-connection-test.py --dynamic-uuids` and paste the full output into a GitHub issue
2. The GATT table in the output will identify which profile variant your device uses
3. If the connection test passes, the bridge will work; if it fails, the output contains the exact failure point

The `--dynamic-uuids` flag handles Variant A automatically if the frame protocol is the same.
For devices where the protocol probe also fails, a BLE traffic capture from the Geberit Home App is needed — see `docs/developer/ble-traffic-capture.md`.
