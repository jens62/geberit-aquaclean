# Mera Comfort ↔ Alba Protocol Mapping

Both devices expose the same logical features, but via entirely different mechanisms:
- **Mera Comfort**: structured RPC procedures (`GetStatisticsDescale` proc 0x45, SPL param array, `GetStoredProfileSetting` proc 0x53/0x54)
- **Alba**: individual DpIds (BLE 2.0), each readable/writable independently via the inventory-based `read(dp_id)` / `write(dp_id, value)` API

---

## Device identification

`GET /info` returns a `DeviceIdentification` struct built from different sources on each device type.

| `/info` field | Mera Comfort source | Alba DpId | Name | Notes |
|---|---|---|---|---|
| `sap_number` | proc 0x82 (12-byte string) | 371 | SALES_PRODUCT_SAP_NUMBER | Toilet article number — the consumer-facing product identifier |
| `serial_number` | proc 0x82 (20-byte string) | 369 | SALES_PRODUCT_SERIAL_NUMBER | Full product serial printed on the box |
| `production_date` | proc 0x82 (10-byte date string) | 3 | DEVICE_PRODUCTION_DATE | TimeStampUtc on Alba; formatted as `YYYY-MM-DD` (UTC) |
| `description` | proc 0x82 (40-byte string, e.g. `"AcMeraComfort"`) | 0 + 1 | DEVICE_SERIES + DEVICE_VARIANT | Derived via `get_full_name(series, variant)` → e.g. `"Aquaclean Alba"` |
| `initial_operation_date` | proc 0x86 GetDeviceInitialOperationDate | — | — | Not available on Alba; field omitted from response |

### Additional Alba DpIds with no Mera Comfort equivalent

| DpId | Name | kstr value | Notes |
|---|---|---|---|
| 4 | DEVICE_SAP_NUMBER | `828.860.00.A` | BLE board article number — also exposed via BLE DIS Model Number |
| 2 | DEVICE_NUMBER | 93136 | BLE board serial — also exposed via BLE DIS Serial Number |
| 313 | SALES_SAP_NUMBER | `245.832.00.1` | Product line article number |
| 236 | UNIQUE_DEVICE_NUMBER | 34819281 | Additional unique device ID |
| 0 | DEVICE_SERIES | 250 | `DeviceSeries.AQUACLEAN = 250` |
| 1 | DEVICE_VARIANT | 0 | `AquacleanVariant.ALBA = 0` |

### BLE DIS vs. application-layer identification

The BLE Device Information Service (DIS) is readable before any application-layer connection:

| DIS characteristic | kstr value | Corresponds to |
|---|---|---|
| Model Number (0x2A24) | `828.860.00.A` | DpId 4 DEVICE_SAP_NUMBER — BLE board article |
| Serial Number (0x2A25) | `93136` | DpId 2 DEVICE_NUMBER — BLE board serial |
| Firmware Revision (0x2A26) | `RS03TS89` | DpId 8 (`RS`) + DpId 9 (`TS`) combined |
| Hardware Revision (0x2A27) | `00` | DpId 10 HW_RS_VERSION |
| Manufacturer Name (0x2A29) | `Geberit` | — |

> DIS Model Number (`828.860.00.A`) is the **BLE board** article, not the toilet product. The toilet article number (`146.350.01.x`) and human-readable name (`"Aquaclean Alba"`) are only accessible at the application layer.

---

## Descaling

