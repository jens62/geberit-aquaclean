# Geberit Home App — DpId polling / notification map

Sources: iOS app v2.14.1 source analysis; nRF52840 BLE captures; OTA capture 2026-06-01.

---

## Mera Comfort — GetSystemParameterList polling

The app holds a **persistent BLE connection** and polls `GetSystemParameterList` (proc 0x0D)
continuously — approximately every **0.4–0.5 seconds** while the app is open.

### SPL indices polled

| App version | Indices |
|-------------|---------|
| Older builds | `[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]` |
| v2.13.2+ (current) | `[13, 12, 0, 1, 2, 3, 4, 5, 6, 7]` |

Indices 8, 9, 10, 11 were removed from v2.13.2 onwards — they are device-variant specific
and can corrupt `GetFilterStatus` state on Mera Comfort until power-cycle.
See `ble-protocol.md` → SPL parameter index definitions for the full index/name table.

### Init sequence (once per connect)

| Phase | Calls | What |
|-------|-------|------|
| CCCD subscribe | 4 | Enable notifications on A5/A6/A7/A8 |
| Identification | 3 | GetDeviceIdentification, GetNodeList, GetSOCApplicationVersions |
| Firmware | 2 | GetFirmwareVersionList (component IDs 1–12, then 15) |
| Subscription init | 8 | SubscribeNotif 0x11 ×4, 0x13 ×4 |
| Profile settings | 13 | 3× SetStoredProfileSetting + 10× GetStoredProfileSetting (init-area) |
| Common settings | 10 | GetStoredCommonSetting IDs 0–9 (proc 0x51) |
| First SPL poll | 1 | GetSystemParameterList |
| Filter status | 1 | GetFilterStatus with IDs [0..11] |
| 0x55 | 1 | GetDeviceRegistrationLevel — bridge does not need this |

### Init sequence timing

The init sequence runs once per BLE connect (persistent BLE — app stays connected).
After init, the app enters the steady-state ~0.5 s SPL polling loop immediately.
No precise per-phase timing from captures; total init to first SPL poll is ~2–4 s.

### Bridge comparison

| What | App | Bridge |
|------|-----|--------|
| Poll method | GetSystemParameterList | GetSystemParameterList |
| Indices | `[13,12,0,1,2,3,4,5,6,7]` (v2.13.2) | `[0,1,2,3,4,5,6,7,12,13]` |
| Interval | ~0.5 s (persistent BLE) | 30 s default (on-demand BLE) |
| Notifications | none | none |

---

## Alba — DpId poll / notification map

Alba uses the Ble20 protocol.  The model is **event-driven, not periodic polling**:
`DataPointInventory` runs once on connect (~15–16 s, all ~78 DpIds) to discover all supported DpIds; after that the
app reads DpIds on-demand when screens open, and subscribes to device-push notifications
for a small number of live-changing values.

### Notifications — device → app (unsolicited push)

Only **one** DpId is subscribed to push notifications:

| DpId | Value | Name | Calling class |
|------|-------|------|---------------|
| `AC_STATUS_DESCALING` | 65600 | Descaling state | `AquaCleanDescaleState` |

Everything else is pulled on-demand.

---

### Reads at connect / init

| DpId | Value | Calling class |
|------|-------|---------------|
| `AC_GET_SERIAL_NUMBER` | 65537 | `DashboardLoginViewModel` |
| `AC_NODE_FW_RS_VERSIONS_TEXT` | 65693 | `GeberitDeviceExtensions` |
| `AC_NODE_FW_TS_VERSIONS_TEXT` | 65697 | `GeberitDeviceExtensions` |
| `AC_GET_INITIAL_OPERATION_DATE` | 65657 | `GeberitDeviceExtensions` |
| `DP_DEVICE_INITIAL_OPERATION_DATE` | 70 | `GeberitDeviceExtensions` |
| `DP_DEVICE_PRODUCTION_DATE` | 3 | `GeberitDeviceExtensions` |
| `DP_SALES_PRODUCT_PRODUCTION_DATE` | 370 | `GeberitDeviceExtensions` |
| `DP_FW_RS_VERSION` | 8 | `DeviceDataPoints.ReadRsTsVersion` |
| `DP_FW_TS_VERSION` | 9 | `DeviceDataPoints.ReadRsTsVersion` |
| `DP_LOCAL_TIME` | 763 | `BaseConnectToDeviceViewModel` |
| `DP_TIME_ZONE` | 547 | `BaseConnectToDeviceViewModel` |
| `DP_ACCESS_REVOCATION` | 14 | `BaseAquaCleanDashboard` |
| `DP_ACCESS_CODE` | 13 | access-level check |
| `AC_DEVICE_REGISTRATION_LEVEL` | 65687 | — |
| `AC_STATUS_TRIAL_REMAINING_DAYS` | 65694 | — |

