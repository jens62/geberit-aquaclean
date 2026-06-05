# HACS Entity Reference

**Auto-generated** — do not edit by hand.
Run `tools/generate-alba-entity-docs.py` after any change to the entity lists.

Entity IDs assume the default integration name `Geberit AquaClean`.
ESPHome proxy entities use the `aquaclean_proxy_` prefix (separate HA device).

**Availability:** All = Mera Comfort + Alba · Alba only · Mera only ·
ESPHome only = only when an ESPHome BLE proxy is configured.

**DpId** (Alba only): `r:N` = read, `w:N` = write, `inst=N` = instance index.

## Sensors (`sensor.*`)

| Entity ID | Friendly Name | Availability | DpId (Alba) |
|-----------|--------------|--------------|-------------|
| `sensor.geberit_aquaclean_serial_number` | Serial Number | All | — |
| `sensor.geberit_aquaclean_sap_number` | SAP Number | All | — |
| `sensor.geberit_aquaclean_description` | Model | All | — |
| `sensor.geberit_aquaclean_production_date` | Production Date | All | — |
| `sensor.geberit_aquaclean_initial_operation_date` | Initial Operation Date | All | — |
| `sensor.geberit_aquaclean_soc_versions` | SOC Versions | All | — |
| `sensor.geberit_aquaclean_firmware_version` | Firmware Version | All | — |
| `sensor.geberit_aquaclean_days_until_next_descale` | Days Until Next Descale | All | — |
| `sensor.geberit_aquaclean_days_until_shower_restricted` | Days Until Shower Restricted | All | — |
| `sensor.geberit_aquaclean_shower_cycles_until_confirmation` | Shower Cycles Until Confirmation | All | — |
| `sensor.geberit_aquaclean_number_of_descale_cycles` | Number of Descale Cycles | All | — |
| `sensor.geberit_aquaclean_unposted_shower_cycles` | Unposted Shower Cycles | All | — |
| `sensor.geberit_aquaclean_date_time_at_last_descale` | Last Descale | All | — |
| `sensor.geberit_aquaclean_filter_days_remaining` | Days Until Filter Change | All | — |
| `sensor.geberit_aquaclean_filter_last_reset` | Last Filter Reset | All | — |
| `sensor.geberit_aquaclean_filter_reset_count` | Filter Reset Count | All | — |
| `sensor.geberit_aquaclean_filter_next_change` | Next Filter Change | All | — |
| `sensor.geberit_aquaclean_poll_epoch` | Last Poll | All | — |
| `sensor.geberit_aquaclean_poll_interval` | Poll Interval | All | — |
| `sensor.geberit_aquaclean_next_poll` | Next Poll | All | — |
| `sensor.geberit_aquaclean_descaling_state` | Descaling State | All | — |
| `sensor.geberit_aquaclean_descaling_duration_min` | Descaling Duration | All | — |
| `sensor.geberit_aquaclean_lid_offset_position` | Lid Offset Position | All | — |
| `sensor.geberit_aquaclean_shower_arm_offset_position` | Spray Arm Offset Position | All | — |
| `sensor.geberit_aquaclean_ble_rssi` | BLE Signal | All | — |
| `sensor.geberit_aquaclean_alba_spray_arm_cleaning_status` | Spray Arm Cleaning Status | Alba only | DpId 567 (enum) |
| `sensor.geberit_aquaclean_alba_descaling_status` | Descaling Status | Alba only | DpId 585 (enum) |
| `sensor.geberit_aquaclean_alba_days_until_next_descaling` | Days Until Next Descaling | Alba only | DpId 589 |
| `sensor.geberit_aquaclean_alba_descaling_cycles` | Descaling Cycles | Alba only | DpId 592 |
| `sensor.geberit_aquaclean_alba_credits_until_next_descaling` | Credits Until Next Descaling | Alba only | DpId 781 |
| `sensor.geberit_aquaclean_alba_descaling_device_lock_remaining_days` | Descaling Lock Remaining Days | Alba only | DpId 977 |
| `sensor.geberit_aquaclean_alba_descaling_device_relock_remaining_cycles` | Descaling Relock Remaining Cycles | Alba only | DpId 979 |
| `sensor.geberit_aquaclean_alba_descaling_device_lock_status` | Descaling Device Lock | Alba only | DpId 983 (enum) |
| `sensor.geberit_aquaclean_alba_unaccounted_shower_cycles` | Unaccounted Shower Cycles | Alba only | DpId 588 |
| `sensor.geberit_aquaclean_alba_timestamp_last_descaling` | Last Descaling | Alba only | DpId 590 |
| `sensor.geberit_aquaclean_alba_timestamp_last_descaling_request` | Last Descaling Request | Alba only | DpId 591 |
| `sensor.geberit_aquaclean_alba_rtc_time` | RTC Time | Alba only | DpId 15 |
| `sensor.geberit_aquaclean_alba_operation_time_total_s` | Operation Time Total | Alba only | DpId 148 |
| `sensor.geberit_aquaclean_alba_operation_time_since_power_up_s` | Operation Time Since Power-Up | Alba only | DpId 149 |
| `sensor.geberit_aquaclean_alba_product_registration_level` | Product Registration Level | Alba only | DpId 796 (enum) |
| `sensor.geberit_aquaclean_alba_active_intensity` | Active Spray Intensity | Alba only | r:DpId 571  w:DpId 570 |
| `sensor.geberit_aquaclean_alba_active_position` | Active Spray Position | Alba only | r:DpId 573  w:DpId 572 |
| `sensor.geberit_aquaclean_alba_active_temperature` | Active Water Temperature | Alba only | r:DpId 575  w:DpId 574 |
| `sensor.geberit_aquaclean_alba_fus_version` | FUS Version | Alba only | DpId 785 (3 instances) |
| `sensor.geberit_aquaclean_alba_geberit_loader_version` | Geberit Loader Version | Alba only | DpId 786 (2 instances) |
| `sensor.geberit_aquaclean_alba_wireless_stack_version` | Wireless Stack Version | Alba only | DpId 787 (3 instances) |
| `sensor.geberit_aquaclean_alba_anal_shower_progress_pct` | Anal Shower Progress | Alba only | DpId 565 (instanced, pct) |
| `sensor.geberit_aquaclean_alba_descaling_progress_pct` | Descaling Progress | Alba only | DpId 586 (instanced, pct) |
| `sensor.geberit_aquaclean_alba_spray_arm_cleaning_progress_pct` | Spray Arm Cleaning Progress | Alba only | DpId 568 (instanced, pct) |
| `sensor.geberit_aquaclean_alba_stats_total_usages` | Total AquaClean Uses | Alba only | DpId 689 inst=31 |
| `sensor.geberit_aquaclean_alba_stats_total_anal_showers` | Total Anal Shower Uses | Alba only | DpId 689 inst=32 |
| `sensor.geberit_aquaclean_alba_stats_total_lady_showers` | Total Lady Shower Uses | Alba only | DpId 689 inst=33 |
| `sensor.geberit_aquaclean_alba_stats_total_dryings` | Total Dryer Uses | Alba only | DpId 689 inst=34 |
| `sensor.geberit_aquaclean_alba_stats_total_descalings` | Total Descaling Cycles | Alba only | DpId 689 inst=35 |
| `sensor.geberit_aquaclean_alba_stats_total_spray_arm_cleanings` | Total Spray Arm Cleanings | Alba only | DpId 689 inst=36 |
| `sensor.geberit_aquaclean_ble_connection` | BLE Connection | All | — |
| `sensor.geberit_aquaclean_last_connect` | Last Connect | All | — |
| `sensor.geberit_aquaclean_last_poll_ms` | Last Poll ms | All | — |
| `sensor.geberit_aquaclean_avg_connect` | Avg Connect | All | — |
| `sensor.geberit_aquaclean_min_connect` | Min Connect | All | — |
| `sensor.geberit_aquaclean_max_connect` | Max Connect | All | — |
| `sensor.geberit_aquaclean_avg_poll` | Avg Poll | All | — |
| `sensor.geberit_aquaclean_min_poll` | Min Poll | All | — |
| `sensor.geberit_aquaclean_max_poll` | Max Poll | All | — |
| `sensor.geberit_aquaclean_poll_samples` | Poll Samples | All | — |
| `sensor.geberit_aquaclean_transport` | Transport | All | — |
| `sensor.geberit_aquaclean_avg_ble_rssi` | Avg BLE RSSI | All | — |
| `sensor.geberit_aquaclean_min_ble_rssi` | Min BLE RSSI | All | — |
| `sensor.geberit_aquaclean_max_ble_rssi` | Max BLE RSSI | All | — |
| `sensor.aquaclean_proxy_connection` | Connection | ESPHome only | — |
| `sensor.aquaclean_proxy_wifi_signal` | WiFi Signal | ESPHome only | — |
| `sensor.aquaclean_proxy_free_heap` | Free Heap | ESPHome only | — |
| `sensor.aquaclean_proxy_max_free_block` | Max Free Block | ESPHome only | — |
| `sensor.aquaclean_proxy_avg_wifi_rssi` | Avg WiFi RSSI | ESPHome only | — |
| `sensor.aquaclean_proxy_min_wifi_rssi` | Min WiFi RSSI | ESPHome only | — |
| `sensor.aquaclean_proxy_max_wifi_rssi` | Max WiFi RSSI | ESPHome only | — |

