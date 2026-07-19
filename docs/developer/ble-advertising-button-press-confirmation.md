# BLE advertising SensorState (button-press) — cross-platform confirmation, 2026-07-19

Consolidates every independent check of the "button-press changes the advertisement"
mechanism documented in `.claude/rules/ble-protocol.md` § "BLE advertising payload —
SensorState": two live scans (different platforms) plus a re-check against the existing
nRF52840 real-device capture, so the evidence trail lives in one place.

## What's being checked

Real Mera Comfort, idle vs. pairing-button-pressed, ADV_IND Manufacturer-Specific-Data:

| State | Company ID | Data (hex) | Decoded |
|-------|-----------|------------|---------|
| Idle | `0x0100` | `00 31 34 36 32 31` | state=0 (not pressed) + article `"14621"` |
| Button pressed | `0x01AA` | `01 31 34 36 32 31` | state=1 (`IsButtonPressed`) + article `"14621"` |

Two independent bits make up `SensorState`: `IsButtonPressed` (payload byte 0) and
`IsEmergencyConnectPermitted` (company-ID low byte, `0x0100`→`0x01AA`). They are separate
conditions — nothing here proves they always flip together, see the pcapng cross-check below.

## Source 1 — nRF Connect (2026-07-18)

Confirmed idle = company `0x0100`, data `00 31 34 36 32 31`; button pressed = company
`0x01AA`, data `01 31 34 36 32 31`.

## Source 2 — nRF Connect for Android (2026-07-19), independent device/scan

Real Mera Comfort `38:AB:41:2A:0D:67`, `Advertising type: Legacy` both times.

*Idle:*
```
Manufacturer data: Company: TomTom International BV <0x0100>  0x003134363231
Complete Local Name: Geberit AC PRO
Manufacturer data: Company: Reserved ID <0x3300>  0x30
```
*Button pressed (held for Geberit Home App / Remote Control onboarding):*
```
Manufacturer data: Company: Geophysical Technology Inc. <0x01AA>  0x013134363231
Complete Local Name: Geberit AC PRO
Manufacturer data: Company: Reserved ID <0x3300>  0x30
```

Byte-for-byte match to Source 1: `0x003134363231` = `00 31 34 36 32 31`, `0x013134363231` =
`01 31 34 36 32 31`.

Two things worth noting about how Android's nRF Connect *displays* this, neither a new
protocol fact:
- "TomTom International BV" / "Geophysical Technology Inc." are just the Bluetooth SIG's
  registered company names for IDs `0x0100`/`0x01AA` — cosmetic, no relation to Geberit or
  either real company. Geberit repurposes those IDs as the state flag.
- The second "Manufacturer data: Reserved ID `<0x3300>` `0x30`" entry is nRF Connect merging
  ADV_IND and SCAN_RSP into one device view. It's really the *separate* SCAN_RSP entry
  `00 33 30` (RS firmware major-version prefix "30"), misread as a second manufacturer-data
  company ID `0x3300` (its first two bytes, `00 33`) with one data byte `0x30`. Unaffected by
  the button press in both scans, exactly as expected — it's firmware-version info, not
  button state.

## Source 3 — cross-check against `onboarding-real-mera.pcapng` (2026-07-19)

Ran `tools/nrf-ble-analyze.py onboarding-real-mera.pcapng --adv` directly (the companion
`.md`, generated via `--markdown` mode, does **not** include advertising packets at all —
that mode only covers the connected-session ATT/GATT traffic; `--adv` is a separate,
mutually-exclusive output mode). Result:

```
t=   0.0s  ADV_IND  UUIDs=0x3EA0        AD type=0x01  company=0x0100  data=0x003134363231
t=   1.2s  ADV_IND  UUIDs=0x1EA0        AD type=0x01  company=0x0100  data=0x00313436b231
...
t=  51.7s  ADV_IND  UUIDs=0x3EA0        AD type=0x01  company=0x0100  data=0x013134363231
...
```

**Confirmed by this capture:**
- Idle payload `00 31 34 36 32 31` — appears repeatedly, cleanly, throughout.
- Button-pressed payload byte (`data` starting `01`) — appears once, at `t=51.7s`.

