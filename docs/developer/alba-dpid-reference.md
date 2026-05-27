# Alba 250 — Full DpId Reference

**Device:** AquaClean Alba, `DEVICE_SERIES=250`, `DEVICE_VARIANT=0`  
**BLE DIS:** model `828.860.00.A`, fw `RS03TS89`, sw `1.14.1 1.2.0`  
**DpId inventory:** 78 DpIds — 55 readable, 23 errors (write-only or require instance parameter)

---

## RTC is never set from factory

```
RTC_TIME (DpId 15)         = 947 744 194
OPERATION_TIME_TOTAL (148) = 1 059 394

946 684 800  (= 2000-01-01 00:00:00 UTC, factory default epoch)
+  1 059 394  (= OPERATION_TIME_TOTAL, seconds of accumulated run time)
= 947 744 194  (= RTC_TIME)        ✓ exact match
```

The device RTC is initialised to 2000-01-01 00:00:00 UTC at the factory and has
**never been synchronised** in the field.  It simply counts seconds from that default
epoch.  To set it, write the current Unix timestamp to DpId 270 (`SET_RTC_TIME`).

---

## Error categories

| Error type | Count | DpIds |
|---|---:|---|
| `InvalidBehavior` | 14 | 62, 83, 153, 270, 563, 566, 569, 570, 572, 574, 576, 584, 802, 978 |
| `InvalidInstance` | 9 | 405 (inst=31), 565 (inst=4), 568 (inst=4), 586 (inst=4), 688 (inst=31), 689 (inst=31), 785 (inst=3), 786 (inst=2), 787 (inst=3) |

All `InvalidBehavior` DpIds are **write-only** — they return an error on read, which is expected.
All `InvalidInstance` DpIds require an instance parameter: `read(dp_id, instance=N)`.

---

## InvalidInstance DpId details

### Progress DpIds (565, 568, 586) — 4 instances each

| Instance | Meaning |
|----------|---------|
| 0 | Maximum Total Time (ms) — full-cycle duration; 0 = no timeout |
| 1 | Elapsed Total Time (ms) |
| 2 | Maximum Step Time (ms) — current step duration; 0 = no timeout |
| 3 | Elapsed Step Time (ms) |

Progress % = `instance[1] / instance[0] × 100` (guard against `instance[0] == 0`).

### Version DpIds (785–787)

| DpId | Name | Instance 0 | Instance 1 | Instance 2 |
|------|------|-----------|-----------|-----------|
| 785 | `FUS_VERSION` | Major | Minor | Bugfix |
| 786 | `GEBERIT_LOADER_VERSION` | Major | Minor | — |
| 787 | `WIRELESS_STACK_VERSION` | Major | Minor | Bugfix |

FUS = Field Update Service (OTA firmware update component).
Readable version string: `f"{major}.{minor}.{bugfix}"`.

### Statistics counter DpIds (405, 688, 689) — instance = `EsStatisticCause` index

Each DpId has the same instance space.  Version 0 supports instances 0–15; version 1
supports instances 0–36.

| Instance | `EsStatisticCause` | Meaning |
|----------|--------------------|---------|
| 0  | `OdourExtraction` | |
| 1  | `OrientationLight` | |
| 2  | `UseWithFlush` | Toilet uses with flush |
| 3  | `FlushWithoutUse` | |
| 4–10 | `UseFfAutomatic` … `PowerOnFlushes` | Flush variants |
| 11–30 | `IntervalFlushesDiv5` … `FlushRemoteGbusV2` | Flush timing/volume/remote |
| 31 | `AquacleanUsages` | Total AquaClean activations |
| 32 | `AquacleanAnalShowers` | Anal shower count |
| 33 | `AquacleanLadyShowers` | Lady shower count |
| 34 | `AquacleanDryings` | Dryer activations |
| 35 | `AquacleanDescalings` | Descaling cycles completed |
| 36 | `AquacleanSprayArmCleanings` | Spray arm cleaning cycles |