## Binary Sensors (`binary_sensor.*`)

| Entity ID | Friendly Name | Availability | DpId (Alba) |
|-----------|--------------|--------------|-------------|
| `binary_sensor.geberit_aquaclean_ble_connected` | BLE Connected | All | — |
| `binary_sensor.aquaclean_proxy_connected` | Connected | ESPHome only | — |
| `binary_sensor.geberit_aquaclean_is_user_sitting` | User Sitting | All | — |
| `binary_sensor.geberit_aquaclean_is_anal_shower_running` | Anal Shower Running | All | — |
| `binary_sensor.geberit_aquaclean_is_lady_shower_running` | Lady Shower Running | Mera only | — |
| `binary_sensor.geberit_aquaclean_is_dryer_running` | Dryer Running | Mera only | — |
| `binary_sensor.geberit_aquaclean_alba_error_power_supply` | Power Supply Error | Alba only | DpId 93 |
| `binary_sensor.geberit_aquaclean_alba_error_water_heater` | Water Heater Error | Alba only | DpId 764 |
| `binary_sensor.geberit_aquaclean_alba_error_level_control` | Level Control Error | Alba only | DpId 765 |
| `binary_sensor.geberit_aquaclean_alba_error_user_detection` | User Detection Error | Alba only | DpId 766 |
| `binary_sensor.geberit_aquaclean_alba_error_water_pump` | Water Pump Error | Alba only | DpId 789 |
| `binary_sensor.geberit_aquaclean_alba_error_spray_arm_drive` | Spray Arm Drive Error | Alba only | DpId 790 |
| `binary_sensor.geberit_aquaclean_alba_error_maintenance_request` | Maintenance Request | Alba only | DpId 820 |
| `binary_sensor.geberit_aquaclean_alba_error_descaling` | Descaling Error | Alba only | DpId 982 |
| `binary_sensor.geberit_aquaclean_alba_demo_mode` | Demo Mode | Alba only | DpId 795 |
| `binary_sensor.geberit_aquaclean_alba_showroom_mode` | Showroom Mode | Alba only | DpId 803 |
| `binary_sensor.geberit_aquaclean_alba_dry_run_mode` | Dry Run Mode | Alba only | DpId 810 |
| `binary_sensor.geberit_aquaclean_alba_active_oscillation` | Spray Arm Oscillation | Alba only | r:DpId 577  w:DpId 576 |

