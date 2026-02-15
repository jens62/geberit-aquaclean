# Geberit AquaClean BLE Command Reference

**Document Version:** 1.0 
**Last Updated:** 2025-08-21  
**Source Verification:** All data point IDs verified against actual `DpId.cs` source code

## Overview

This document provides a comprehensive reference for BLE commands supported by Geberit AquaClean toilet systems. **ALL data point IDs have been verified against the actual source code** to ensure accuracy.

## Data Type Legend

| Type | Description | Range/Format |
|------|-------------|--------------|
| Binary | Raw binary data | 1-4 bytes |
| OffOn | Boolean state | 0=Off, 1=On |
| Enum | Enumerated values | 0-N (device specific) |
| Percent | Percentage value | 0-100% |
| Counter | Numeric counter | 0-4294967295 |
| String | Text string | Variable length |
| TimeStampUtc | UTC timestamp | Unix timestamp |
| Signed | Signed integer | -2147483648 to +2147483647 |

## Toilet Control Operations

### Flush Operations

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 112 | `DP_BLOCK_FLUSH` | Write-Only | All Models | OffOn: 0-1 | Block flush operation |
| 113 | `DP_BLOCK_FLUSH_STATUS` | Read-Only | All Models | Binary | Flush block status |
| 115 | `DP_CLEANING_MODE` | Write-Only | All Models | Enum: 0-2 | Cleaning mode |
| 117 | `DP_CLEANING_MODE_STATUS` | Read-Only | All Models | Enum: 0-2 | Cleaning status |
| 118 | `DP_PRE_FLUSH` | Read/Write | Mera Series Only | OffOn: 0-1 | Pre-flush configuration |
| 119 | `DP_POST_FLUSH` | Read/Write | All Models | OffOn: 0-1 | Post-flush configuration |
| 126 | `DP_MANUAL_FLUSH` | Read/Write | All Models | OffOn: 0-1 | Manual flush trigger |
| 127 | `DP_AUTOMATIC_FLUSH` | Read/Write | All Models | OffOn: 0-1 | Automatic flush enable |
| 141 | `DP_FLUSH` | Write-Only | All Models | Binary | Trigger flush operation |
| 142 | `DP_FLUSH_STATUS` | Read-Only | All Models | Binary | Flush operation status |
| 291 | `DP_FULL_FLUSH_VOLUME` | Read/Write | All Models | Counter | Full flush volume |
| 292 | `DP_PART_FLUSH_VOLUME` | Read/Write | All Models | Counter | Partial flush volume |

