# ble-decode.py — Geberit AquaClean BLE Decoder

Decodes raw **Xcode PacketLogger `.txt` exports** from an iPhone into human-readable
Geberit GATT frames. Understands the full protocol stack: frame assembly, procedure
names, command codes, and profile setting names.

---

## Capture setup

- iPhone connected via USB to a Mac running **Xcode PacketLogger**
- Bluetooth profile installed on the iPhone to enable HCI-level capture
- iPhone physically close to the Geberit toilet (BLE in range)
- Log file = raw PacketLogger `.txt` export
- The PacketLogger Mac is separate from the `/Users/jens` dev Mac

---

## Usage

```bash
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py <logfile.txt> [options]
```

### Options

| Option | Description |
|--------|-------------|
| `--mac MAC` | Filter to one device MAC (default: `38:AB:41:2A:0D:67`) |
| `--from HH:MM:SS` | Include only lines at or after this time |
| `--to HH:MM:SS` | Include only lines at or before this time |
| `--filter PROC` | Show only frames matching procedure name or hex code (e.g. `GetStoredCommonSetting` or `0x51`) |
| `--firmware` | Shorthand for `--filter 0x0E` (GetFirmwareVersionList) |
| `--decode-fw` | Pretty-print firmware version records from GetFirmwareVersionList responses |
| `--filter-status` | Shorthand for `--filter 0x59` (GetFilterStatus) |
| `--decode-filter` | Pretty-print filter status records from GetFilterStatus responses |
| `--impl` | After each decoded procedure, show Python CallClass implementation hint |
| `--verbose` | Also print raw result hex bytes for every response |
| `--raw` | Print every raw 20-byte Geberit frame without decoding |
| `--markdown` | Render the full session as annotated markdown, grouped by logical phase |
| `--output FILE` | Write markdown output to FILE instead of stdout (requires `--markdown`) |

### Examples

```bash
# Decode everything
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt

# Different device MAC
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --mac AA:BB:CC:DD:EE:FF

# Time-slice a long session
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --from 13:05:00 --to 13:06:30

# Focus on one procedure by name or hex code
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --filter GetStoredCommonSetting
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --filter 0x51

# Show raw result bytes for every response
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --verbose

# Show raw 20-byte frames (useful when investigating new/unknown frame types)
/Users/jens/venv/bin/python tools/ble-decode.py session.txt --raw

# Annotated markdown — full session grouped by logical phase (stdout or file)
/Users/jens/venv/bin/python tools/ble-decode.py session.txt --markdown
/Users/jens/venv/bin/python tools/ble-decode.py session.txt --markdown --output session-analysis.md

# Firmware version list (shorthand + parsed records)
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --firmware
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --firmware --decode-fw

# Filter status (shorthand + parsed records)
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --filter-status
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --filter-status --decode-filter

# Show Python implementation hints after each procedure (useful when reverse-engineering a new feature)
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --impl
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py session.txt --filter 0x51 --impl
```

---

## Output format

```
HH:MM:SS.mmm  →  WRITE_0   REQ  #58596  GetDeviceIdentification
HH:MM:SS.mmm  ←  READ_0    RESP #    0  GetDeviceIdentification  OK result=3134362e...
HH:MM:SS.mmm  →  WRITE_0   REQ  #34694  GetSOCApplicationVersions
HH:MM:SS.mmm  ←  READ_0    RESP #62913  GetSOCApplicationVersions  OK → SOC 10.18
HH:MM:SS.mmm  →  WRITE_0   REQ  #38574  GetStoredProfileSetting → AnalShowerPressure
HH:MM:SS.mmm  ←  READ_0    RESP #60913  GetStoredProfileSetting  OK result=0200
```

- `→ WRITE_x` — app → device (ATT Write Without Response)
- `← READ_x` — device → app (ATT Handle Value Notification)
- `REQ #N` / `RESP #N` — rolling request counter from the protocol (0 = multi-frame assembled body, counter not in header)
- `OK` / `ERR=0xNN` — response status byte

---

## Frame types handled

| Type | Description | Action |
|------|-------------|--------|
| `SINGLE` | Complete one-frame message (most commands) | Decoded immediately |
| `FIRST[N]` + `CONS` | App-originated multi-frame message | Assembled then decoded |
| `FIRST_DEV` + `CONS_DEV` | Device-originated multi-frame response (header 0x30-style) | Assembled then decoded |
| `CONTROL` | ACK / flow-control frame | Silently skipped (visible with `--raw`) |
| `INFO` | Info flood sent by device at connect | Silently skipped (visible with `--raw`) |