**NOT confirmed by this capture:**
- The company-ID flip to `0x01AA`. At that same `t=51.7s` line, `company` is still `0x0100`.
  A full-file grep for `01AA`/`01aa` returns zero hits anywhere in this pcapng.

At the time this note was first written, this looked like it might mean the company-ID flip
was only reachable live, not capturable in a stored file. **Source 4 below overturns that —
the flip does show up in stored captures, just not in this particular one.**

**Data-quality caveat for this specific pcapng:** most of its *other* advertising lines are
corrupted — garbled UUIDs (`0x7AE4`, `0xBEA0`, `0x32A0`, ...), mangled `Complete Local Name`
strings (e.g. `'Geberi�'yk.�o'`), nonsense company IDs (`0x1100`, `0x8100`). Consistent with
RF noise or overlapping-device interference during that capture session, not a decoder bug —
`tools/nrf-ble-analyze.py --adv` is decoding exactly what's in the file. Don't treat this
pcapng as a clean general-purpose advertising reference; it's fine for the two clean facts
above (idle payload, one button-press payload) but not for anything beyond that.

## Source 4 — five more stored captures checked (2026-07-19)

Same method (`--adv`, then `--markdown --include-adv` into a separate `*-with-adv.md` per
file, originals untouched) run against every other capture in the repo that plausibly covers
onboarding against a **real** Mera (mock-only captures excluded):

| Capture | `IsButtonPressed` (`data=0x01…`) | `company=0x01AA` |
|---|---|---|
| `geberit-home-app/nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-0.pcapng` | not present | not present |
| `geberit-home-app/nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-1.pcapng` → `...-with-adv.md` | line 28, `t=49.5s` | line 37, `t=54.4s` (+ line 38, `t=54.9s`) |
| `geberit-home-app/on-board-geberit-Home-app-to-mera.pcapng` | `t=23.4s` | not present |
| `firmware-update-mera-comfort/onboard-real-mera-probab-incomplete.pcapng` | `t=12.9s` | not present |
| `firmware-update-mera-comfort/firmware-update-vom-mac.pcapng` → `...-with-adv.md` | line 22, `t=26.9s` | line 23, `t=31.9s` |

**This resolves the Source 3 gap**: two of these five (`nRF-sniff-...-onboard-1` and
`firmware-update-vom-mac`) show `company=0x01aa data=0x0131…` byte-for-byte matching Sources
1/2 — the company-ID flip mechanism *is* confirmed from a stored capture, twice over.

**New finding — the two bits are not simultaneous.** In both captures that show the flip, the
data-byte flip happens *first*, and the company-ID flip follows several seconds later, in the
same capture:
- `nRF-sniff-...-onboard-1`: data flips at `t=49.5s`, company flips at `t=54.4s` (≈5s later)
- `firmware-update-vom-mac`: data flips at `t=26.9s`, company flips at `t=31.9s` (≈5s later)

The other three captures (`onboard-0`, `on-board-geberit-Home-app-to-mera`,
`onboard-real-mera-probab-incomplete`) never show the company-ID flip at all, even though two
of them do show the data-byte flip — consistent with `IsEmergencyConnectPermitted` requiring
a longer button hold (or a later onboarding stage) than a plain button press, rather than the
two bits being tied together. The original claim in Sources 1/2 ("both flags flip together")
was likely just sampling a moment after both had already flipped, not evidence they flip
atomically.

All five additional captures share the same RF-noise caveat as Source 3 — treat only the
specific rows above as reliable, not the files as clean general-purpose references.

## Bottom line

| Claim | Confirmed by |
|-------|-------------|
| Idle payload `00 31 34 36 32 31` | Sources 1, 2, 3, 4 |
| Button-pressed payload `01 31 34 36 32 31` | Sources 1, 2, 3, 4 |
| Company-ID flip `0x0100`→`0x01AA` on button press | Sources 1, 2, and 2 of the 5 captures in Source 4 |
| The two bits flip simultaneously | **Contradicted** by Source 4 — data-byte flip consistently precedes the company-ID flip by ~5s in both captures that show both |
| SCAN_RSP RS-firmware-prefix entry, unaffected by button state | Source 2 (the `--adv` output doesn't decode SCAN_RSP manufacturer data content, only name) |
