# BLE Procedure Probe — Findings

This document records the results of brute-forcing the API-layer procedure code space
using `operation_support/ble_procedure_probe.py`.

---

## Methodology

The probe sends an empty-payload BLE request for each procedure code in a configurable
range using `ApiCallAttribute(context=0x01, procedure=0xXX, node=0x01)` — the same
framing as all known call classes.  Raw response bytes are captured from
`AquaCleanBaseClient.message_context.result_bytes`.

**Important behaviour observed:** the device (Geberit AquaClean Mera Comfort,
`38:AB:41:2A:0D:67`) drops the BLE connection after receiving an unknown procedure code.
The probe must reconnect (with a configurable delay) between each failed probe.

---

## Run 1 — 2026-02-22, range 0x40–0x4A (interrupted)

| Code | Status | Bytes | Raw hex | Notes |
|------|--------|------:|---------|-------|
| `0x40` | OK | 1 | `00` | See analysis below |
| `0x41` | OK | 1 | `01` | See analysis below |
| `0x42` | ERR | — | timeout | Device dropped BLE |
| `0x43` | ERR | — | timeout | Device dropped BLE |
| `0x44` | ERR | — | timeout | Device dropped BLE |
| `0x45` | ERR | — | timeout | Known: GetStatisticsDescale — timed out because prior disconnect not recovered |
| `0x46`–`0x4A` | ERR | — | timeout | All cascading from same disconnect |

Probe interrupted at `0x4A` (^C). Remaining range `0x4B`–`0x60` not yet tested.

---

## Analysis of `0x40` and `0x41`

### Cross-reference with thomas-bingel C# repo

A full search of the [thomas-bingel/geberit-aquaclean](https://github.com/thomas-bingel/geberit-aquaclean)
C# repository shows:

- **`0x40` is NOT a documented API procedure code.**
  It appears only as a BLE *frame layer* constant in `FrameFactory.cs`:
  `0x40` is the lower bound of header byte values that map to **CONS frame type** (consecutive
  frame in a multi-frame transmission). The frame type is extracted as `(headerByte >> 5) & 7`,
  so any header byte in `0x40`–`0x5F` maps to frame type 2 (CONS).
  The `00` response byte may be a protocol-level artefact rather than application data.

- **`0x41` does not appear anywhere** in the C# repository — not as a procedure code, command
  value, profile setting, frame byte, or any other constant.

### Conclusion

`0x40` and `0x41` are likely **not real application-layer procedures**.  Their single-byte
responses (`00`, `01`) may reflect BLE framing behaviour rather than device application data.

**To confirm:** probe these codes multiple times while changing device state (lid open/closed,
user sitting/not sitting) and check whether the response bytes change.  If they are always
`00` and `01` regardless of state, they are almost certainly protocol artefacts.

---

## Complete known procedure code map (as of 2026-02-22)

| Procedure | Call | Implemented |
|-----------|------|:-----------:|
| `0x05` | `GetNodeList()` | ❌ |
| `0x08` | `SetActiveProfileSetting(profileSettingId, value)` | ❌ |
| `0x09` | `SetCommand(command)` | ✅ |
| `0x0D` | `GetSystemParameterList(params)` | ✅ |
| `0x0E` | `GetFirmwareVersionList(arg1, arg2)` | ❌ |
| `0x45` | `GetStatisticsDescale()` | ✅ |
| `0x51` | `GetStoredCommonSetting(storedCommonSettingId)` | ❌ |
| `0x53` | `GetStoredProfileSetting(profileId, setting)` | ❌ (migrated, not wired) |
| `0x54` | `SetStoredProfileSetting(profileId, setting, value)` | ❌ (migrated, not wired) |
| `0x56` | `SetDeviceRegistrationLevel(registrationLevel)` | ❌ |
| `0x81` | `GetSOCApplicationVersions()` | ✅ |
| `0x82` | `GetDeviceIdentification()` | ✅ |
| `0x86` | `GetDeviceInitialOperationDate()` | ✅ |

---

## Next steps

1. Re-run the probe for `0x45` (GetStatisticsDescale) in isolation as a sanity check — it should return 16 bytes of descale statistics.
2. Continue the probe for range `0x4B`–`0x60` (not yet tested).
3. Probe `0x05`, `0x08`, `0x0E` to confirm they respond and understand their return format.
4. Probe `0x51` with varying payload values to map `storedCommonSettingId` → device settings.
5. Confirm or rule out `0x40`/`0x41` as real procedures by testing across device states.
