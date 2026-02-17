# On-Demand BLE Connection

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

Every REST API response that required a BLE round-trip includes two extra fields:

| Field | Description |
|-------|-------------|
| `_connect_ms` | Time in ms to establish the BLE connection |
| `_query_ms` | Time in ms for the query itself after connecting |

Example — toggle lid:
```json
{"status":"success","command":"toggle-lid","_connect_ms":4388,"_query_ms":1316}
```

Example — user sitting state:
```json
{"is_user_sitting":false,"_connect_ms":4311,"_query_ms":306}
```

**Note:** `_connect_ms` includes the full connect sequence — the client fetches identification, SOC versions, and initial operation date during connect.  As a result, `_query_ms` for those specific endpoints (`/data/identification`, `/data/soc-versions`, `/data/initial-operation-date`) will be ~0 ms because the data is already cached by the time the query runs.

The web UI displays these timings below the Queries buttons in on-demand mode.

---

## Trade-off summary

| | Persistent | On-demand |
|-|-----------|----------|
| Long-term stability | Degrades after a few days | Stable indefinitely |
| Request latency | Instant | ~1–2 s (connect overhead) |
| Best for | Continuous high-frequency monitoring | REST API, scripting, occasional polling |
