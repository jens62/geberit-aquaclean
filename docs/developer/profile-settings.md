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
| 2 | Orientation Light Activation | 0–2 | 0=On, 1=Off, 2=When Approached |
| 3 | Orientation Light Color | 0–6 | See color table below |
| 4 | WC Lid Sensor Sensitivity | 0–4 | Confirmed 2026-04-15 via BLE log |
| 6 | WC Lid Open Automatically | 0–1 | 0=off, 1=on; confirmed 2026-04-15 |
| 7 | WC Lid Close Automatically | 0–1 | 0=off, 1=on; confirmed 2026-04-15 |

IDs 4, 6, 7 confirmed from WC-Lid BLE log (2026-04-15). ID 5 unknown (candidate: Maximum Lid Position).

---

## Orientation Light Colors

Color name constants from `colors.xml` in the Android app source.
**BLE wire values confirmed for Blue and Magenta** (from BLE log: initial state was Magenta/when-approached;
after user changed to On/Blue the wire values became `id=3=1` for color and `id=2=0` for activation).

| BLE Wire Value | Color Name | Hex | Confirmation |
|---------------|-----------|-----|--------------|
| 0 | Warm White | `#fff1d8` | Unconfirmed — position 0 assumed |
| 1 | Blue | `#117aff` | **Confirmed via BLE log** |
| 2 | Magenta | `#eb4994` | **Confirmed via BLE log** |
| 3 | Orange | `#ffad3f` | Unconfirmed |
| 4 | Cold White | `#e4efff` | Unconfirmed |
| 5 | Turquoise | `#96f3f3` | Unconfirmed |
| 6 | Yellow | `#ffee7c` | Unconfirmed |

> **Note:** The Excel spreadsheet `Profile-Settings.xlsx` listed color indices 0–6 in a different order
> (its column B said 0=Warmwhite, 1=Yellow, 2=Orange, 3=Magenta, 4=Coldwhite, 5=Turquoise, 6=Blue).
> **These were the user's guesses and do not match the confirmed BLE wire values.**
> Only values 1 (Blue) and 2 (Magenta) are confirmed from the BLE log.
> The mapping for values 0, 3, 4, 5, 6 needs user testing to verify.

---

## What still needs BLE sniffing

| Unknown | Where to look |
|---------|--------------|
| WC lid sensor sensitivity setting ID | Sniff iPhone while adjusting lid sensor slider |
| WC lid auto-open/close/max-position IDs | Sniff iPhone on WC lid settings screen |
| Orientation light color values 0, 3, 4, 5, 6 | Select each color on iPhone; observe `SetStoredCommonSetting` `id=3` payload |
| Common setting IDs 4–9 | Inspect iPhone log: iPhone reads IDs up to 9 on connect |

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

| BLE Index (confirmed) | Color Name | Hex |
|----------------------|-----------|-----|
| 1 (**confirmed**) | Blue | `#117aff` |
| 2 (**confirmed**) | Magenta | `#eb4994` |
| 0 (unconfirmed) | Warm White | `#fff1d8` |
| 3 (unconfirmed) | Orange | `#ffad3f` |
| 4 (unconfirmed) | Cold White | `#e4efff` |
| 5 (unconfirmed) | Turquoise | `#96f3f3` |
| 6 (unconfirmed) | Yellow | `#ffee7c` |

> Second turquoise variant in `colors.xml`: `NonGeberitTurquoise2 = #4ba5a0` — not exposed in app UI,
> not mapped to any BLE value.