### Writes at connect (clock sync)

| DpId | Value | When |
|------|-------|------|
| `DP_SET_RTC_TIME` | 270 | write-only; sets device RTC to current Unix timestamp |
| `DP_TIME_ZONE` | 547 | write timezone offset |
| `DP_UTC_TIME_OFFSET` | 411 | write UTC offset |

---

### Reads on-demand (screen open)

#### Device status / dashboard

| DpId | Value | Calling class |
|------|-------|---------------|
| `DP_USER_DETECTION_STATUS` | 607 | `NewAqcDeviceFunctionState` |
| `DP_ANAL_SHOWER_STATUS` | 564 | `NewAqcDeviceFunctionState` |
| `DP_LADY_SHOWER_STATUS` | 872 | `NewAqcDeviceFunctionState` |
| `DP_DRYING_STATUS` | 875 | `NewAqcDeviceFunctionState` |
| `DP_SPRAY_ARM_CLEANING_STATUS` | 567 | `NewAqcCleaningState` |
| `DP_DESCALING_STATUS` | 585 | `NewAqcDescaleState` |
| `AC_STATUS_DESCALING` | 65600 | `AquaCleanDescaleState` (also push-notified) |
| `AC_STATUS_DRYER` | 65599 | — |
| `AC_STATUS_USER_PRESENT` | 65596 | — |
| `AC_STATUS_SERVICE` | 65603 | `AquaCleanCleaningState` |
| `AC_STATUS_LAST_ERROR` | 65602 | `AquaCleanErrorService` |
| `AC_STATUS_ORIENTATION_LIGHT` | 65605 | `DeviceOptionSelectionState` |
| `AC_STATUS_DRAINING` | 65606 | — |

#### Descale statistics (maintenance screen)

| DpId | Value |
|------|-------|
| `AC_STATUS_DURATION_DESCALING` | 65601 |
| `AC_STATUS_DAYS_UNTIL_NEXT_DESCALE` | 65609 |
| `AC_STATUS_DAYS_UNTIL_SHOWER_RESTRICTED` | 65610 |
| `AC_STATUS_SHOWER_CYCLES_UNTIL_CONFIRMATION` | 65611 |
| `AC_STATUS_TIMESTAMP_AT_LAST_DESCALE` | 65612 |
| `AC_STATUS_TIMESTAMP_AT_LAST_DESCALE_PROMPT` | 65613 |
| `AC_STATUS_NUMBER_OF_DESCALE_CYCLES` | 65614 |
| `AC_STATUS_UNPOSTED_SHOWER_CYCLES` | 65608 |
| `AC_STATUS_DAYS_UNTIL_NEXT_FILTERCHANGE` | 65615 |
| `DP_DAYS_UNTIL_NEXT_DESCALING` | 589 |
| `DP_DESCALING_DEVICE_LOCK_STATUS` | 983 |

#### Active profile settings (settings screen — read before presenting sliders)

