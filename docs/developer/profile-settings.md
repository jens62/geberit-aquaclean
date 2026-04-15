# Profile Settings & Common Settings Reference

BLE procedure codes, setting IDs, value ranges, and confirmed color mappings.
Derived from `Profile-Settings.xlsx` (user-provided), BLE traffic log analysis,
and the Android app color resources (`local-assets/geberit-home/app/src/main/res/values/colors.xml`).

---

## Profile Settings — proc 0x53 (read) / 0x54 (write)

Per-user preference storage. Payload format:
- Read (0x53): `[profile_id, setting_id]` — 2 bytes
- Write (0x54): `[profile_id, setting_id, value_lo, value_hi]` — 4 bytes

`profile_id = 0` for the default (and only known) profile.

### Shower

| Setting ID | Name | Range | Notes |
|-----------|------|-------|-------|
| 6 | Water Temperature | 0–5 | Shared with Lady shower |
| 4 | Anal Shower Position | 0–4 | Spray arm position |
| 2 | Anal Shower Pressure | 0–4 | Shower spray intensity |
| 1 | Oscillator State | 0–1 | Oscillating spray (boolean) |

### Lady Shower

| Setting ID | Name | Range | Notes |
|-----------|------|-------|-------|
| 6 | Water Temperature | 0–5 | Likely same storage as shower temp |
| 5 | Lady Shower Position | 0–4 | Confirmed via BLE sniff: value 1 = one step from left |
| 3 | Lady Shower Pressure | 0–4 | Shower spray intensity |

### Dryer

| Setting ID | Name | Range | Notes |
|-----------|------|-------|-------|
| 9 | Dryer State | 0–1 | On/off (boolean) |
| 8 | Dryer Temperature | 0–5 | Air temperature |
| 13 | Dryer Spray Intensity | 0–4 | Confirmed via BLE log 2026-04-15 (`Dryer-spray-intensity-from-1-to-3.txt`) |

### Odour Extraction (profile-level)

| Setting ID | Name | Range | Notes |
|-----------|------|-------|-------|
| 0 | Odour Extraction | 0–1 | On/off (boolean) |

The **run-on time** for odour extraction is a **common setting** (device-wide), not a profile setting — see below.

### WC Lid (not yet mapped)

| Setting | Range | Status |
|---------|-------|--------|
| Sensor Sensitivity | 0–4 | **Setting ID unknown** |
| Automatic Lid Opening | boolean | **Setting ID unknown** |
| Automatic Lid Closing | boolean | **Setting ID unknown** |
| Maximum Lid Position | float/int | **Setting ID unknown** |

### Seat Heating

| Setting ID | Name | Range |
|-----------|------|-------|
| 7 | WC Seat Heat | 0–5 |

### iPhone read order (proc 0x0A init sequence)

`[2, 1, 3, 4, 6, 7, 5, 8, 0, 9]`

This is the init-handshake sequence, **not** the proc 0x53 read order for data purposes.
See `memory/ble-procedure-investigation-method.md` for the 0x0A vs 0x53 distinction.

---

## Common Settings — proc 0x51 (read) / 0x52 (write)

Device-wide (not per-user) storage. Payload format:
- Read (0x51): `[setting_id]` — 1 byte; response is 2-byte little-endian int
- Write (0x52): `[setting_id, value_lo, value_hi]` — 3 bytes

Confirmed from BLE traffic log:
`Geräteeinstellungen-Orientierungslicht-von-Magenta-bei-Annäherung-zu-Ein-und-Blau-und-wieder-zurück.txt`

iPhone reads IDs in order: `[2, 1, 3, 0]` on every connect (from the log).

### Confirmed IDs

| Setting ID | Name | Range | Notes |
|-----------|------|-------|-------|
| 0 | Odour Extraction Run-On | 0–1 | Run-on time after use (boolean) |
| 1 | Orientation Light Brightness | 0–4 | — |
| 2 | **Orientation Light Color** | 0–6 | **id=2=COLOR** — see color table below; confirmed 2026-04-15 |
| 3 | **Orientation Light Activation** | 0–2 | **id=3=ACTIVATION** — 0=Off, 1=On, 2=When Approached; confirmed 2026-04-15 |
| 4 | WC Lid Sensor Sensitivity | 0–4 | Confirmed 2026-04-15 via BLE log |
| 6 | WC Lid Open Automatically | 0–1 | 0=off, 1=on; confirmed 2026-04-15 |
| 7 | WC Lid Close Automatically | 0–1 | 0=off, 1=on; confirmed 2026-04-15 |

