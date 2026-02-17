# Operating Modes

The application has three operating modes, selected with `--mode`:

```
python main.py --mode service   # default
python main.py --mode api
python main.py --mode cli --command <command>
```

---

## `service` (default)

A long-running background process that keeps a permanent BLE connection to the device and polls its state every `[POLL] interval` seconds.

- State changes are published to MQTT in real time.
- No HTTP server is started.
- Suitable for running as a systemd service on a Raspberry Pi.

```
python main.py
python main.py --mode service
```

## `api`

A long-running process that exposes a **REST API** and **web UI** over HTTP, plus optional MQTT publishing.  Supports two BLE connection strategies:

| `ble_connection` | Behaviour |
|-----------------|-----------|
| `persistent` | Permanent BLE connection with background polling — same as service mode, plus HTTP. |
| `on-demand` | BLE is connected, queried, and disconnected per REST request. A background polling loop still runs at the configured interval to keep MQTT and SSE updated. |

The connection mode can be switched at runtime via the web UI, the REST API, or an MQTT message — no restart required.

```
python main.py --mode api
```

Web UI available at `http://<host>:<port>/` (default: `http://0.0.0.0:8080/`).

See [rest-api.md](rest-api.md) and [webapp.md](webapp.md) for details.

### On-demand mode — why it matters

The original C# library and the initial Python port keep a **permanent BLE connection** to the AquaClean.  In practice, the device firmware stops responding after a few days of continuous use under a persistent connection — a known hardware/firmware limitation.

**On-demand mode** is a non-blocking alternative: BLE is connected only for the duration of a single request, then released.  This avoids holding a long-lived connection and keeps the device stable indefinitely.

```
Each REST or MQTT request:
  connect → query → disconnect   (~1–2 s round-trip)
```

A background polling loop (interval configured in `[POLL]`) still runs on the same on-demand pattern to keep MQTT topics and SSE state current between explicit requests.

**Trade-off summary:**

| | Persistent | On-demand |
|-|-----------|----------|
| Long-term stability | Degrades after a few days | Stable indefinitely |
| Request latency | Instant (always connected) | ~1–2 s (connect overhead) |
| Best for | Continuous high-frequency monitoring | REST integrations, occasional polling |

Switch modes without restart:

```bash
# via REST API
curl -X POST http://localhost:8080/config/ble-connection \
     -H "Content-Type: application/json" -d '{"value": "on-demand"}'

# via MQTT
mosquitto_pub -h YOUR_BROKER \
  -t "Geberit/AquaClean/centralDevice/config/bleConnection" -m "on-demand"
```

Or use the toggle button in the web UI.

## `cli`

A one-shot tool that connects to the device, runs a single command, prints JSON to stdout, and exits.  Log output goes to stderr.

```
python main.py --mode cli --command <command> [--address <ble-mac>]
```

Does not start an HTTP server and does not require MQTT. `get-config`, `publish-ha-discovery`, and `remove-ha-discovery` do not even need a BLE connection.

See [cli.md](cli.md) for all available commands.

---

## Choosing a mode

| Goal | Mode |
|------|------|
| Headless background service, MQTT only | `service` |
| Home automation with REST + MQTT + web UI | `api` |
| Scripting / one-off commands | `cli` |
| Check device state in a shell script | `cli` |
| Set up Home Assistant MQTT discovery | `cli --command publish-ha-discovery` |
