#!/usr/bin/env bash
# decode-pcapng.sh — decode a Geberit BLE pcapng (HCI btsnoop or nRF sniffer) into
# ATT fields TSV + supporting extracts.  Run from the repo root.
#
# Usage:
#   tools/decode-pcapng.sh <file.pcapng> [output-prefix]
#
# Output files (default prefix = file basename without extension):
#   <prefix>.att-fields.tsv   — all ATT packets, tab-separated fields
#   <prefix>.tshark.txt       — all packets, one-line summary
#
# Requirements: tshark 4.6+ at /Applications/Wireshark.app/Contents/MacOS/tshark

set -euo pipefail

TSHARK="/Applications/Wireshark.app/Contents/MacOS/tshark"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <file.pcapng> [output-prefix]" >&2
    exit 1
fi

INPUT="$1"
if [[ $# -ge 2 ]]; then
    PREFIX="$2"
else
    PREFIX="${INPUT%.*}"
fi

echo "Input:  $INPUT"
echo "Prefix: $PREFIX"
echo ""

# ── 1. one-line summary of every packet ──────────────────────────────────────
echo "==> ${PREFIX}.tshark.txt"
"$TSHARK" -r "$INPUT" 2>/dev/null > "${PREFIX}.tshark.txt"
wc -l "${PREFIX}.tshark.txt"

# ── 2. ATT fields TSV ────────────────────────────────────────────────────────
# All ATT packets with opcode, handle, UUID, value, and human-readable info.
echo ""
echo "==> ${PREFIX}.att-fields.tsv"
"$TSHARK" -r "$INPUT" \
  -Y "btatt" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e _ws.col.Info \
  -e btatt.opcode \
  -e btatt.handle \
  -e btatt.uuid128 \
  -e btatt.uuid16 \
  -e btatt.value \
  -E header=y \
  -E separator='\t' \
  2>/dev/null > "${PREFIX}.att-fields.tsv"
wc -l "${PREFIX}.att-fields.tsv"

# ── 3. HCI connection/disconnect events ──────────────────────────────────────
echo ""
echo "==> Connection timeline:"
"$TSHARK" -r "$INPUT" \
  -Y "bthci_evt.le_meta_subevent == 0x0a || bthci_evt.le_meta_subevent == 0x13 || bthci_evt.code == 0x05" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e _ws.col.Info \
  -E separator='|' \
  2>/dev/null

# ── 4. GATT primary service discovery ────────────────────────────────────────
echo ""
echo "==> Primary service layout (Read By Group Type Responses):"
"$TSHARK" -r "$INPUT" \
  -Y "btatt.opcode == 0x11" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e _ws.col.Info \
  -E separator='|' \
  2>/dev/null

# ── 5. 559eb110 characteristic reads ─────────────────────────────────────────
echo ""
echo "==> 559eb110 reads (handle containing uuid 559eb110):"
"$TSHARK" -r "$INPUT" \
  -Y "btatt.uuid128 contains \"559eb110\"" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e _ws.col.Info \
  -e btatt.opcode \
  -e btatt.handle \
  -e btatt.value \
  -E separator='|' \
  2>/dev/null

# ── 6. Ble20 write commands (ATT Write Command to Geberit write char) ─────────
echo ""
echo "==> Ble20 Write Commands (opcode 0x52):"
"$TSHARK" -r "$INPUT" \
  -Y "btatt.opcode == 0x52" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e btatt.handle \
  -e btatt.value \
  -E separator='|' \
  2>/dev/null

# ── 7. Device Name reads ──────────────────────────────────────────────────────
echo ""
echo "==> Device Name reads (uuid 0x2a00):"
"$TSHARK" -r "$INPUT" \
  -Y "btatt.uuid16 == 0x2a00 || (btatt.opcode == 0x08 && btatt.starting_handle && btatt.ending_handle)" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e _ws.col.Info \
  -e btatt.opcode \
  -e btatt.handle \
  -e btatt.value \
  -E separator='|' \
  2>/dev/null

# ── 8. SMP frames ─────────────────────────────────────────────────────────────
echo ""
echo "==> SMP frames:"
"$TSHARK" -r "$INPUT" \
  -Y "btl2cap.cid == 6" \
  2>/dev/null | head -20 || true

echo ""
echo "Done."