Three flavors of the same counter space:

| DpId | Name | Resets on |
|------|------|-----------|
| 405 | `STATISTIC_COUNTER_SINCE_POWER_UP` | Each power cycle |
| 688 | `STATISTIC_COUNTER_SINCE_RESET` | User/factory reset |
| 689 | `STATISTIC_COUNTER_TOTAL` | Never (lifetime) |

---

## Full DpId table

**Column key:**
- **Beh**: DataPoint behaviour — `Info` (read-only, factory-set), `Protected` (read-only, device-internal), `Status` (live state, notifiable), `Nvm` (stored setting, readable+writable), `Command` (write-only trigger)
- **Type**: wire encoding — `Counter` (uint), `String`, `Enum` (uint with named values), `OffOn`, `Binary` (bitmask), `TimeStampUtc` (Unix timestamp uint), `Seconds`, `MilliSeconds`, `Unused` (0-byte command)
- **Inst**: instance required by `read(dp_id, instance=N)`; blank = no instance needed
- **Min/Max**: value range from protocol spec
- **Ver**: minimum DataPoint version
- **Raw**: value read from device (`ERROR` = read failed; reason in Comment)
- **Decoded**: human-readable interpretation (UTC datetime, duration, enum label)
- **UI Label / T-ID**: English label and translation column ID from `docs/developer/translation-30-lang.md`
- **Range**: manually experienced value range from `docs/developer/profile-settings.md`; blank where not documented
- **Mera Comfort**: equivalent field in Mera Comfort bridge (proc 0x53 profile setting ID or GetStatisticsDescale field)