IDs 2, 3 fully confirmed 2026-04-15 from all-colors BLE log. Previous analysis had id=2 and id=3 swapped — now corrected.
IDs 4, 6, 7 confirmed from WC-Lid BLE log (2026-04-15). ID 5 unknown (candidate: Maximum Lid Position).

---

## Orientation Light Colors (common setting id=2)

All 7 values **fully confirmed** 2026-04-15 from BLE log
`Orientierungslicht-von-bei-Aktivierung-auf-EIn-Aus-Bei-Annäherung-dann-alle-Farben-von-links-nach-rechts.txt`.

| BLE Wire Value | Color Name | Hex | Confirmation |
|---------------|-----------|-----|--------------|
| 0 | Blue | `#117aff` | **Confirmed 2026-04-15** |
| 1 | Turquoise | `#96f3f3` | **Confirmed 2026-04-15** |
| 2 | Magenta | `#eb4994` | **Confirmed 2026-04-15** |
| 3 | Orange | `#ffad3f` | **Confirmed 2026-04-15** |
| 4 | Yellow | `#ffee7c` | **Confirmed 2026-04-15** |
| 5 | Warm White | `#fff1d8` | **Confirmed 2026-04-15** |
| 6 | Cold White | `#e4efff` | **Confirmed 2026-04-15** |

> **Note:** Previous table (before 2026-04-15) had id=2 and id=3 swapped, and only Blue/Magenta confirmed.
> The full mapping is now confirmed. The Excel spreadsheet ordering was wrong.

---

## What still needs BLE sniffing

| Unknown | Where to look |
|---------|--------------|
| Common setting ID 5 | Candidate: Maximum Lid Position — sniff iPhone while adjusting |
| Common setting IDs 8–9 | Purpose unknown — sniff iPhone on connect |

---

## Profile-Settings.xlsx — Full Table

Original Excel data as-is. Source: user-provided spreadsheet.

| Category | Setting (English) | Range / Value | Meaning | German (Bedeutung) |
|----------|-------------------|--------------|---------|-------------------|
| Shower | Water temperature | 0–5 | | Wassertemperatur |
| | Spray arm position | 0–4 | | Duscharmposition |
| | Shower spray intensity | 0–4 | | Duschstrahlstärke |
| | Oscillating spray | boolean | | Oszillation |
| Lady shower | Water temperature | 0–5 | | Wassertemperatur |
| | Spray arm position | 0–4 | | Duscharmposition |
| | Shower spray intensity | 0–4 | | Duschstrahlstärke |
| Dryer | Dryer | boolean | | Föhn |
| | Air temperature | 0–5 | | Lufttemperatur |
| | Dryer spray intensity | 0–4 | | Föhnstrahlstärke |
| Orientation light | Activation | 0, 1, 2 | On, Off, when approached | Aktivierung (Ein, Aus, bei Annäherung) |
| | Brightness | 0–4 | | Helligkeit |
| | Colour | 0–6 | See color table above | Farbe |
| Odour extraction | Odour extraction | boolean | | Geruchsabsaugung |
| | Extraction run-on time | boolean | | Geruchsabsaugung Nachlauf |
| WC lid | Sensor sensitivity | 0–4 | | Sensorempfindlichkeit |
| | Automatic lid opening | boolean | | Automatisch WC Deckel öffnen |
| | Automatic lid closing | boolean | | Automatisch WC Deckel schließen |
| | Maximum lid position | float/int | | Maximaldeckelposition |
| Seat heating | Temperature | 0–5 | | Temperatur |

### Color resource values (from `colors.xml`)

| BLE Index | Color Name | Hex | Status |
|-----------|-----------|-----|--------|
| 0 | Blue | `#117aff` | **Confirmed 2026-04-15** |
| 1 | Turquoise | `#96f3f3` | **Confirmed 2026-04-15** |
| 2 | Magenta | `#eb4994` | **Confirmed 2026-04-15** |
| 3 | Orange | `#ffad3f` | **Confirmed 2026-04-15** |
| 4 | Yellow | `#ffee7c` | **Confirmed 2026-04-15** |
| 5 | Warm White | `#fff1d8` | **Confirmed 2026-04-15** |
| 6 | Cold White | `#e4efff` | **Confirmed 2026-04-15** |

> Second turquoise variant in `colors.xml`: `NonGeberitTurquoise2 = #4ba5a0` — not exposed in app UI,
> not mapped to any BLE value.