| DpId | Value |
|------|-------|
| `AC_ACTIVE_PROFILE_SETTING_ODOUR_EXTRACTION` | 65628 |
| `AC_ACTIVE_PROFILE_SETTING_OSCILLATION` | 65629 |
| `AC_ACTIVE_PROFILE_SETTING_ANAL_PRESSURE` | 65630 |
| `AC_ACTIVE_PROFILE_SETTING_LADY_PRESSURE` | 65631 |
| `AC_ACTIVE_PROFILE_SETTING_ANAL_POSITION` | 65632 |
| `AC_ACTIVE_PROFILE_SETTING_LADY_POSITION` | 65633 |
| `AC_ACTIVE_PROFILE_SETTING_WATER_TEMPERATURE` | 65634 |
| `AC_ACTIVE_PROFILE_SETTING_SEAT_TEMPERATURE` | 65635 |
| `AC_ACTIVE_PROFILE_SETTING_DRYER_TEMPERATURE` | 65636 |
| `AC_ACTIVE_PROFILE_SETTING_DRYER` | 65637 |
| `AC_ACTIVE_PROFILE_SETTING_SYSTEM_FLUSH` | 65638 |
| `AC_ACTIVE_PROFILE_SETTING_SEAT_HEATING` | 65639 |
| `AC_ACTIVE_PROFILE_SETTING_WATER_HEATING` | 65640 |
| `AC_ACTIVE_PROFILE_SETTING_DRYER_FAN_POWER` | 65641 |
| `AC_ACTIVE_PROFILE_SETTING_LADY_OSCILLATION` | 65642 |

Stored-profile mirrors (65658–65672): same names with `STORED` prefix; read alongside
actives when presenting "stored defaults" view.

#### Active common settings (device config screen)

| DpId | Value |
|------|-------|
| `AC_ACTIVE_COMMON_SETTING_WATER_TYPE` | 65643 |
| `AC_ACTIVE_COMMON_SETTING_ORIENTATION_LIGHT_BRIGHTNESS` | 65644 |
| `AC_ACTIVE_COMMON_SETTING_ORIENTATION_LIGHT_COLOUR` | 65645 |
| `AC_ACTIVE_COMMON_SETTING_ORIENTATION_LIGHT_MODE` | 65646 |
| `AC_ACTIVE_COMMON_SETTING_LID_LIFTER_SENSOR_RANGE` | 65647 |
| `AC_ACTIVE_COMMON_SETTING_ODOUR_EXTRACTION_RUN_ON` | 65648 |
| `AC_ACTIVE_COMMON_SETTING_LID_LIFTER_OPENS` | 65649 |
| `AC_ACTIVE_COMMON_SETTING_LID_LIFTER_CLOSES` | 65650 |
| `AC_ACTIVE_COMMON_SETTING_AUTO_FLUSH` | 65651 |
| `AC_ACTIVE_COMMON_SETTING_DEMO_MODE` | 65652 |
| `AC_ACTIVE_COMMON_SETTING_LIGHT_SENSOR_SENSITIVITY` | 65653 |
| `AC_ACTIVE_COMMON_SETTING_ORIENTATION_LIGHT_RUN_ON` | 65654 |
| `AC_ACTIVE_COMMON_SETTING_CARE_MODE` | 65655 |
| `AC_ACTIVE_COMMON_SETTING_LANGUAGE` | 65656 |

Stored-common mirrors (65673–65686): read alongside actives.

---

### Writes — commands / triggers

| DpId | Value | When |
|------|-------|------|
| `AC_CMD_TOGGLE_ANAL_SHOWER` | 65540 | user taps anal shower |
| `AC_CMD_TOGGLE_LADY_SHOWER` | 65541 | user taps lady shower |
| `AC_CMD_TOGGLE_DRYER` | 65542 | user taps dryer |
| `AC_CMD_STOP` | 65543 | user taps Stop |
| `AC_CMD_LID` | 65550 | user taps lid button |
| `AC_CMD_DESCALING` | 65546 | start descaling |
| `AC_CMD_CONFIRM_DESCALING` | 65547 | confirm descaling |
| `AC_CMD_CANCEL_DESCALING` | 65548 | cancel descaling |
| `AC_CMD_DELAY_DESCALING` | 65549 | postpone descaling |
| `AC_CMD_FILTER_CHANGE` | 65587 | confirm filter change |
| `AC_CMD_ORIENTATION_LIGHT` | 65560 | toggle orientation light |
| `AC_CMD_SERVICE` | 65544 | start service mode |
| `AC_CMD_STEP_SERVICE` | 65545 | step through service |
| `AC_CMD_DRAINING` | 65592 | draining command |
| `AC_CMD_FLUSH_FULL` | 65577 | full flush |
| `DP_TRIGGER_LID_LIFTING` | 1009 | toggle lid (Alba-native) |
| `DP_START_STOP_ANAL_SHOWER` | 563 | start/stop anal shower (Alba-native) |
| `DP_START_STOP_LADY_SHOWER` | 868 | start/stop lady shower (Alba-native) |
| `DP_START_STOP_DRYING` | 874 | start/stop dryer (Alba-native) |
| `DP_START_STOP_DESCALING` | 584 | start/stop descaling (Alba-native) |
| `DP_START_STOP_SPRAY_ARM_CLEANING` | 566 | spray arm cleaning (Alba-native) |

