# Android BLE Log Format

This document describes the Android `BTSNOOP_LOG_SUMMARY` file format as
reverse-engineered from a Samsung Redmi Note 9 capture (2026-04-21), and
explains how to use `tools/android-ble-analyze.py` to extract HCI packets
from it.

---

## Obtaining Android BLE Logs

1. Enable Developer Options on the Android device (Settings → About phone →
   tap Build number 7×).
2. Enable **Bluetooth HCI snoop log** (Settings → Developer options →
   Bluetooth HCI snoop log).
3. Perform the BLE session you want to capture.
4. Pull the log file. Location varies by Android version and vendor:
   - Android 11+: the file is embedded in a **bug report** (Settings →
     Developer options → Bug report). Extract the ZIP and look for
     `bt_snoop_hci.log` or `BTSNOOP_LOG_SUMMARY` inside it.
   - Older Android / some vendors: `/data/misc/bluetooth/logs/btsnoop_hci.log`
     (requires root or ADB with sufficient permissions).
   - The `BTSNOOP_LOG_SUMMARY` wrapper described below is what appears inside
     Android bug report ZIPs.

---

## BTSNOOP_LOG_SUMMARY Wrapper Format

The file is plain-text ASCII:

```
--- BEGIN:BTSNOOP_LOG_SUMMARY (262112 bytes in) ---
<base64 encoded payload>
--- END:BTSNOOP_LOG_SUMMARY ---
```

The number in parentheses (`262112 bytes in`) is the size of the original
binary data **before compression** — useful as a sanity check but not needed
for decoding.

### Decode recipe

1. Extract the base64 block between the `BEGIN` and `END` markers.
2. Base64-decode it to get a binary blob.
3. **Skip the first 9 bytes** — a custom prefix with format/version metadata
   (exact meaning unknown; confirmed bytes: `02 00 e3 2d 9a 1d b5 da 98`).
4. The remaining bytes form a standard zlib stream (magic `78 9C`).
5. Zlib-decompress. The result is the packet stream described below.

```python
import base64, zlib

raw = base64.b64decode(base64_block)
packet_stream = zlib.decompress(raw[9:])
```

---

## Custom Binary Record Format

The decompressed data is **not** a standard BTSnoop binary (no `btsnoop\0`
magic header). Instead it uses a headerless custom record format:

```
[inc_len:  2 bytes LE]   — payload length in bytes
[orig_len: 2 bytes LE]   — same as inc_len for all valid records
[flags:    4 bytes LE]   — direction flag (see below)
[payload:  inc_len bytes]
```

Each record is `8 + inc_len` bytes. Records repeat until end of stream.

There is **no file-level header** — the stream begins immediately with the
first record.

### Direction flags

| `flags` value  | Direction |
|----------------|-----------|
| `0x00000000`   | RX (device → host) |
| `0x01000000`   | TX (host → device) |

### Payload type bytes (non-standard)

The first byte of each payload indicates the HCI packet type. Android uses
non-standard values here (standard HCI UART uses different codes):

| First byte | Packet type |
|------------|-------------|
| `0x10`     | HCI Event |
| `0x20`     | HCI ACL data |

HCI commands are embedded as ACL-type (`0x20`) TX records rather than as a
separate command type.

### No timestamps

This format drops per-packet timestamps entirely. Standard BTSnoop has 8-byte
timestamps per record; this format does not. Relative timing of packets
**cannot** be recovered from this file.

---

## Differences from Standard BTSnoop

| Feature | Standard BTSnoop | Android BTSNOOP_LOG_SUMMARY |
|---------|------------------|-----------------------------|
| File magic | `btsnoop\0` | None (no file header) |
| Compression | None | zlib (with 9-byte prefix) |
| Timestamps | 8 bytes per record | None |
| HCI type byte `0x02` | HCI ACL | Not used |
| HCI type byte `0x04` | HCI Event | Not used |
| HCI type byte `0x10` | Not used | HCI Event |
| HCI type byte `0x20` | Not used | HCI ACL (incl. commands) |
| Direction field | 4 bytes LE | Same |

The `tools/android-ble-analyze.py` script handles both formats via
auto-detection:
- `BTSNOOP_LOG_SUMMARY` detected by the `--- BEGIN:BTSNOOP_LOG_SUMMARY`
  marker.
- Standard BTSnoop detected by the `btsnoop\0` magic at offset 0.

---

## Analysis Tool

```
tools/android-ble-analyze.py
```

Parses both Android `BTSNOOP_LOG_SUMMARY` and standard binary BTSnoop files.

### Usage

```bash
# Analyze all BLE traffic in a log
python tools/android-ble-analyze.py local-assets/Android-BLE-Logs/BTSNOOP_LOG.log

# Filter to a specific device MAC
python tools/android-ble-analyze.py file.log --mac 38:AB:41:2A:0D:67

# List all BLE MACs seen (without filtering)
python tools/android-ble-analyze.py file.log --all-macs
```

The tool outputs:
- Total HCI packet count
- List of unique advertising BLE device addresses seen
- Per-device packet details when `--mac` is specified

---

## Limitations

- **No timestamps** — cannot determine exact timing between events.
- **HCI commands partially parseable** — they arrive as ACL-type TX records
  rather than as a dedicated command type; the tool decodes them as ACL but
  may misinterpret the structure for non-standard payloads.
- **No L2CAP/ATT reassembly** — fragmented GATT packets are not reassembled.
  For full GATT decode (characteristic reads/writes), use the iOS
  PacketLogger decoder (`local-assets/Bluetooth-Logs/ble-decode.py`) on a
  corresponding iPhone capture.
- **Passive advertising only** — if the Android phone was scanning passively,
  the log contains advertisement reports but no connection-layer traffic
  unless the phone actively connected to the device.

---

## First Android Log Analyzed

**File:** `local-assets/Android-BLE-Logs/BTSNOOP_LOG_2026-04-20.log`
**Device:** Samsung Redmi Note 9
**Date captured:** 2026-04-20
**Analysis date:** 2026-04-21

Results:
- 7,016 HCI packets decoded
- 44 unique BLE devices seen advertising
- Geberit device `38:AB:41:2A:0D:67` **not present** — phone was scanning
  but did not connect to the toilet during this session
- Device name "Redmi Note 9" visible in ACL TX data
- This log was used to validate the format decoder; no Geberit protocol data
  was present

---

## See Also

- `memory/android-ble-log-format.md` — quick-reference decode recipe
- `memory/ble-traffic-logs.md` — index of all captured BLE logs
- `local-assets/Bluetooth-Logs/ble-decode.py` — iOS PacketLogger decoder
  (for iPhone captures; separate format, includes timestamps and full GATT)
- `local-assets/Bluetooth-Logs/ble-decode.md` — iOS decoder documentation