| DpId | Name | Beh | Type | Inst | Min | Max | Ver | Raw | Decoded | UI Label | T-ID | Comment | Range | Description | Mera Comfort |
|-----:|------|-----|------|-----:|----:|----:|----:|-----|---------|----------|------|---------|-------|-------------|--------------|
| 0 | DEVICE_SERIES | Info | Counter | | 0 | 255 | 0 | 250 | | | | | | | |
| 1 | DEVICE_VARIANT | Info | Counter | | 0 | 255 | 0 | 0 | | | | | | | |
| 2 | DEVICE_NUMBER | Protected | Counter | | 0 | 9999999 | 0 | *(redacted)* | | | | | | | |
| 3 | DEVICE_PRODUCTION_DATE | Protected | TimeStampUtc | | 0 | 0 | 0 | *(redacted)* | *(redacted)* | | | | | | |
| 4 | DEVICE_SAP_NUMBER | Protected | String | | 0 | 12 | 0 | `828.860.00.A` | | | | | | | |
| 8 | FW_RS_VERSION | Info | String | | 2 | 2 | 0 | `03` | | | | 2-char fixed-length string | | | |
| 9 | FW_TS_VERSION | Info | Counter | | 0 | 65535 | 0 | 89 | | | | | | | |
| 10 | HW_RS_VERSION | Protected | String | | 2 | 2 | 0 | `00` | | | | 2-char fixed-length string | | | |
| 12 | PAIRING_SECRET | Protected | String | | 0 | 4 | 0 | *(redacted)* | | | | 4-char PIN printed on toilet sticker | | | |
| 13 | ACCESS_CODE | Nvm | String | | 0 | 6 | 0 | (empty) | | | | | | | |
| 14 | ACCESS_REVOCATION | Nvm | Counter | | 0 | 0 | 0 | 0 | | | | | | | |
| 15 | RTC_TIME | Status | TimeStampUtc | | 0 | 0 | 0 | *(see above)* | `2000-01-01 + OPERATION_TIME_TOTAL` | | | Never set — equals 2000-01-01 00:00:00 UTC + OPERATION_TIME_TOTAL (exact match) | | | |
| 62 | RESET | Command | Enum | | 0 | 4 | 1 | ERROR | | | | InvalidBehavior — write-only | | | |
| 83 | START_BOOTLOADER | Command | Enum | | 0 | 1 | 1 | ERROR | | | | InvalidBehavior — write-only | | | |
| 93 | POWER_SUPPLY_ERROR_STATUS | Status | Binary | | 4 | 4 | 1 | 0 | | | | | | | |
| 148 | OPERATION_TIME_TOTAL | Status | Seconds | | 0 | 0 | 0 | *(varies)* | `Xd HHh MMm SSs` | | | = RTC_TIME − 946684800 (2000-01-01 epoch) | | | |
| 149 | OPERATION_TIME_SINCE_POWER_UP | Status | Seconds | | 0 | 0 | 0 | *(varies)* | `Xd HHh MMm SSs` | | | | | | |
| 153 | RESTART | Command | Unused | | 0 | 0 | 0 | ERROR | | | | InvalidBehavior — write-only | | | |
| 236 | UNIQUE_DEVICE_NUMBER | Info | Counter | | 0 | 0 | 0 | *(redacted)* | | | | | | | |
| 270 | SET_RTC_TIME | Command | TimeStampUtc | | 946684800 | 4102358400 | 0 | ERROR | | | | InvalidBehavior — write-only; writable range 946684800–4102358400 | | | |
| 313 | SALES_SAP_NUMBER | Protected | String | | 0 | 20 | 0 | `245.832.00.1` | | | | | | | |
| 337 | BOOTLOADER_VARIANT | Info | Counter | | 0 | 255 | 0 | 0 | | | | | | | |
| 369 | SALES_PRODUCT_SERIAL_NUMBER | Protected | String | | 0 | 20 | 0 | *(redacted)* | | | | Full Geberit serial (SB…) | | | |
| 370 | SALES_PRODUCT_PRODUCTION_DATE | Protected | TimeStampUtc | | 0 | 0 | 0 | *(redacted)* | *(redacted)* | | | | | | |
| 371 | SALES_PRODUCT_SAP_NUMBER | Protected | String | | 0 | 12 | 0 | `146.350.01.x` | | | | Confirms Alba 250 toilet article number | | | |
| 405 | STATISTIC_COUNTER_SINCE_POWER_UP | Status | Counter | 31 | 0 | 999999999 | 1 | ERROR | | | | InvalidInstance — use `read(405, instance=31)` | | | |
| 431 | OPERATION_TIME_OFFSET | Protected | Seconds | | 0 | 0 | 0 | 0 | `0d 00h 00m 00s` | | | | | | |
| 563 | START_STOP_ANAL_SHOWER | Command | Enum | | 0 | 1 | 0 | ERROR | | Anal wash | T03 | InvalidBehavior — write-only; 0=Stop, 1=Start | | Starts or stops the anal shower. Whether the command is executed depends on the current status `DP_ANAL_SHOWER_STATUS`. | |
| 564 | ANAL_SHOWER_STATUS | Status | Enum | | 0 | 7 | 0 | 1 | Disabled | Anal wash | T03 | 0=Error, 1=Disabled, 2=Ready, 3=Prerinsing, 4=ArmExtending, 5=Shower, 6=ArmRetracting, 7=Postrinsing | | Updated on every change. The current status of the anal shower process. | |
| 565 | ANAL_SHOWER_PROGRESS | Status | MilliSeconds | 4 | 0 | 0 | 0 | ERROR | | Anal wash | T03 | InvalidInstance — use `read(565, instance=4)` | | Updated when notify enabled. Maximum and elapsed times of the complete anal shower process and current step. Maximum time 0 means no timeout. | |
| 566 | START_STOP_SPRAY_ARM_CLEANING | Command | Enum | | 0 | 1 | 0 | ERROR | | | | InvalidBehavior — write-only; 0=Stop, 1=Start | | Start or stops the spray arm cleaning. Whether the command is executed depends on the current status `DP_SPRAY_ARM_CLEANING_STATUS`. | |
| 567 | SPRAY_ARM_CLEANING_STATUS | Status | Enum | | 0 | 5 | 0 | 2 | Ready | Anal wash | T03 | 0=Error, 1=Disabled, 2=Ready, 3=ArmExtending, 4=Cleaning, 5=ArmRetracting | | Updated on every change. The current status of the spray arm cleaning. | |
| 568 | SPRAY_ARM_CLEANING_PROGRESS | Status | MilliSeconds | 4 | 0 | 0 | 0 | ERROR | | | | InvalidInstance — use `read(568, instance=4)` | | Updated when notify enabled. Maximum and elapsed times of the complete spray arm cleaning cycle and current step. Maximum time 0 means no timeout. | |
| 569 | LOAD_PROFILE | Command | Enum | | 0 | 0 | 0 | ERROR | | User profiles | T01 | InvalidBehavior — write-only | | | |
| 570 | SET_ACTIVE_ANAL_SPRAY_INTENSITY | Command | Enum | | 0 | 4 | 0 | ERROR | | Spray intensity | T02 | InvalidBehavior — write-only; 0=Level 1, 1=Level 2, 2=Level 3, 3=Level 4, 4=Level 5 | 0–4 | | |
| 571 | ACTIVE_ANAL_SPRAY_INTENSITY_STATUS | Status | Enum | | 0 | 4 | 0 | *(varies)* | | Spray intensity | T02 | 0=Level 1, 1=Level 2, 2=Level 3, 3=Level 4, 4=Level 5 | 0–4 | Updated on every change. The active setting of the spray intensity for the anal shower. | `anal_shower_pressure` (proc 0x53 ID 2) |
| 572 | SET_ACTIVE_ANAL_SPRAY_ARM_POSITION | Command | Enum | | 0 | 4 | 0 | ERROR | | Spray arm position | T07 | InvalidBehavior — write-only; 0=Position 1 … 4=Position 5 | 0–4 | | |
| 573 | ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS | Status | Enum | | 0 | 4 | 0 | *(varies)* | | Spray arm position | T07 | 0=Position 1, 1=Position 2, 2=Position 3, 3=Position 4, 4=Position 5 | 0–4 | Updated on every change. The active setting of the spray arm position for the anal shower. | `anal_shower_position` (proc 0x53 ID 4) |
| 574 | SET_ACTIVE_SHOWER_WATER_TEMPERATURE | Command | Enum | | 0 | 5 | 0 | ERROR | | Anal wash | T03 | InvalidBehavior — write-only; 0=Off, 1=Level 1 … 5=Level 5 | 0–5 | | |
| 575 | ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS | Status | Enum | | 0 | 5 | 0 | *(varies)* | | Anal wash | T03 | 0=Off, 1=Level 1, 2=Level 2, 3=Level 3, 4=Level 4, 5=Level 5 | 0–5 | Updated on every change. The active setting of the water temperature for both the anal and lady shower. | `water_temperature` (proc 0x53 ID 6) |
| 576 | SET_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION | Command | OffOn | | 0 | 1 | 0 | ERROR | | Anal wash | T03 | InvalidBehavior — write-only | 0–1 | | |
| 577 | ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS | Status | OffOn | | 0 | 1 | 0 | *(varies)* | | Anal wash | T03 | | 0–1 | Updated on every change. The active setting of the oscillating motion of the spray arm for the anal shower. | `oscillator_state` (proc 0x53 ID 1) |
| 580 | STORED_ANAL_SPRAY_INTENSITY | Nvm | Enum | | 0 | 4 | 0 | *(varies)* | | Spray intensity | T02 | 0=Level 1 … 4=Level 5 | 0–4 | Spray intensity for the anal shower stored in profiles. Active setting may differ — see DpId 571. | `anal_shower_pressure` stored (proc 0x53 ID 2) |
| 581 | STORED_ANAL_SPRAY_ARM_POSITION | Nvm | Enum | | 0 | 4 | 0 | *(varies)* | | Spray arm position | T07 | 0=Position 1 … 4=Position 5 | 0–4 | Spray arm position for the anal shower stored in profiles. Active setting may differ — see DpId 573. | `anal_shower_position` stored (proc 0x53 ID 4) |
| 582 | STORED_SHOWER_WATER_TEMPERATURE | Nvm | Enum | | 0 | 5 | 0 | *(varies)* | | Anal wash | T03 | 0=Off, 1=Level 1 … 5=Level 5 | 0–5 | Water temperature for both the anal and lady shower stored in profiles. Active setting may differ — see DpId 575. | `water_temperature` stored (proc 0x53 ID 6) |
| 583 | STORED_ANAL_SPRAY_ARM_OSCILLATION | Nvm | OffOn | | 0 | 1 | 0 | *(varies)* | | Anal wash | T03 | | 0–1 | Oscillating motion of the spray arm stored in profiles. Active setting may differ — see DpId 577. | `oscillator_state` stored (proc 0x53 ID 1) |
| 584 | START_STOP_DESCALING | Command | Enum | | 0 | 1 | 0 | ERROR | | | | InvalidBehavior — write-only; 0=Stop, 1=Start | | Start or stops the descaling. Whether the command is executed depends on the current status `DP_DESCALING_STATUS`. | |
| 585 | DESCALING_STATUS | Status | Enum | | 0 | 4 | 0 | 2 | Ready | | | 0=Error, 1=Disabled, 2=Ready, 3=Descaling, 4=Flushing | | Updated on every change. The current status of the descaling process. | SPL param 4 (`descaling_state`) |
| 586 | DESCALING_PROGRESS | Status | MilliSeconds | 4 | 0 | 0 | 0 | ERROR | | | | InvalidInstance — use `read(586, instance=4)` | | Updated when notify enabled. Maximum and elapsed times of the complete descaling process and current step. Maximum time 0 means no timeout. | |
| 588 | UNACCOUNTED_SHOWER_CYCLES | Nvm | Counter | | 0 | 0 | 0 | *(varies)* | | | | | | The number of showers started since the last calculation of the descaling related counters. Usually calculated every 24 hours, then counter is reset. | `unposted_shower_cycles` (GetStatisticsDescale) |
| 589 | DAYS_UNTIL_NEXT_DESCALING | Status | Counter | | 0 | 0 | 0 | *(varies)* | | | | | | Updated on every change. The number of days left before the user is requested to descale the device. Counter is reset after descaling. | `days_until_next_descale` (GetStatisticsDescale) |
| 590 | TIMESTAMP_OF_LAST_DESCALING | Nvm | TimeStampUtc | | 0 | 0 | 0 | 0 | `1970-01-01 00:00:00 UTC` | | | Value 0 = never descaled | | The date and time when the last descaling was successfully performed. | `date_time_at_last_descale` (GetStatisticsDescale) |
| 591 | TIMESTAMP_OF_LAST_DESCALING_REQUEST | Nvm | TimeStampUtc | | 0 | 0 | 0 | 0 | `1970-01-01 00:00:00 UTC` | | | Value 0 = never descaled | | The date and time when the user was first requested to descale after the last successful descaling. | `date_time_at_last_descale_prompt` (GetStatisticsDescale) |
| 592 | DESCALING_CYCLES | Nvm | Counter | | 0 | 0 | 0 | 0 | | | | | | The number of successfully performed descalings. | `number_of_descale_cycles` (GetStatisticsDescale) |
| 607 | USER_DETECTION_STATUS | Status | Enum | | 0 | 1 | 0 | 0 | User absent | | | 0=User absent, 1=User present | | Updated on every change. The current status of the user detection, regardless of the detection method. | |
| 688 | STATISTIC_COUNTER_SINCE_RESET | Status | Counter | 31 | 0 | 999999999 | 1 | ERROR | | | | InvalidInstance — use `read(688, instance=31)` | | | |
| 689 | STATISTIC_COUNTER_TOTAL | Status | Counter | 31 | 0 | 999999999 | 1 | ERROR | | | | InvalidInstance — use `read(689, instance=31)` | | | |
| 711 | STATISTIC_COUNTER_SINCE_POWER_UP_SUM | Status | Counter | | 0 | 0 | 0 | *(varies)* | | | | | | | |
| 764 | WATER_HEATER_ERROR_STATUS | Status | Binary | | 4 | 4 | 0 | 0 | | | | | | | |
| 765 | LEVEL_CONTROL_ERROR_STATUS | Status | Binary | | 4 | 4 | 0 | 0 | | | | | | | |
| 766 | USER_DETECTION_ERROR_STATUS | Status | Binary | | 4 | 4 | 0 | 0 | | | | | | | |
| 781 | CREDITS_UNTIL_NEXT_DESCALING | Nvm | Counter | | 0 | 0 | 0 | *(varies)* | | | | 33600 / 168 = 200 credits per day (exact integer) | | The number of credits left before the user is requested to descale. Reset after descaling. | internal only → `days_until_next_descale` × 200 |
| 785 | FUS_VERSION | Info | Counter | 3 | 0 | 0 | 0 | ERROR | | | | InvalidInstance — use `read(785, instance=3)` | | | |
| 786 | GEBERIT_LOADER_VERSION | Info | Counter | 2 | 0 | 0 | 0 | ERROR | | | | InvalidInstance — use `read(786, instance=2)` | | | |
| 787 | WIRELESS_STACK_VERSION | Info | Counter | 3 | 0 | 0 | 0 | ERROR | | | | InvalidInstance — use `read(787, instance=3)` | | | |
| 789 | WATER_PUMP_ERROR_STATUS | Status | Binary | | 4 | 4 | 0 | 0 | | | | | | | |
| 790 | SPRAY_ARM_DRIVE_ERROR_STATUS | Status | Binary | | 4 | 4 | 0 | 0 | | | | | | | |
| 795 | DEMO_MODE | Nvm | OffOn | | 0 | 1 | 0 | Off | | | | | | | |
| 796 | PRODUCT_REGISTRATION_LEVEL | Nvm | Enum | | 0 | 2 | 0 | 0 | Unregistered | User profiles | T01 | 0=Unregistered, 1=Registered Private, 2=Registered Public | | Set by the Home App when the product is registered. Determines which Home App features are unlocked. | |
| 802 | START_USER_SESSION | Command | Unused | | 0 | 0 | 0 | ERROR | | | | InvalidBehavior — write-only | | | |
| 803 | SHOWROOM_MODE | Protected | OffOn | | 0 | 1 | 0 | Off | | | | | | | |
| 810 | DRY_RUN_MODE | Status | OffOn | | 0 | 1 | 0 | Off | | | | | | | |
| 820 | MAINTENANCE_REQUEST_STATUS | Status | Binary | | 4 | 4 | 0 | 0 | | | | | | | |
| 977 | DESCALING_DEVICE_LOCK_REMAINING_DAYS | Nvm | Counter | | 0 | 0 | 0 | *(varies)* | | | | | | Days remaining until the device is locked due to overdue descaling. | `days_until_shower_restricted` (GetStatisticsDescale) |
| 978 | DESCALING_UNLOCK_DEVICE | Command | Unused | | 0 | 0 | 0 | ERROR | | | | InvalidBehavior — write-only | | | |
| 979 | DESCALING_DEVICE_RELOCK_REMAINING_CYCLES | Nvm | Counter | | 0 | 0 | 0 | 0 | | | | | | Shower cycles remaining until the device is re-locked due to overdue descaling. | `shower_cycles_until_confirmation` (GetStatisticsDescale) |
| 982 | DESCALING_ERROR_STATUS | Info | Binary | | 4 | 4 | 0 | 0 | | | | | | Active errors related to the descaling process. | |
| 983 | DESCALING_DEVICE_LOCK_STATUS | Status | OffOn | | 0 | 1 | 0 | Off | | | | | | Updated on every change. Indicates whether the device is locked due to overdue descaling. | |