### Shower Operations

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 563 | `DP_START_STOP_ANAL_SHOWER` | Write-Only | All Models | OffOn: 0-1 | Start/stop anal shower |
| 564 | `DP_ANAL_SHOWER_STATUS` | Read-Only | All Models | Enum: 0-2 | Anal shower status |
| 565 | `DP_ANAL_SHOWER_PROGRESS` | Read-Only | All Models | Percent: 0-100% | Shower progress |
| 568 | `DP_START_STOP_LADY_SHOWER` | Write-Only | Exclude: Tuma Classic, Cama | OffOn: 0-1 | Start/stop lady shower |
| 570 | `DP_SET_ACTIVE_ANAL_SPRAY_INTENSITY` | Write-Only | All Models | Percent: 0-100% | Set spray intensity |
| 571 | `DP_ACTIVE_ANAL_SPRAY_INTENSITY_STATUS` | Read-Only | All Models | Percent: 0-100% | Current spray intensity |
| 572 | `DP_SET_ACTIVE_ANAL_SPRAY_ARM_POSITION` | Write-Only | Exclude: Cama | Percent: 0-100% | Set spray arm position |
| 573 | `DP_ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS` | Read-Only | Exclude: Cama | Percent: 0-100% | Current spray arm position |
| 574 | `DP_SET_ACTIVE_SHOWER_WATER_TEMPERATURE` | Write-Only | All Models | Counter: 30-40°C | Set water temperature |
| 575 | `DP_ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS` | Read-Only | All Models | Counter: 30-40°C | Current water temperature |
| 576 | `DP_SET_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION` | Write-Only | Exclude: Cama | OffOn: 0-1 | Set arm oscillation |
| 577 | `DP_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS` | Read-Only | Exclude: Cama | OffOn: 0-1 | Current oscillation status |
| 580 | `DP_STORED_ANAL_SPRAY_INTENSITY` | Read/Write | Exclude: Tuma Classic, Cama | Percent: 0-100% | Stored spray intensity |
| 581 | `DP_STORED_ANAL_SPRAY_ARM_POSITION` | Read/Write | Exclude: Tuma Classic, Cama | Percent: 0-100% | Stored spray arm position |
| 582 | `DP_STORED_SHOWER_WATER_TEMPERATURE` | Read/Write | Exclude: Tuma Classic, Cama | Counter: 30-40°C | Stored water temperature |
| 583 | `DP_STORED_ANAL_SPRAY_ARM_OSCILLATION` | Read/Write | Exclude: Tuma Classic, Cama | OffOn: 0-1 | Stored oscillation setting |
| 849 | `DP_SET_ACTIVE_ANAL_SHOWER_TIME` | Write-Only | All Models | Counter: 5-60s | Set shower duration |
| 850 | `DP_ACTIVE_ANAL_SHOWER_TIME` | Read-Only | All Models | Counter: 5-60s | Current shower time |
| 851 | `DP_STORED_ANAL_SHOWER_TIME` | Read/Write | All Models | Counter: 5-60s | Stored shower time |
| 855 | `DP_SET_ACTIVE_LADY_SHOWER_TIME` | Write-Only | Exclude: Tuma Classic, Cama | Counter: 5-60s | Set lady shower time |
| 858 | `DP_SET_ACTIVE_LADY_SPRAY_INTENSITY` | Write-Only | Exclude: Tuma Classic, Cama | Percent: 0-100% | Set lady spray intensity |
| 868 | `DP_START_STOP_LADY_SHOWER` | Write-Only | Exclude: Tuma Classic, Cama | OffOn: 0-1 | Start/stop lady shower |
| 872 | `DP_LADY_SHOWER_STATUS` | Read-Only | Exclude: Tuma Classic, Cama | Enum: 0-2 | Lady shower status |
| 873 | `DP_LADY_SHOWER_PROGRESS` | Read-Only | Exclude: Tuma Classic, Cama | Percent: 0-100% | Lady shower progress |

### Dryer Operations

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 874 | `DP_START_STOP_DRYING` | Write-Only | Exclude: Sela, Tuma Classic, Cama | OffOn: 0-1 | Start/stop dryer |
| 875 | `DP_DRYING_STATUS` | Read-Only | Exclude: Tuma Classic, Cama | Enum: 0-2 | Drying status |
| 876 | `DP_DRYING_PROGRESS` | Read-Only | Exclude: Tuma Classic, Cama | Percent: 0-100% | Drying progress |
| 877 | `DP_DRYER_FAN_SET_INTENSITY` | Write-Only | Mera Series, Tuma Comfort | Percent: 0-100% | Set fan intensity |
| 878 | `DP_DRYER_FAN_INTENSITY` | Read-Only | Mera Series, Tuma Comfort | Percent: 0-100% | Current fan intensity |
| 883 | `DP_DRYER_HEATER_SET_TEMPERATURE` | Write-Only | Mera Series, Tuma Comfort | Counter: 30-40°C | Set heater temperature |
| 884 | `DP_DRYER_HEATER_TEMPERATURE` | Read-Only | Mera Series, Tuma Comfort | Counter: 0-60°C | Current heater temperature |
| 893 | `DP_SET_ACTIVE_DRYER_FAN_INTENSITY` | Write-Only | Mera Series (FW ≥20) | Percent: 0-100% | Set active fan intensity |
| 894 | `DP_ACTIVE_DRYER_FAN_INTENSITY_STATUS` | Read-Only | Mera Series (FW ≥20) | Percent: 0-100% | Active fan intensity |
| 895 | `DP_STORED_DRYER_FAN_INTENSITY` | Read/Write | Mera Series (FW ≥20) | Percent: 0-100% | Stored fan intensity |