| Mera Comfort field | Source | Alba DpId | Name | Notes |
|---|---|---|---|---|
| `descaling_state` | SPL param 4 | 585 | DESCALING_STATUS | 0=Error, 1=Disabled, 2=Ready, 3=Descaling, 4=Flushing |
| `days_until_next_descale` | GetStatisticsDescale | 589 | DAYS_UNTIL_NEXT_DESCALING | Same unit (days) |
| `unposted_shower_cycles` | GetStatisticsDescale | 588 | UNACCOUNTED_SHOWER_CYCLES | Showers since last 24 h recalculation |
| `date_time_at_last_descale` | GetStatisticsDescale | 590 | TIMESTAMP_OF_LAST_DESCALING | Unix timestamp |
| `date_time_at_last_descale_prompt` | GetStatisticsDescale | 591 | TIMESTAMP_OF_LAST_DESCALING_REQUEST | Unix timestamp |
| `number_of_descale_cycles` | GetStatisticsDescale | 592 | DESCALING_CYCLES | Count of completed descalings |
| `days_until_shower_restricted` | GetStatisticsDescale | 977 | DESCALING_DEVICE_LOCK_REMAINING_DAYS | Grace period after prompt before lock |
| `shower_cycles_until_confirmation` | GetStatisticsDescale | 979 | DESCALING_DEVICE_RELOCK_REMAINING_CYCLES | Cycles until re-lock after manual unlock |
| *(internal — not exposed over BLE)* | firmware | 781 | CREDITS_UNTIL_NEXT_DESCALING | Alba exposes raw credits; 200 credits = 1 day |

### Credit system (Alba only)

The Mera Comfort computes a credit accumulator internally (shower cycles × water hardness
setting) and exposes only the derived days value. The Alba promotes the raw counter to a
first-class readable DpId (781), alongside the derived days DpId (589):

```
CREDITS_UNTIL_NEXT_DESCALING (DpId 781) = DAYS_UNTIL_NEXT_DESCALING (DpId 589) × 200
```

Confirmed on kstr device (E4:85:01:CD:6B:04, never descaled):
```
CREDITS_UNTIL_NEXT_DESCALING = 33600
DAYS_UNTIL_NEXT_DESCALING    =   168
33600 / 168 = 200  ← exact integer
```

---

## Shower settings

The Mera Comfort stores preferences via proc 0x53 (read) / 0x54 (write) profile settings.
The Alba exposes both the active runtime value and the stored NVM value as separate DpIds.

| Mera Comfort setting ID | Name | Alba active DpId | Alba stored DpId | Range |
|---|---|---|---|---|
| 1 | Oscillator State | 577 ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS | 583 STORED_ANAL_SPRAY_ARM_OSCILLATION | 0–1 |
| 2 | Anal Shower Pressure | 571 ACTIVE_ANAL_SPRAY_INTENSITY_STATUS | 580 STORED_ANAL_SPRAY_INTENSITY | 0–4 |
| 4 | Anal Shower Position | 573 ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS | 581 STORED_ANAL_SPRAY_ARM_POSITION | 0–4 |
| 6 | Water Temperature | 575 ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS | 582 STORED_SHOWER_WATER_TEMPERATURE | 0–5 |

---

## Features on Mera Comfort absent from Alba 250 inventory

Confirmed absent from kstr (E4:85:01:CD:6B:04, `DEVICE_SERIES` = 250):

| Feature | Mera Comfort setting ID | Absent Alba DpIDs |
|---|---|---|
| Lady shower | 3 (pressure), 5 (position), 6 (temperature) | — |
| Dryer | 8 (temperature), 9 (state), 13 (spray intensity) | — |
| Odour extraction | 0 (on/off profile), common setting 0 (run-on) | — |
| Seat heating | 7 | — |
| Orientation light | common settings 1 (brightness), 2 (color), 3 (activation) | — |

> **Note:** orientation light and proximity detection are confirmed present on higher Alba
> models — see `docs/developer/alba-ble20-protocol.md` for DpId 44, 47, 55.

---

## Shower process status enums (Alba, confirmed from firmware)

| DpId | Name | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|---|
| 564 | ANAL_SHOWER_STATUS | Error | **Disabled** | Ready | Prerinsing | Arm Extending | **Shower** | Arm Retracting | Postrinsing |
| 567 | SPRAY_ARM_CLEANING_STATUS | Error | Disabled | **Ready** | Arm Extending | Cleaning | Arm Retracting | — | — |
| 585 | DESCALING_STATUS | Error | Disabled | **Ready** | Descaling | Flushing | — | — | — |

Bold = value observed on kstr at time of readall (2026-05-08, device idle, never descaled).
