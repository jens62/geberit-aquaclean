# Performance Notes

This document explains how to read and interpret the performance statistics
reported by the HACS integration and the standalone bridge, and why the two
sets of numbers are not directly comparable.

---

## What each metric actually measures

### HACS integration (Lovelace Performance Statistics card)

| Metric | What is timed |
|--------|--------------|
| **Connect** | ESPHome TCP connect + WiFi RSSI read + BLE advertisement scan + BLE GATT connect |
| **Poll (GATT)** | `GetSystemParameterList` + `GetDeviceIdentification` + `GetDeviceInitialOperationDate` + `GetStatisticsDescale` + `GetSOCApplicationVersions` |

All five GATT procedures are included in a single "Poll (GATT)" measurement because
the HACS coordinator fetches all data in one BLE session per poll cycle.

### Standalone bridge (webapp / MQTT `centralDevice/performanceStats`)

| Metric | What is timed |
|--------|--------------|
| **ESP32 connect** | ESPHome TCP handshake only |
| **BLE connect** | BLE advertisement scan + GATT connect |
| **Poll (query)** | `GetSystemParameterList` only |

The standalone bridge times each phase separately and only times the state
query — not identification, descale statistics, or SOC versions.

---

## Why HACS numbers are larger than standalone numbers

This is expected, not a regression.

**Poll time:** HACS "Poll (GATT)" includes five GATT procedures; the standalone
"Poll (query)" is one. A standalone poll at ~365 ms and a HACS poll at ~2000 ms
are consistent — the extra ~1600 ms is the four additional procedures
(identification, initial operation date, descale statistics, SOC versions).

**Connect time:** HACS adds a WiFi RSSI read to the connect phase (introduced
in v2.4.33). `_read_esphome_wifi_rssi_async()` opens a `subscribe_states()`
subscription and waits for the ESP32 to push the current WiFi signal value.
This adds roughly 100–500 ms on top of the TCP + BLE connect time that the
standalone bridge measures.

---

## BLE RSSI and its effect on connect time

BLE RSSI is the strongest predictor of connect time variance.

| RSSI range | Effect |
|-----------|--------|
| −60 dBm and above | Clean connection, typically < 500 ms |
| −70 to −80 dBm | Occasional link-layer retries, 500–1500 ms |
| −80 to −90 dBm | Frequent retries, 1500–5000 ms |
| Below −90 dBm | Connection may fail entirely (E0003) |

A high **Max Connect** value (e.g. 4877 ms) with a low **Min BLE RSSI**
(e.g. −89 dBm) in the same session means the device briefly moved to the edge
of BLE range — not a software problem.

The ESP32 proxy helps here: it is typically mounted closer to the toilet than
the HA server, so its BLE RSSI is better than a direct local adapter scan would
be. The **WiFi RSSI** of the proxy itself (ESP32 ↔ router) has a smaller effect:
weak WiFi adds TCP retransmit latency to `last_esphome_api_ms` but usually only
by tens of milliseconds.

---

## Sample count and statistical validity

A session with 85 samples (a few hours) captures a narrower range of conditions
than one with 1300+ samples (several days). Short sessions tend to show lower
min/avg values simply because they miss the occasional slow poll caused by:

- Toilet moving to a weak BLE position briefly
- WiFi channel congestion spikes
- ESP32 garbage-collection pauses
- HA server load (if running on a shared Raspberry Pi)

When comparing two measurement periods, check that both have similar sample
counts before drawing conclusions about regressions.

---

## Persistent vs on-demand mode (standalone bridge only)

The standalone bridge's `performanceStats` output separates persistent-mode
samples from on-demand samples. In persistent mode the BLE connection stays
open between polls, so `last_ble_ms` and `last_esphome_api_ms` are zero after
the first connect — only `last_poll_ms` varies. On-demand mode reconnects every
poll, so all three components vary independently.

The HACS integration always operates in on-demand mode (a fresh BLE session per
coordinator update cycle). There is no persistent mode in HACS.

---

## Quick reference: apples-to-apples comparison

To compare HACS and standalone numbers fairly, sum the standalone components:

```
Standalone total connect ≈ ESP32 connect + BLE connect
Standalone total poll    ≈ Poll (query)   [identification not separately tracked]

HACS total connect       = Connect        [includes WiFi RSSI read — subtract ~200 ms]
HACS total poll          = Poll (GATT)    [5 procedures — subtract ~1500 ms for non-state calls]
```

Or simply: expect HACS "Connect" to be ~200–500 ms higher and HACS "Poll (GATT)"
to be ~1000–2000 ms higher than the equivalent standalone values, purely due to
the additional work performed per cycle.
