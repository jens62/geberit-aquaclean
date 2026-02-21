# On-Demand BLE Connection

## Upgrading from a previous version

If you are running an older version of this software, add the following sections to your `config.ini` — they did not exist before:

```ini
[SERVICE]
; mqtt_enabled: publish status to MQTT broker (true/false)
mqtt_enabled = true
; ble_connection: persistent = keep BLE connected with polling loop
;                 on-demand  = connect/disconnect per REST request (api mode only)
ble_connection = on-demand

[API]
host = 0.0.0.0
port = 8080
```

Then start in API mode:

```bash
python main.py --mode api
```

Open `http://<your-host>:8080/` in a browser to verify everything works — the web UI is the quickest way to confirm the BLE connection comes up, queries return data, and the on-demand timing fields (`_connect_ms`, `_query_ms`) appear below the query buttons.

---

## Background — why this was needed

A long-standing problem with the original C# library and early Python port is that they hold a **permanent BLE connection** to the AquaClean.  The device firmware does not cope well with a persistent connection over several days: after 2–5 days of continuous use the device stops responding and must be power-cycled.

This was one of the most-requested improvements from the community.  The `feature/rest-api` branch introduces **on-demand BLE** as a first-class connection strategy that eliminates the problem entirely.

---

## How on-demand mode works

In persistent mode the bridge connects once at startup and keeps the channel open:

```
startup → connect → poll → poll → poll → … (connection held permanently)
```

In on-demand mode every interaction follows the same short lifecycle:

```
request arrives → connect → query → disconnect → response returned
```

The BLE connection is established **immediately before** the query and **released immediately after**.  No long-lived connection is held between requests.  The device is only occupied for the ~1–2 seconds it takes to connect and query, then it is free.

A background polling loop (configured via `[POLL] interval`) also runs on the same on-demand pattern to keep MQTT topics and the SSE stream updated between explicit requests.

---

## Configuration

Set `ble_connection` in `config.ini`:

```ini
[SERVICE]
ble_connection = on-demand   # or: persistent
```

```ini
[POLL]
interval = 30   # seconds between background polls; 0 to disable
```

This takes effect on the next start.  To switch without a restart see below.

---

## Switching at runtime — no restart required

**Web UI** — click the *Switch to On-Demand* / *Switch to Persistent* toggle button in the Connection panel.

**REST API:**
```bash
curl -X POST http://localhost:8080/config/ble-connection \
     -H "Content-Type: application/json" \
     -d '{"value": "on-demand"}'
```

**MQTT:**
```bash
mosquitto_pub -h YOUR_BROKER \
  -t "Geberit/AquaClean/centralDevice/config/bleConnection" \
  -m "on-demand"
```

---

## Timing information

Every REST API response that required a BLE round-trip includes timing fields:

| Field | Description |
|-------|-------------|
| `_connect_ms` | Total time in ms to establish all connections (ESP32 TCP + BLE scan + handshake) |
| `_esphome_api_ms` | Portion spent connecting to the ESP32 API (TCP); `null` if using local BLE; `0` if reused |
| `_ble_ms` | Portion spent on BLE scan + GATT handshake; `null` if using local BLE directly |
| `_query_ms` | Time in ms for the query itself after connecting; `0` means data was served from cache |

Example — toggle lid (ESP32 proxy, fresh TCP connection):
```json
{"status":"success","command":"toggle-lid","_connect_ms":1050,"_esphome_api_ms":980,"_ble_ms":70,"_query_ms":312}
```

Example — same request with persistent ESP32 API connection (TCP reused):
```json
{"status":"success","command":"toggle-lid","_connect_ms":75,"_esphome_api_ms":0,"_ble_ms":75,"_query_ms":318}
```

Example — cached endpoint (identification already fetched):
```json
{"sap_number":"966.848.00.0","_connect_ms":0,"_esphome_api_ms":0,"_ble_ms":0,"_query_ms":0}
```

**Cached endpoints:** identification, SOC versions, and initial operation date are fetched once on the first background poll and cached in memory. Subsequent REST calls to `/data/identification`, `/data/soc-versions`, and `/data/initial-operation-date` return the cached data without a BLE connect — all timing fields are `0` and the web UI shows `— (cached)` for the Query field.

The web UI displays these timings below the Queries buttons in on-demand mode.

---

## Circuit breaker (on-demand polling)

The background polling loop has a built-in circuit breaker to handle unresponsive devices gracefully.

After **5 consecutive poll failures** the circuit opens:
- The log shows `Circuit open after 5 failures — probing every 60s`
- The poll interval switches to **60-second probe attempts** instead of the normal interval
- The BLE error status is shown in the web UI

On the **first successful probe** the circuit closes:
- The log shows `Poll recovered after N failures`
- Normal polling resumes at the configured interval
- Identification data is re-fetched (in case the device was power-cycled during the outage)

This prevents the app from hammering an unresponsive device at full poll frequency. The threshold and probe interval are constants at the top of `_polling_loop` in `main.py`.

---

## Trade-off summary

| | Persistent | On-demand |
|-|-----------|----------|
| Long-term stability | Degrades after a few days | Stable indefinitely |
| Request latency | Instant | ~1–2 s (connect overhead) |
| Best for | Continuous high-frequency monitoring | REST API, scripting, occasional polling |

---

See also [ble-coexistence.md](ble-coexistence.md) for how the bridge behaves alongside the Geberit Home app and what to do after a stale connection.