## Numbers (`number.*`)

| Entity ID | Friendly Name | Availability | DpId (Alba) |
|-----------|--------------|--------------|-------------|
| `number.geberit_aquaclean_ps_anal_shower_pressure` | Anal Shower Pressure | All | — |
| `number.geberit_aquaclean_ps_lady_shower_pressure` | Lady Shower Pressure | Mera only | — |
| `number.geberit_aquaclean_ps_anal_shower_position` | Anal Shower Position | All | — |
| `number.geberit_aquaclean_ps_lady_shower_position` | Lady Shower Position | Mera only | — |
| `number.geberit_aquaclean_ps_water_temperature` | Water Temperature | All | — |
| `number.geberit_aquaclean_ps_wc_seat_heat` | WC Seat Heat | Mera only | — |
| `number.geberit_aquaclean_ps_dryer_temperature` | Dryer Temperature | Mera only | — |
| `number.geberit_aquaclean_ps_dryer_state` | Dryer State | Mera only | — |
| `number.geberit_aquaclean_ps_dryer_spray_intensity` | Dryer Spray Intensity | Mera only | — |
| `number.geberit_aquaclean_ps_odour_extraction` | Odour Extraction | Mera only | — |
| `number.geberit_aquaclean_ps_oscillator_state` | Oscillator State | All | — |
| `number.geberit_aquaclean_cs_orientation_light_brightness` | Orientation Light Brightness | All | — |
| `number.geberit_aquaclean_cs_orientation_light_activation` | Orientation Light Activation | All | — |
| `number.geberit_aquaclean_cs_orientation_light_color` | Orientation Light Color | All | — |
| `number.geberit_aquaclean_cs_odour_extraction_run_on` | Odour Extraction Run-On | All | — |
| `number.geberit_aquaclean_cs_wc_lid_sensor_sensitivity` | WC Lid Sensor Sensitivity | Mera only | — |
| `number.geberit_aquaclean_cs_wc_lid_open_automatically` | WC Lid Open Automatically | Mera only | — |
| `number.geberit_aquaclean_cs_wc_lid_close_automatically` | WC Lid Close Automatically | Mera only | — |
| `number.geberit_aquaclean_alba_active_intensity` | Active Spray Intensity | Alba only | r:DpId 571  w:DpId 570 |
| `number.geberit_aquaclean_alba_active_position` | Active Spray Position | Alba only | r:DpId 573  w:DpId 572 |
| `number.geberit_aquaclean_alba_active_temperature` | Active Water Temperature | Alba only | r:DpId 575  w:DpId 574 |
| `number.geberit_aquaclean_alba_active_oscillation` | Spray Arm Oscillation | Alba only | r:DpId 577  w:DpId 576 |