Multi-frame assembly is keyed separately per direction (`WRITE` vs `READ`) to prevent
cross-contamination between outgoing CONS frames on WRITE_1+ and incoming device responses.

---

## Known procedures

| Context | Proc | Name | Request args | Response |
|---------|------|------|-------------|----------|
| 0x00 | 0x82 | `GetDeviceIdentification` | none | device info blob |
| 0x00 | 0x86 | `GetDeviceInitialOperationDate` | none | date blob |
| 0x01 | 0x05 | `UnknownProc_0x05` | none | blob (seen at connect) |
| 0x01 | 0x07 | `UnknownProc_0x07` | 1 byte | — |
| 0x01 | 0x09 | `SetCommand` | 1 byte = command code | OK/ERR |
| 0x01 | 0x0A | `GetStoredProfileSetting` | 1 byte = index | 2 bytes |
| 0x01 | 0x0B | `SetStoredProfileSetting` | 2 bytes = index, value | OK/ERR |
| 0x01 | 0x0D | `GetSystemParameterList` | count + indices | values |
| 0x01 | 0x0E | `GetFirmwareVersionList` | count + component IDs | 5-byte records |
| 0x01 | 0x45 | `GetStatisticsDescale` | none | struct |
| 0x01 | 0x51 | `GetStoredCommonSetting` | 1 byte = setting ID | 2 bytes |
| 0x01 | 0x53 | `GetStoredProfileSetting_C#` | 1 byte = index | 2 bytes |
| 0x01 | 0x54 | `SetStoredProfileSetting_C#` | 2 bytes | OK/ERR |
| 0x01 | 0x56 | `SetDeviceRegistrationLevel` | 1 byte | OK/ERR |
| 0x01 | 0x59 | `GetFilterStatus` | count + record IDs | 5-byte records `[ID][uint32 LE]` |
| 0x01 | 0x81 | `GetSOCApplicationVersions` | none | `SOC X.Y` |
| 0x01 | 0x11 | `Proc(0x01,0x11)` | count + component IDs | version strings |
| 0x01 | 0x13 | `Proc(0x01,0x13)` | count + component IDs | setting list data |

### SetCommand command codes

| Code | Name |
|------|------|
| 0x00 | `ToggleAnalShower` |
| 0x01 | `ToggleLadyShower` |
| 0x02 | `ToggleDryer` |
| 0x04 | `StartCleaningDevice` |
| 0x05 | `ExecuteNextCleaningStep` |
| 0x06 | `PrepareDescaling` |
| 0x07 | `ConfirmDescaling` |
| 0x08 | `CancelDescaling` |
| 0x09 | `PostponeDescaling` |
| 0x0A | `ToggleLidPosition` |
| 0x14 | `ToggleOrientationLight` |
| 0x21 | `StartLidPositionCalibration` |
| 0x22 | `LidPositionOffsetSave` |
| 0x23 | `LidPositionOffsetIncrement` |
| 0x24 | `LidPositionOffsetDecrement` |
| 0x25 | `TriggerFlushManually` |
| 0x2F | `ResetFilterCounter` |

The `ResetFilterCounter` command is what the iOS app sends when the user taps "Confirm" in the filter-exchange dialog. It atomically: sets days remaining → 365, updates last-reset timestamp → now, clears next-change timestamp → 0, increments reset count by 1.

### GetFilterStatus record IDs (proc 0x59)

Request payload: `[count][id0][id1]…` — iOS app requests IDs 0–10.
Response: N records of 5 bytes each: `[ID (1 byte)][value uint32 LE (4 bytes)]`

| Record ID | Name | Notes |
|-----------|------|-------|
| 0 | status | = 1 |
| 1 | shower_cycles | shower cycle counter |
| 2 | unknown_02 | |
| 3 | unknown_03 | |
| 4 | unknown_ts_04 | Unix timestamp |
| 5 | unknown_05 | |
| 6 | unknown_06 | |
| **7** | **days_until_filter_change** | 0 = exchange now; 365 = just reset |
| **8** | **last_filter_reset** | Unix timestamp of last reset |
| 9 | next_filter_change | Unix timestamp; 0 after reset |
| **10** | **filter_reset_count** | Total resets performed |