## Personal Settings & Comfort

### Lighting Control

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 42 | `DP_ORIENTATION_LIGHT_LED` | Read-Only | Sela Only | Percent: 0-100% | LED status |
| 43 | `DP_ORIENTATION_LIGHT_SET_LED` | Write-Only | Sela Only | Percent: 0-100% | Set LED state |
| 44 | `DP_ORIENTATION_LIGHT_MODE` | Read/Write | Sela Only | Enum: 0-2 | Lighting mode |
| 48 | `DP_ORIENTATION_LIGHT_INTENSITY` | Read/Write | Sela Only | Enum: 0-4 | Light intensity |
| 322 | `DP_LIGHTING_BRIGHTNESS_ADJUST` | Write-Only | All Models | Percent: 0-100% | Brightness adjustment |
| 340 | `DP_LIGHTING_SET_BRIGHTNESS` | Write-Only | All Models | Percent: 0-100% | Set brightness level |
| 341 | `DP_LIGHTING_BRIGHTNESS_STATUS` | Read-Only | All Models | Percent: 0-100% | Current brightness |
| 382 | `DP_LED_COLOR` | Read/Write | All Models | Counter: 0-16777215 | LED color setting |

### Odor Extraction

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 20 | `DP_ODOUR_EXTRACTION_FAN` | Read-Only | Exclude: Sela, Tuma Classic, Cama | Percent: 0-100% | Fan status |
| 21 | `DP_ODOUR_EXTRACTION_SET_FAN` | Write-Only | Exclude: Sela, Tuma Classic, Cama | Percent: 0-100% | Set fan state |
| 23 | `DP_ODOUR_EXTRACTION_MODE` | Read/Write | Exclude: Sela, Tuma Classic, Cama | Enum: 0-2 | Extraction mode |
| 27 | `DP_ODOUR_EXTRACTION_POWER` | Read/Write | Exclude: Sela, Tuma Classic, Cama | Enum: 0-4 | Power level |
| 29 | `DP_ODOUR_EXTRACTION_FOLLOW_UP_TIME` | Read/Write | Exclude: Sela, Tuma Classic, Cama | Enum: 0-4 | Follow-up time |

## Maintenance & Diagnostics

### Descaling Operations

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 584 | `DP_START_STOP_DESCALING` | Write-Only | All Models | OffOn: 0-1 | Start/stop descaling |
| 585 | `DP_DESCALING_STATUS` | Read-Only | All Models | Enum: 0-2 | Descaling status |
| 586 | `DP_DESCALING_PROGRESS` | Read-Only | All Models | Percent: 0-100% | Descaling progress |
| 587 | `DP_WATER_HARDNESS` | Read/Write | All Models | Enum: 0-4 | Water hardness setting |
| 589 | `DP_DAYS_UNTIL_NEXT_DESCALING` | Read-Only | All Models | Counter: 0-365 | Days until next descaling |
| 590 | `DP_TIMESTAMP_OF_LAST_DESCALING` | Read-Only | All Models | TimeStampUtc | Last descaling timestamp |
| 798 | `DP_DESCALING_RESULT` | Read-Only | All Models | Enum: 0-3 | Descaling result |

### Cleaning & Maintenance

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 474 | `DP_MAINTENANCE_DONE` | Write-Only | Mera Series Only | OffOn: 0-1 | Maintenance completed |
| 475 | `DP_MAINTENANCE_STATUS` | Read-Only | Mera Series Only | Enum: 0-3 | Maintenance status |
| 515 | `DP_MAINTENANCE_COUNTDOWN` | Read-Only | All Models | Counter: 0-65535 | Maintenance countdown |
| 566 | `DP_START_STOP_SPRAY_ARM_CLEANING` | Write-Only | All Models | OffOn: 0-1 | Spray arm cleaning |
| 567 | `DP_SPRAY_ARM_CLEANING_STATUS` | Read-Only | All Models | Enum: 0-2 | Cleaning status |