## Buttons (`button.*`)

| Entity ID | Friendly Name | Availability | DpId (Alba) |
|-----------|--------------|--------------|-------------|
| `button.aquaclean_proxy_restart_aquaclean_proxy` | Restart AquaClean Proxy | ESPHome only | — |
| `button.geberit_aquaclean_toggle_lid` | Toggle Lid | Mera only | — |
| `button.geberit_aquaclean_toggle_anal_shower` | Toggle Anal Shower | All | — |
| `button.geberit_aquaclean_toggle_lady_shower` | Toggle Lady Shower | Mera only | — |
| `button.geberit_aquaclean_toggle_dryer` | Toggle Dryer | Mera only | — |
| `button.geberit_aquaclean_toggle_orientation_light` | Toggle Orientation Light | Mera only | — |
| `button.geberit_aquaclean_orientation_light_off` | Orientation Light Off | Mera only | — |
| `button.geberit_aquaclean_orientation_light_on` | Orientation Light On | Mera only | — |
| `button.geberit_aquaclean_orientation_light_when_approached` | Orientation Light When Approached | Mera only | — |
| `button.geberit_aquaclean_stop` | Stop | Mera only | — |
| `button.geberit_aquaclean_toggle_odour_extraction` | Toggle Odour Extraction | Mera only | — |
| `button.geberit_aquaclean_odour_extraction_run_on` | Odour Extraction Run-On | Mera only | — |
| `button.geberit_aquaclean_trigger_flush_manually` | Trigger Flush Manually | Mera only | — |
| `button.geberit_aquaclean_prepare_descaling` | Prepare Descaling | All | — |
| `button.geberit_aquaclean_confirm_descaling` | Confirm Descaling | All | — |
| `button.geberit_aquaclean_cancel_descaling` | Cancel Descaling | All | — |
| `button.geberit_aquaclean_postpone_descaling` | Postpone Descaling | All | — |
| `button.geberit_aquaclean_start_cleaning_device` | Start Cleaning Device | Mera only | — |
| `button.geberit_aquaclean_execute_next_cleaning_step` | Execute Next Cleaning Step | Mera only | — |
| `button.geberit_aquaclean_start_lid_position_calibration` | Start Lid Position Calibration | Mera only | — |
| `button.geberit_aquaclean_lid_position_offset_save` | Lid Position Offset Save | Mera only | — |
| `button.geberit_aquaclean_lid_position_offset_increment` | Lid Position Offset Increment | Mera only | — |
| `button.geberit_aquaclean_lid_position_offset_decrement` | Lid Position Offset Decrement | Mera only | — |
| `button.geberit_aquaclean_reset_filter_counter` | Reset Filter Counter | Mera only | — |
| `button.geberit_aquaclean_sync_rtc` | Sync RTC | Alba only | DpId 270 (write-only) |
| `button.geberit_aquaclean_restart_alba_device` | Restart Alba Device | Alba only | DpId 153 (write-only) |
| `button.geberit_aquaclean_start_stop_spray_arm_cleaning` | Start Spray Arm Cleaning | Alba only | DpId 566 (write-only) |
| `button.geberit_aquaclean_start_stop_spray_arm_cleaning` | Stop Spray Arm Cleaning | Alba only | DpId 566 (write-only) |