### Writes — settings (user saves a change)

| DpId | Value | What |
|------|-------|------|
| `AC_ACTIVE_COMMON_SETTING_ORIENTATION_LIGHT_BRIGHTNESS` | 65644 | brightness |
| `AC_ACTIVE_COMMON_SETTING_ORIENTATION_LIGHT_COLOUR` | 65645 | colour |
| `AC_ACTIVE_COMMON_SETTING_ORIENTATION_LIGHT_MODE` | 65646 | mode |
| `AC_ACTIVE_COMMON_SETTING_LIGHT_SENSOR_SENSITIVITY` | 65653 | proximity sensor range |
| `AC_ACTIVE_PROFILE_SETTING_*` | 65628–65642 | all profile settings |
| `DP_ORIENTATION_LIGHT_MODE` | 44 | Alba-native orientation light mode |
| `DP_ORIENTATION_LIGHT_INTENSITY` | 48 | Alba-native orientation light intensity |

---

### Bridge — what is actually polled

The bridge polls on a configurable interval (default 30 s) using on-demand BLE connects.

`DataPointInventory` runs once on the **first connect** (~15–16 s, ~78 DpIds at ~200 ms/frame)
and is cached in `coordinator._alba_inventory`. On subsequent connects the inventory is skipped.

| Connect type | DataPointInventory | Rest of poll | Total |
|---|---|---|---|
| First connect (uncached) | ~15–16 s | ~11 s | ~27 s |
| Subsequent connects (cached) | skipped | ~14 s | ~14 s |

Fast/slow split per poll cycle:

#### Every poll — fast path (13 DpId reads, ~4.6 s total)

`get_system_parameter_list_async([0, 1, 2, 3])` — maps SPL indices to Alba DpIds:

| SPL index | DpId | Value | Name |
|-----------|------|-------|------|
| 0 | 607 | `DP_USER_DETECTION_STATUS` | user sitting |
| 1 | — | *(no equivalent)* | dryer |
| 2 | 872 | `DP_LADY_SHOWER_STATUS` | lady shower |
| 3 | 564 | `DP_ANAL_SHOWER_STATUS` | anal shower |

`get_misc_state_fast_async()` — 9 live-changing DpId reads:

| DpId | Value | Name |
|------|-------|------|
| 571 | `DP_ACTIVE_ANAL_SPRAY_INTENSITY_STATUS` | active intensity |
| 573 | `DP_ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS` | active position |
| 575 | `DP_ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS` | active temperature |
| 577 | `DP_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS` | active oscillation |
| 567 | `DP_SPRAY_ARM_CLEANING_STATUS` | spray arm cleaning state |
| 585 | `DP_DESCALING_STATUS` | descaling state |
| 589 | `DP_DAYS_UNTIL_NEXT_DESCALING` | days until next descale |
| 588 | `DP_UNACCOUNTED_SHOWER_CYCLES` | unaccounted shower cycles |
| 607 | `DP_USER_DETECTION_STATUS` | user sitting (re-read) |

#### Every 10th poll — slow path (~40 additional reads, ~22 s total)

`get_device_identification_async(0)` — proc 0x82 (serial, SAP number, etc.)

`get_misc_state_async()` — all fast DpIds above, plus:

| DpId | Value | Name |
|------|-------|------|
| 590 | `DP_TIMESTAMP_OF_LAST_DESCALING` | timestamp last descaling |
| 591 | `DP_TIMESTAMP_OF_LAST_DESCALING_REQUEST` | timestamp last descaling request |
| 592 | `DP_DESCALING_CYCLES` | total descaling cycles |
| 781 | `DP_CREDITS_UNTIL_NEXT_DESCALING` | credits remaining |
| 977 | `DP_DESCALING_DEVICE_LOCK_REMAINING_DAYS` | days until device lock |
| 979 | `DP_DESCALING_DEVICE_RELOCK_REMAINING_CYCLES` | cycles until re-lock |
| 983 | `DP_DESCALING_DEVICE_LOCK_STATUS` | lock status |
| 15 | `DP_RTC_TIME` | device RTC |
| 148 | `DP_OPERATION_TIME_TOTAL` | total operation time |
| 149 | `DP_OPERATION_TIME_SINCE_POWER_UP` | operation time since power-up |
| 93 | `DP_POWER_SUPPLY_ERROR_STATUS` | error: power supply |
| 764 | `DP_WATER_HEATER_ERROR_STATUS` | error: water heater |
| 765 | `DP_LEVEL_CONTROL_ERROR_STATUS` | error: level control |
| 766 | `DP_USER_DETECTION_ERROR_STATUS` | error: user detection |
| 789 | `DP_WATER_PUMP_ERROR_STATUS` | error: water pump |
| 790 | `DP_SPRAY_ARM_DRIVE_ERROR_STATUS` | error: spray arm drive |
| 820 | `DP_MAINTENANCE_REQUEST_STATUS` | maintenance request |
| 982 | `DP_DESCALING_ERROR_STATUS` | error: descaling |
| 795 | `DP_DEMO_MODE` | demo mode |
| 803 | `DP_SHOWROOM_MODE` | showroom mode |
| 810 | `DP_DRY_RUN_MODE` | dry run mode |
| 796 | `DP_PRODUCT_REGISTRATION_LEVEL` | registration level |
| 8 | `DP_FW_RS_VERSION` | firmware RS version |
| 9 | `DP_FW_TS_VERSION` | firmware TS version |
| 10 | `DP_HW_RS_VERSION` | hardware RS version |
| 69 | `DP_MCU_VERSION` | MCU version |
| 12 | `DP_PAIRING_SECRET` | pairing secret (diagnostic only) |

`get_instanced_stats_async()` — instanced reads:

| DpId | Instance | Name |
|------|----------|------|
| 565 | 4 | `DP_ANAL_SHOWER_PROGRESS` |
| 568 | 4 | `DP_SPRAY_ARM_CLEANING_PROGRESS` |
| 586 | 4 | `DP_DESCALING_PROGRESS` |
| 785 | 3 | `DP_FUS_VERSION` |
| 786 | 2 | `DP_GEBERIT_LOADER_VERSION` |
| 787 | 3 | `DP_WIRELESS_STACK_VERSION` |
| 405 | 31 | `DP_STATISTIC_COUNTER_SINCE_POWER_UP` |
| 688 | 31 | `DP_STATISTIC_COUNTER_SINCE_RESET` |
| 689 | 31 | `DP_STATISTIC_COUNTER_TOTAL` |

`get_stored_profile_settings_async()` — 4 NVM reads:

| DpId | Value | Name |
|------|-------|------|
| 580 | `DP_STORED_ANAL_SPRAY_INTENSITY` | stored intensity |
| 581 | `DP_STORED_ANAL_SPRAY_ARM_POSITION` | stored position |
| 582 | `DP_STORED_SHOWER_WATER_TEMPERATURE` | stored temperature |
| 583 | `DP_STORED_ANAL_SPRAY_ARM_OSCILLATION` | stored oscillation |

---

### App vs. bridge gap summary

| Category | App | Bridge |
|----------|-----|--------|
| Notifications | `AC_STATUS_DESCALING` (65600) push | none |
| Active status | 13 DpIds on-demand when screen opens | 4 DpIds every poll (607, 872, 564 + dryer placeholder) |
| Active misc state | 9 DpIds on-demand | 9 DpIds every poll (fast); ~27 more every 10th poll (slow) |
| Descale stats | 8 `AC_` + 2 `DP_` on-demand | `DP_DESCALING_STATUS` (585) + `DP_DAYS_UNTIL_NEXT_DESCALING` (589) fast; full set on slow poll |
| Stored profile settings | 15 active + 15 stored | 4 DpIds (580–583) every 10th poll |
| Common settings | 14 active + 14 stored | none |
| Commands | 21 `AC_CMD_` + 6 `DP_` | lid (1009), anal/lady/dryer/stop + descaling cmds |
| Firmware / versions | at connect | every 10th poll (misc slow + instanced) |