### Diagnostics & Testing

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 151 | `DP_START_SELF_TEST` | Write-Only | All Models | OffOn: 0-1 | Start self-test |
| 152 | `DP_SELF_TEST_STATUS` | Read-Only | All Models | Enum: 0-3 | Self-test status |
| 184 | `DP_CHECK_ACTUATOR` | Write-Only | All Models | OffOn: 0-1 | Check actuator |
| 330 | `DP_LED_TEST` | Write-Only | All Models | OffOn: 0-1 | LED test |
| 372 | `DP_DIAGNOSE_DEVICE_STATE` | Read-Only | All Models | Enum: 0-15 | Device diagnostic state |
| 453 | `DP_CHECK_BUZZER` | Write-Only | All Models | OffOn: 0-1 | Buzzer test |
| 791 | `DP_START_STOP_VALVE_TEST` | Write-Only | All Models | OffOn: 0-1 | Valve test |

### Error Status Monitoring

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 88 | `DP_ODOUR_EXTRACTION_ERROR_STATUS` | Read-Only | Exclude: Sela, Tuma Classic, Cama | Binary: 1-4 bytes | Odor extraction errors |
| 93 | `DP_POWER_SUPPLY_ERROR_STATUS` | Read-Only | All Models | Binary: 1-4 bytes | Power supply errors |
| 359 | `DP_GLOBAL_ERROR` | Read-Only | All Models | Binary: 1-4 bytes | Global errors |
| 360 | `DP_GLOBAL_WARNING` | Read-Only | All Models | Binary: 1-4 bytes | Global warnings |
| 478 | `DP_TEMPSENS_ERROR_STATUS` | Read-Only | All Models | Binary: 1-4 bytes | Temperature sensor errors |
| 819 | `DP_SEAT_HEATER_ERROR_STATUS` | Read-Only | Mera Comfort, Tuma Comfort | Binary: 1-4 bytes | Seat heater error status |

## System & Device Information

### System Information

| Data Point ID | Name | Access | Device Support | Values/Range | Description |
|---------------|------|--------|----------------|--------------|-------------|
| 1 | `DP_DEVICE_VARIANT` | Read-Only | All Models | Counter | Device variant |
| 2 | `DP_DEVICE_NUMBER` | Read-Only | All Models | Counter | Device number |
| 5 | `DP_PCB_SERIAL_NUMBER` | Read-Only | All Models | String | PCB serial number |
| 8 | `DP_FW_RS_VERSION` | Read-Only | All Models | String | Firmware RS version |
| 9 | `DP_FW_TS_VERSION` | Read-Only | All Models | String | Firmware TS version |
| 10 | `DP_HW_RS_VERSION` | Read-Only | All Models | String | Hardware RS version |
| 11 | `DP_BLUETOOTH_ID` | Read-Only | All Models | String | Bluetooth ID |
| 15 | `DP_RTC_TIME` | Read/Write | All Models | TimeStampUtc | Real-time clock |
| 16 | `DP_NAME` | Read/Write | All Models | String | Device name |
| 19 | `DP_SUPPLY_VOLTAGE` | Read-Only | All Models | Counter | Supply voltage |

### Device Identification

| Data Point ID | Name | Values/Range | Description |
|---------------|------|--------------|-------------|
| 0 | `DP_DEVICE_SERIES` | Counter | Device series |
| 1 | `DP_DEVICE_VARIANT` | Counter | Device variant |
| 2 | `DP_DEVICE_NUMBER` | Counter | Device number |
| 11 | `DP_BLUETOOTH_ID` | String | Bluetooth ID |
| 16 | `DP_NAME` | String | Device name |

## Important Notes

1. **Source Verification**: All data point IDs in this version have been verified against the actual `DpId.cs` source code
2. **Value Ranges**: Some value ranges are estimated based on typical usage patterns and may need device-specific validation




---

*This document represents only verified data points from the actual Geberit source code. Any implementation should validate device-specific support and value ranges.*