### GetStoredProfileSetting indices

| Index | Name |
|-------|------|
| 0 | `OdourExtraction` |
| 1 | `OscillatorState` |
| 2 | `AnalShowerPressure` |
| 3 | `LadyShowerPressure` |
| 4 | `AnalShowerPosition` |
| 5 | `LadyShowerPosition` |
| 6 | `WaterTemperature` |
| 7 | `WcSeatHeat` |
| 8 | `DryerTemperature` |
| 9 | `DryerState` |
| 10 | `SystemFlush` |

---

## GATT handles

| Handle | Channel | Direction |
|--------|---------|-----------|
| 0x0003 | WRITE_0 | app → device (SINGLE / FIRST frames) |
| 0x0006 | WRITE_1 | app → device (CONS frames) |
| 0x0009 | WRITE_2 | app → device (CONS frames) |
| 0x000C | WRITE_3 | app → device (CONS frames) |
| 0x000F | READ_0  | device → app (notifications) |
| 0x0013 | READ_1  | device → app |
| 0x0017 | READ_2  | device → app |
| 0x001B | READ_3  | device → app |

---

## Workflow: reverse-engineer a new feature from a new log

**Step 1 — Capture**
Open the iOS Geberit Home app, perform the feature you want to reverse-engineer
(e.g. change water hardness, read filter status, trigger descaling). Export the
PacketLogger log.

**Step 2 — Full decode pass**
```bash
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py newlog.txt > decoded.txt
```
Look for `Proc(0x01,0xXX)` entries — those are procedures the iOS app calls that are
not yet named or implemented in our codebase.

**Step 3 — Investigate with filters**
```bash
# Isolate an unknown procedure by hex code and see full result bytes
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py newlog.txt --filter 0xNN --verbose

# For entirely unknown frame shapes, inspect raw bytes
/Users/jens/venv/bin/python local-assets/Bluetooth-Logs/ble-decode.py newlog.txt --raw
```

**Step 4 — Update this decoder**
Once the procedure's name and response format are understood:
1. Add `(ctx, proc): "NewProcedureName"` to `PROCEDURES` in `ble-decode.py`
2. If the request args have structure, add a decode branch in `_fmt_request()`
3. If the response has structure (e.g. enum, uint16 LE), add a decode branch in `_fmt_response()`
4. Update the procedure table in this file (`ble-decode.md`)

**Step 5 — Implement in the main codebase**
Follow the "all interfaces" rule — every new procedure needs:

| Layer | File | What to do |
|-------|------|-----------|
| CallClass | `aquaclean_core/Api/CallClasses/` | New file, same pattern as `GetStatisticsDescale.py` |
| Client | `AquaCleanClient.py` | Add `get_xxx_async()` method |
| REST | `RestApiService.py` / `main.py` | Add GET endpoint |
| MQTT | `main.py` `send_data_async()` | Publish value; update HA Discovery |
| CLI | `main.py` / `__main__.py` | Add `--command` (keep both parsers in sync) |
| HACS sensor | `custom_components/.../sensor.py` | Add `SensorEntity` |

---

## Known limitations

- **`--decode-fw` firmware record display**: FW01 decodes correctly (`"28" build=199` →
  iOS shows "RS28.0 TS199"). Records FW03+ have alignment issues in the pretty-printer;
  the raw `result=` hex in the base output is always correct.
- **Time filter granularity**: `--from` / `--to` match to the second (milliseconds ignored).
- **Single MAC per run**: only one `--mac` filter supported; run separately for multi-device logs.
- **Firmware update availability is NOT in the BLE log.** PacketLogger captures only
  Bluetooth HCI/L2CAP/ATT traffic between the iPhone and BLE peripherals. The "new firmware
  available" notification (e.g. RS30.0 TS206) comes from Geberit's cloud servers to the iOS
  app over WiFi/cellular — a completely separate TCP/HTTPS stack that PacketLogger never sees.
  `GetFirmwareVersionList` (proc 0x0E) always returns the firmware currently *installed* on the
  device, not what is available as an update. To capture cloud communication you would need a
  different tool (Charles Proxy, mitmproxy, or iOS HTTP Instruments with MITM).
