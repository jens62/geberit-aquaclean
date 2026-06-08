# BLE Advertisement — Model Detection Without GATT Connection

**Date:** 2026-06-08  
**Source:** `AcDeviceTypeHelper.cs`, `AquaCleanProduct.cs` from Geberit Home v2.14.1 app.

---

## Key finding

**No GATT connection is made during the device scan.** When the Geberit Home App shows
"Add Device" and lists found devices by name ("AquaClean Sela", "AquaClean Mera Comfort",
etc.), the model name is determined entirely from the **BLE advertisement
manufacturer-specific data payload** — no pairing, no GATT connection, no `GetDeviceIdentification`
(proc 0x82) call at that point.

The model is identified from a 5-character **article number prefix** embedded in the
advertisement payload. `GetDeviceIdentification` is called only after the user taps the
device to connect.

---

## Manufacturer-specific data payload format

The payload is present in the BLE advertisement as manufacturer-specific data
(`AdvertiseInfoType.ManufacturerSpecificData`). Three variants exist:

### 3-byte variant (recovery mode / boot state)

| Byte | Content |
|------|---------|
| 0 | (unused in model detection) |
| 1–2 | RS firmware number chars (ASCII digits) |

Article number is NOT present → device treated as `IsInRecoveryMode = true`.

### 8-byte variant (standard)

| Byte | Content |
|------|---------|
| 0 | State byte A: `0xAA` (170) = `IsEmergencyConnectPermitted` |
| 1 | (unused) |
| 2 | State byte B: `0x01` = `IsButtonPressed` |
| 3–5 | First 3 ASCII chars of article prefix (e.g. `'1','4','6'`) |
| 6–7 | Last 2 ASCII chars of article prefix (e.g. `'2','2'`) |

Article number assembled as: `chars[3..5] + "." + chars[6..7]` → e.g. `"146.22"`

### 11-byte variant (standard + firmware version)

Same layout as 8-byte, plus:

| Byte | Content |
|------|---------|
| 8 | Separator (unused in parsing) |
| 9–10 | RS firmware number chars (ASCII digits) |

### SensorState computation

```
SensorState bit 0 = (data[2] == 0x01)  → IsButtonPressed
SensorState bit 1 = (data[0] == 0xAA)  → IsEmergencyConnectPermitted
```

This is consistent with the `SensorState` description in `.claude/rules/ble-protocol.md`.

---

## Article number prefix → model mapping

From `AcDeviceTypeHelper.GetDeviceType(articleNumber, serialNumber)`.
Matching is prefix/substring: a table entry matches if it is contained in the article number
or the article number is contained in it.

**`DeviceSeries` is hardcoded to 248 (`AquacleanOld`)** for all standard AC protocol
devices. The `DeviceVariant` is what distinguishes them.

| Article prefix(es) in advertisement | `AquacleanOldVariant` | Model shown in app |
|-------------------------------------|-----------------------|--------------------|
| `146.22`, `243.64`, `243.71` | `AcSela` | AquaClean Sela |
| `146.21` (serial number does **not** start with `'G'`) | `AcMeraComfort` | AquaClean Mera Comfort |
| `146.21` (serial number starts with `'G'`) | `AcMeraClassic` | AquaClean Mera Classic |
| `146.20` | `AcMeraClassic` | AquaClean Mera Classic |
| `146.19`, `146.24` | `AcMeraFloorstanding` | AquaClean Mera Floorstanding |
| `146.07`, `146.09`, `146.096`, `146.100`,<br>`243.36`, `243.46`, `243.47`, `243.514`, `243.515` | `AcTumaClassic` | AquaClean Tuma Classic |
| `146.098`, `243.367` | `AcTumaComfort` | AquaClean Tuma Comfort |
| `146.27`, `146.29`, `146.102`, `243.29`,<br>`243.48`, `243.49`, `243.516`, `243.517`,<br>`146.098`, `243.367` | `AcTumaComfort` | AquaClean Tuma Comfort |
| `146.30` | `AcCamaTestset` | AquaClean Cama Testset |
| `146.34` | `AcCama` | AquaClean Cama |
| (no match) | `Unknown` | shown as unsupported |

### Tiebreaker: AcMeraComfort vs AcMeraClassic (both `146.21`)

Both share the same article prefix. The tiebreaker is the **serial number first character**:
- Serial starts with `'G'` → `AcMeraClassic`
- Serial starts with `'H'` or anything else → `AcMeraComfort`

During a BLE scan (before connection), the serial number is unknown (null) → defaults to
`AcMeraComfort`. The correct classification requires `GetDeviceIdentification` (proc 0x82)
or a cloud lookup after connection.

### Real-world example

msperl's AcSela: SAP number `146.22x.xx.1` →
advertisement contains article prefix `"146.22"` →
`AcDeviceTypeHelper.GetDeviceType("146.22")` → `AcSela` →
app shows "AquaClean Sela" **before any connection**.

---

## Alba — different advertisement entirely

The Alba (series 250, `DeviceSeries.Aquaclean`) uses the Arendi BLE20 protocol with a
completely different GATT UUID set and advertisement format. It is always distinguishable
from AquacleanOld devices at scan time purely from the BLE service UUID.
See `docs/developer/alba-ble20-protocol.md` for Alba advertisement details.

---

## Same GATT UUIDs across all AquacleanOld variants

All standard AC protocol devices (Mera Comfort, Sela, Tuma, Cama) share the **same GATT
service and characteristic UUIDs**. The bridge's Mera Comfort UUID set connects to an
AcSela without modification — confirmed by msperl's HACS log
(`local-assets/HACS-Logs/msperl/geberit_aquaclean.txt`, 2026-06-07).

---

## Implication for the bridge

The 5-char article number prefix is available in the BLE advertisement, **before any GATT
connection**. The bridge (and HACS coordinator) could determine `DeviceVariant` at scan
time using the same lookup table as `AcDeviceTypeHelper`, without waiting for
`GetDeviceIdentification`.

**Practical use:** expose the detected model variant as a coordinator property immediately
after the first BLE scan, so that model-specific HACS entity registration (e.g. AcSela
orientation light entities) can happen at `async_setup_entry` time rather than requiring
a first-poll round-trip.

See `docs/roadmap.md` → "HACS: model-aware entity visibility" for the planned implementation.
