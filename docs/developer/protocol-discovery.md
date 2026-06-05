# Protocol Discovery — Finding What the App Doesn't Use

The Geberit Home app source tells us what **the app sends**.
It does not tell us what the **toilet firmware accepts beyond that**.
This document covers how to discover undocumented or unused procedure codes,
commands, and parameters.

---

## The problem

Every confirmed protocol item in the bridge was found via one of:
- App source analysis (what the app calls)
- BLE captures of the app (what the app actually sends during a session)
- thomas-bingel C# reference repo

All three sources share the same blind spot: **they only reveal procedures the
phone app invokes**. The toilet firmware may accept additional procedure codes,
SetCommand codes, or parameter indices that the app never calls — factory
diagnostics, service-mode commands, calibration routines, or features the app
simply hides.

---

## Discovery approach 1 — Systematic read-proc sweep (safe)

`tools/geberit-ble-probe.py` can call any procedure code with arbitrary args.
Read-only procedures (those that return data without changing device state) are
safe to probe blindly.

```bash
# Probe an unknown read procedure — example: proc 0x06 (GetActualOutletTemperature)
/Users/jens/venv/bin/python tools/geberit-ble-probe.py --proc 0x06

# Sweep a range — wrap in a shell loop or extend geberit-ble-probe.py
for code in 0x01 0x02 0x03 0x04 0x05 0x06; do
    /Users/jens/venv/bin/python tools/geberit-ble-probe.py --proc $code
done
```

**Safe**: procedures that return data but do not change device state.
**Dangerous**: write procedures, SetCommand codes, SetActiveProfileSetting.
Skip dangerous codes unless `--unsafe` is intentional and the device is in a
recoverable state (see `geberit-ble-fuzz.py` below for guarded automation).

Known safe read procs already confirmed: `0x05`, `0x07`, `0x0D`, `0x0E`, `0x51`,
`0x53`, `0x55`, `0x59`, `0x81`, `0x82`, `0x86`.

---

## Discovery approach 2 — Agentic BLE protocol fuzzer (planned)

`tools/geberit-ble-fuzz.py` — not yet implemented; tracked in `roadmap-todo.md`.

Planned modes:
- `--mode read-procs` — sweep all procedure codes 0x01–0xFF, skip known-dangerous
- `--mode setcommand` — probe SetCommand codes not yet confirmed
- `--mode common-settings` — sweep all CommonSetting IDs via proc 0x51
- `--mode profile-settings` — sweep all ProfileSetting IDs via proc 0x53

Safe defaults skip destructive SetCommand codes (33–36, 4, 37, 6–9) unless
`--unsafe` is passed.

---

## Discovery approach 3 — nRF52840 passive sniffing of the remote control

This is the **highest-value approach** for finding procedures the app never calls.

### Why the remote control, not the app

The physical Geberit remote control connects to the toilet over BLE independently
of the app. It is a separate BLE central that may use procedure codes or command
codes the phone app never sends — e.g. direct hardware-button shortcuts, service
sequences, or factory-programmed calibration routines.

Remote control BLE address: `b0:10:a0:68:5c:8b` (public, Texas Instruments OUI).

### Why the nRF52840

The nRF52840 dongle is a **passive OTA sniffer** — it captures raw BLE frames from
the air without participating in the connection. It sees ALL traffic between ANY
two devices, including the remote↔toilet link that has no phone involved.

From `ble-traffic-capture.md`:
> "The nRF52840 method is the only one that can capture traffic from devices that
> are not a phone (physical remote, bridge on a Raspberry Pi)."

An iOS btsnoop or Android HCI log would miss this entirely — those logs only
capture the phone's own BLE traffic.

### Procedure

1. Start Wireshark with the nRF52840 sniffer plugin — see `ble-traffic-capture.md`
   for setup. Use REQ_FOLLOW in Wireshark.
   **Do NOT use direct serial / Python REQ_FOLLOW** — this is a confirmed dead end
   with nrfutil v4.x firmware; `tools/archive/sniff.py` is archived for this reason.
2. Let the remote control connect to the toilet and operate normally
   (press buttons on the remote).
3. Save the `.pcapng` capture.
4. Analyse with `tools/find-geberit-remote.py capture.pcapng` to extract
   the remote's ATT Write frames.
5. Compare procedure codes and payloads against the known procedure table in
   `ble-protocol.md` — any unrecognised code is a candidate for a
   remote-only feature.

### Limitation

Passive capture only reveals what the remote **actually transmits** during the
session. Factory/service-mode procedures that require a special hardware trigger
(e.g. holding a button combination) will only appear if that trigger is activated
during the capture.

---

## Summary of approaches

| Approach | Discovers | Risk | Status |
|----------|-----------|------|--------|
| App source analysis | what the app calls | none | done |
| BLE capture of app session | what app sends at runtime | none (passive) | done |
| Read-proc sweep (`geberit-ble-probe.py`) | unknown read procs | low (reads only) | available now |
| Agentic fuzzer (`geberit-ble-fuzz.py`) | unknown read+write procs | medium (guarded) | planned |
| nRF52840 sniff of remote control | remote-only procedures | none (passive) | available now |

---

## Related docs

- `ble-traffic-capture.md` — nRF52840 setup and Wireshark procedure
- `protocol-gap-analysis.md` — app↔bridge gap (what the app uses that the bridge doesn't)
- `unknown-procedures.md` — procedures seen in captures but not yet understood
- `ble-protocol.md` — confirmed procedure table and known Commands/SPL indices
