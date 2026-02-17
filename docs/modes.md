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
