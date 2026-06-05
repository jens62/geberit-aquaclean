# Alba HACS Entity Reference

**Auto-generated** — do not edit by hand.
Run `tools/generate-alba-entity-docs.py` after any change to the ALBA entity lists.

Entity IDs assume the default integration name `Geberit AquaClean`.
All entities are unavailable on Mera Comfort devices.

DpId notation: `r:N` = read DpId, `w:N` = write DpId, `inst=N` = instance index.

## Sensors (`sensor.*`)

| Entity ID | Friendly Name | DpId |
|-----------|--------------|------|
| `sensor.geberit_aquaclean_alba_spray_arm_cleaning_status` | Spray Arm Cleaning Status | DpId 567 (enum) |
| `sensor.geberit_aquaclean_alba_descaling_status` | Descaling Status | DpId 585 (enum) |
| `sensor.geberit_aquaclean_alba_days_until_next_descaling` | Days Until Next Descaling | DpId 589 |
| `sensor.geberit_aquaclean_alba_descaling_cycles` | Descaling Cycles | DpId 592 |
| `sensor.geberit_aquaclean_alba_credits_until_next_descaling` | Credits Until Next Descaling | DpId 781 |
| `sensor.geberit_aquaclean_alba_descaling_device_lock_remaining_days` | Descaling Lock Remaining Days | DpId 977 |
| `sensor.geberit_aquaclean_alba_descaling_device_relock_remaining_cycles` | Descaling Relock Remaining Cycles | DpId 979 |
| `sensor.geberit_aquaclean_alba_descaling_device_lock_status` | Descaling Device Lock | DpId 983 (enum) |
| `sensor.geberit_aquaclean_alba_unaccounted_shower_cycles` | Unaccounted Shower Cycles | DpId 588 |
| `sensor.geberit_aquaclean_alba_timestamp_last_descaling` | Last Descaling | DpId 590 |
| `sensor.geberit_aquaclean_alba_timestamp_last_descaling_request` | Last Descaling Request | DpId 591 |
| `sensor.geberit_aquaclean_alba_rtc_time` | RTC Time | DpId 15 |
| `sensor.geberit_aquaclean_alba_operation_time_total_s` | Operation Time Total | DpId 148 |
| `sensor.geberit_aquaclean_alba_operation_time_since_power_up_s` | Operation Time Since Power-Up | DpId 149 |
| `sensor.geberit_aquaclean_alba_product_registration_level` | Product Registration Level | DpId 796 (enum) |
| `sensor.geberit_aquaclean_alba_active_intensity` | Active Spray Intensity | r:DpId 571  w:DpId 570 |
| `sensor.geberit_aquaclean_alba_active_position` | Active Spray Position | r:DpId 573  w:DpId 572 |
| `sensor.geberit_aquaclean_alba_active_temperature` | Active Water Temperature | r:DpId 575  w:DpId 574 |
| `sensor.geberit_aquaclean_alba_fus_version` | FUS Version | DpId 785 (3 instances) |
| `sensor.geberit_aquaclean_alba_geberit_loader_version` | Geberit Loader Version | DpId 786 (2 instances) |
| `sensor.geberit_aquaclean_alba_wireless_stack_version` | Wireless Stack Version | DpId 787 (3 instances) |
| `sensor.geberit_aquaclean_alba_anal_shower_progress_pct` | Anal Shower Progress | DpId 565 (instanced, pct) |
| `sensor.geberit_aquaclean_alba_descaling_progress_pct` | Descaling Progress | DpId 586 (instanced, pct) |
| `sensor.geberit_aquaclean_alba_spray_arm_cleaning_progress_pct` | Spray Arm Cleaning Progress | DpId 568 (instanced, pct) |
| `sensor.geberit_aquaclean_alba_stats_total_usages` | Total AquaClean Uses | DpId 689 inst=31 |
| `sensor.geberit_aquaclean_alba_stats_total_anal_showers` | Total Anal Shower Uses | DpId 689 inst=32 |
| `sensor.geberit_aquaclean_alba_stats_total_lady_showers` | Total Lady Shower Uses | DpId 689 inst=33 |
| `sensor.geberit_aquaclean_alba_stats_total_dryings` | Total Dryer Uses | DpId 689 inst=34 |
| `sensor.geberit_aquaclean_alba_stats_total_descalings` | Total Descaling Cycles | DpId 689 inst=35 |
| `sensor.geberit_aquaclean_alba_stats_total_spray_arm_cleanings` | Total Spray Arm Cleanings | DpId 689 inst=36 |

## Binary Sensors (`binary_sensor.*`)

| Entity ID | Friendly Name | DpId |
|-----------|--------------|------|
| `binary_sensor.geberit_aquaclean_alba_error_power_supply` | Power Supply Error | DpId 93 |
| `binary_sensor.geberit_aquaclean_alba_error_water_heater` | Water Heater Error | DpId 764 |
| `binary_sensor.geberit_aquaclean_alba_error_level_control` | Level Control Error | DpId 765 |
| `binary_sensor.geberit_aquaclean_alba_error_user_detection` | User Detection Error | DpId 766 |
| `binary_sensor.geberit_aquaclean_alba_error_water_pump` | Water Pump Error | DpId 789 |
| `binary_sensor.geberit_aquaclean_alba_error_spray_arm_drive` | Spray Arm Drive Error | DpId 790 |
| `binary_sensor.geberit_aquaclean_alba_error_maintenance_request` | Maintenance Request | DpId 820 |
| `binary_sensor.geberit_aquaclean_alba_error_descaling` | Descaling Error | DpId 982 |
| `binary_sensor.geberit_aquaclean_alba_demo_mode` | Demo Mode | DpId 795 |
| `binary_sensor.geberit_aquaclean_alba_showroom_mode` | Showroom Mode | DpId 803 |
| `binary_sensor.geberit_aquaclean_alba_dry_run_mode` | Dry Run Mode | DpId 810 |
| `binary_sensor.geberit_aquaclean_alba_active_oscillation` | Spray Arm Oscillation | r:DpId 577  w:DpId 576 |

## Numbers (`number.*`)

| Entity ID | Friendly Name | DpId |
|-----------|--------------|------|
| `number.geberit_aquaclean_alba_active_intensity` | Active Spray Intensity | r:DpId 571  w:DpId 570 |
| `number.geberit_aquaclean_alba_active_position` | Active Spray Position | r:DpId 573  w:DpId 572 |
| `number.geberit_aquaclean_alba_active_temperature` | Active Water Temperature | r:DpId 575  w:DpId 574 |
| `number.geberit_aquaclean_alba_active_oscillation` | Spray Arm Oscillation | r:DpId 577  w:DpId 576 |

## Buttons (`button.*`)

| Entity ID | Friendly Name | DpId |
|-----------|--------------|------|
| `button.geberit_aquaclean_sync_rtc` | Sync RTC | DpId 270 (write-only) |
| `button.geberit_aquaclean_restart_alba_device` | Restart Alba Device | DpId 153 (write-only) |
| `button.geberit_aquaclean_start_stop_spray_arm_cleaning` | Start Spray Arm Cleaning | DpId 566 (write-only) |
| `button.geberit_aquaclean_start_stop_spray_arm_cleaning` | Stop Spray Arm Cleaning | DpId 566 (write-only) |
