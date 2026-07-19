# `tools/nrf-ble-analyze.py` — completeness audit (2026-07-18)

## Why this exists

The user asked, more than once, whether `nrf-ble-analyze.py` decodes ALL and EVERYTHING in a
pcapng file. Each time, the answer given was "yes" without ever actually verifying it. On
2026-07-18, a real gap surfaced (the BLE advertising decode was missing a second data
structure) — directly contradicting that assurance. This document records the resulting audit,
so "does it decode everything" has an actual, checkable answer instead of another assertion.

## Root cause — one bug class, two confirmed instances

`_run_tshark()` extracts tshark fields with `-T fields`, which defaults to
`-E occurrence=f` (tshark's "first occurrence only" mode). When tshark reports **more than
one** occurrence of a queried field within a single matched frame, `occurrence="f"` silently
returns just the first — the rest is lost with no error, no warning, nothing.

At the time of the audit, **1 of 17** `_run_tshark()` call sites in the tool used
`occurrence="a"` (all occurrences). The other 16 used the default. Two of those were confirmed,
by direct testing against real captures, to be silently dropping real data:

### 1. BLE advertising (`--adv`)

The real Mera Comfort sends its manufacturer-specific data as **two separate packets**, not
one — see `docs/developer/mera-home-app-onboarding.md` for the full byte-level evidence. The
tool only ever queried the first `company_id`/`data` occurrence, so the second
(SCAN_RSP-carried) RS-firmware-version tail was invisible. **Fixed** by switching the ADV_IND
field extraction to `occurrence="a"` and printing every Manufacturer Specific Data entry found,
not just the first.

### 2. GATT discovery (`--gatt-map`, `_extract_gatt_handles`)

`READ_BY_GROUP_TYPE_RSP` (0x11), `READ_BY_TYPE_RSP` (0x09), and `FIND_INFO_RSP` (0x05) are all
designed to pack **multiple** handle/UUID pairs into one response PDU whenever the MTU allows —
this is the common case for any service/characteristic list longer than one item, not an edge
case. Direct testing against `onboarding-real-mera.pcapng`:

| Opcode | Matching frames | Frames with >1 entry (silently truncated to 1) |
|---|---|---|
| `0x11` READ_BY_GROUP_TYPE_RSP | 4 | 2 |
| `0x09` READ_BY_TYPE_RSP | 26 | 25 |
| `0x05` FIND_INFO_RSP | 10 | 8 |

The existing code even had `handles_raw.split(",")` logic already written, anticipating
comma-joined multi-value output — it just never received any, because the underlying
`_run_tshark()` call never used `occurrence="a"`.

## Why the fix is NOT just "add `occurrence=\"a\"`"

Naively switching `_extract_gatt_handles()` to `occurrence="a"` and trusting the comma-split
logic would have produced a **wrong** result, not just an incomplete one. Example: for a
3-service `READ_BY_GROUP_TYPE_RSP` frame, querying `btatt.uuid16` with `occurrence="a"` returns
`[0x1800, 0x1801, 0x180a, 0x2800]` — **four** values for **three** services. The extra
`0x2800` ("Primary Service") is the *attribute type being queried*, echoed at a different tree
depth in the same frame — not a fourth service's UUID. `-T fields` flattens by field name
regardless of nesting depth, so it can't distinguish a field's real per-entry value from an
unrelated field that happens to share the same name deeper or shallower in the same frame.
`-T json` doesn't help either — tshark's JSON export collapses repeated same-named sibling
elements (e.g. multiple `attribute_data` blocks) down to just the last one.

**The fix**: `_extract_gatt_handles()` was rewritten to use `-T pdml` (tshark's XML tree
output, via the new `_run_tshark_pdml()` helper) and walk each `attribute_data` /
`information_data` sibling element's *direct children* explicitly. PDML is the only tshark
output format that preserves the real tree structure without collapsing or flattening —
confirmed correct against real captures (32 handles now extracted from
`onboarding-real-mera.pcapng`, up from ~3 before).

## What got fixed vs. what got a plain warning instead

| Call site | Risk | Fix |
|---|---|---|
| `_extract_gatt_handles` (`--gatt-map`) | Confirmed wrong pairing if naively fixed | Rewritten with `-T pdml` tree-walking |
| `_get_geberit_traffic` / `_decode_gatt_frame` | Only queries `btatt.handle` (no UUID field) for 0x05/0x07/0x11 — safe under `occurrence="a"` | Switched to `occurrence="a"`; existing comma-split logic now actually fires; 0x07 branch extended to show all found handles, not just the first |
| Mera ATT-events extractor (`_extract_mera_events`, opcode `0x09`) | `btatt.uuid16` has the same tree-depth trap as `_extract_gatt_handles` for this opcode | Left at `occurrence="f"`, but no longer silently shows a truncated single value — now explicitly flags "(multi-entry characteristic discovery — see `--gatt-map`)" |

The other ~13 call sites were checked (not just assumed) and are genuinely safe: either the
filter matches exactly one frame by construction (e.g. `frame.number == 1`), or the queried
fields are single-valued by protocol structure (LL control PDUs, CONNECT_IND/ADV_DIRECT_IND
fields, MTU exchange, L2CAP connection-parameter signaling, Alba's write/notify-only extractor,
raw `scan_response_data` byte parsing).

## The regression check: `tools/audit-nrf-ble-analyze-coverage.py`

A one-time manual audit doesn't answer "does it still decode everything" for the *next*
capture or the *next* code change. `tools/audit-nrf-ble-analyze-coverage.py`:

1. Monkeypatches `_run_tshark()` to record every `(display_filter, fields, occurrence)` tuple
   actually invoked while driving the tool through its main code paths (`--markdown`,
   `--gatt-map`, `--adv`, default) against a corpus of real captures. This auto-tracks every
   *current* call site with no hand-maintained list, and will pick up future ones too.
2. For every recorded call still using the default `occurrence="f"`, re-runs the same query
   with `occurrence="a"` and flags any field that returns more than one comma-joined value in
   any row.

Run it:
```bash
python tools/audit-nrf-ble-analyze-coverage.py                    # default corpus (below)
python tools/audit-nrf-ble-analyze-coverage.py capture1.pcapng ... # explicit captures
```

Default corpus: `onboarding-real-mera.pcapng`,
`firmware-update-mera-comfort/firmware-update-vom-mac.pcapng`,
`firmware-update-mera-comfort/firmware-update-von-windows.pcapng`.

**What it is not**: an auto-fixer, or a pass/fail guarantee that a flagged candidate is a real
bug. As shown above, a field CAN legitimately return multiple comma-joined values that are
still wrong to use directly (the tree-depth-conflation trap) — every candidate this script
surfaces needs the same kind of manual `-T pdml` verification that found the original two
bugs. Confirmed working as intended: running it against the three captures above currently
flags exactly the one call site (the Mera `0x09` handler) that was deliberately left at
`occurrence="f"` for that reason — nothing more, nothing less.

## Applied to (2026-07-18)

Re-ran `--markdown` with the fixed tool against all three captures in the default corpus,
overwriting their `.md` companions:
- `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/onboarding-real-mera.md`
- `local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/firmware-update-vom-mac.md`
- `local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/firmware-update-von-windows.md`

Spot-checked: GATT discovery log lines that previously would have shown only the first handle
(e.g. `services=0x001E`) now correctly show every handle in the frame (e.g.
`services=0x001E  0x0029  0x002D`), while genuinely single-entry frames are unchanged.

## Unrelated bug class found 2026-07-19 — mac fallback and no-ATT-frames early return

Found while investigating RC pairing captures in `geberit-remote-control/`
(`docs/developer/ble-advertising-button-press-confirmation.md` § "Source 5"). Unrelated to the
`occurrence="f"` bug class above — a different mechanism, found independently.

### 1. Empty MAC not falling back to `DEFAULT_MAC`

When device auto-detection fails, `main()` passes `mac=""` into `_analyze_mera()`. Four call
sites downstream used this empty string directly instead of falling back to `DEFAULT_MAC`
(`38:AB:41:2A:0D:67`), the way `_get_adv_packets()` already did:
- `_get_connection_events(tshark, pcapng, mac)` — filters `CONNECT_IND` by exact MAC match; an
  empty string matches nothing, silently producing "No CONNECT_IND ... found" even when the
  capture clearly contains the toilet's real connection events.
- `_render_ll_encryption_markdown(enc, pcapng, mac, ...)` — rendered `**Device:** \`\`` (blank)
  in the header.
- `_android_ble.render_markdown_android(...)` and `_print_mera_table(...)` — same blank-MAC
  header, in the main (ATT-events-found) code path.

**Fix**: all four call sites now use `mac or DEFAULT_MAC`. Commits `f8f381a`, `4a8fd52`.

### 2. No-ATT-frames-found path discarded computed advertising/connection data

When a capture has no decodable Geberit ATT frames and no `LL_ENC_REQ`, `_analyze_mera()`
printed only `"No Geberit ATT frames found"` and returned — even when `--include-adv` had
already computed a usable advertising section, or real `CONNECT_IND`/`ADV_DIRECT_IND` events
existed for the target MAC. Both were silently discarded.

**Fix**: added a fallback that renders the advertising section and/or connection events
instead, with a note explaining the likely cause (sniffer didn't follow the data channel's
hopping sequence, or the connection closed before any GATT activity). Commit `f8f381a`.

### Files found affected and fixed

A repo-wide grep for the bug's signature (a blank `Device:` MAC, or the literal text "No
CONNECT_IND or ADV_DIRECT_IND frames found") found 5 more pre-existing `.md` files affected
outside the RC directory (none git-tracked — all under gitignored `local-assets/`). Regenerated
4 with the fixed tool:
- `geberit-home-app/onboard-Geberit-Home-App_against_mera-mock_v1.68.0b1_1.md`
- `geberit-home-app/onboard-Geberit-Home-App_against_mera-mock_v1.65.0b1_1.md`
- `geberit-home-app/onboard-Geberit-Home-App_against_mera-mock_v1.65.0b1.md`
- `firmware-update-mera-comfort/firmware-update-against-mera-mock/onboarding-and-firmware-update-against-mera-mock.md`

**Left stale, not regenerated**: `geberit-home-app/compare-mock_v1.68.0b1-vs-real-mera.md` — a
derived diff (via `tools/compare-nrf-md.py`) of the first file above against
`onboarding-real-mera.md`. Regenerating it correctly requires knowing the original `--from`/
`--to` filter flags used, which weren't recorded anywhere; guessing them risked producing a
subtly wrong comparison, so it was left as-is and flagged here instead of silently going stale
with no record.
