# REST API Reference

Start the server with:

```bash
python main.py --mode api
```

Base URL: `http://<host>:<port>` (default: `http://0.0.0.0:8080`)

All endpoints return JSON.  In **on-demand** mode, timing fields `_connect_ms` and `_query_ms` are appended to every response that required a BLE round-trip.

An interactive Swagger UI is available at `http://<host>:<port>/docs`.

---

## General

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serve web UI |
| `GET` | `/events` | SSE stream of state updates |
| `GET` | `/status` | Current device state (4 monitor flags + BLE metadata) |
| `GET` | `/info` | Device identification + initial operation date |
| `GET` | `/config` | Current runtime config (`ble_connection`, `poll_interval`) |
| `POST` | `/config/ble-connection` | Switch connection mode. Body: `{"value": "persistent"}` or `{"value": "on-demand"}` |
| `POST` | `/config/poll-interval` | Set poll interval at runtime (does not write `config.ini`). Body: `{"value": 10.5}`. `0` disables background polling. |
| `POST` | `/connect` | Request BLE connect (persistent: reconnect; on-demand: connect + fetch info) |
| `POST` | `/disconnect` | Request BLE disconnect (persistent only) |

## Commands

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/command/toggle-lid` | Toggle lid open/closed |
| `POST` | `/command/toggle-anal` | Toggle anal shower on/off |

## Data queries

Each endpoint queries only the relevant parameter from the device.

| Method | Path | Response field(s) |
|--------|------|-------------------|
| `GET` | `/data/system-parameters` | `is_user_sitting`, `is_anal_shower_running`, `is_lady_shower_running`, `is_dryer_running` |
| `GET` | `/data/user-sitting-state` | `is_user_sitting` |
| `GET` | `/data/anal-shower-state` | `is_anal_shower_running` |
| `GET` | `/data/lady-shower-state` | `is_lady_shower_running` |
| `GET` | `/data/dryer-state` | `is_dryer_running` |
| `GET` | `/data/identification` | `sap_number`, `serial_number`, `production_date`, `description` |
| `GET` | `/data/initial-operation-date` | `initial_operation_date` |
| `GET` | `/data/soc-versions` | `soc_versions` |

---

## Examples

### Query user sitting state

```bash
curl http://localhost:8080/data/user-sitting-state
```
```json
{
  "is_user_sitting": false,
  "_connect_ms": 843,
  "_query_ms": 198
}
```

### Query all system parameters

```bash
curl http://localhost:8080/data/system-parameters
```
```json
{
  "is_user_sitting": false,
  "is_anal_shower_running": false,
  "is_lady_shower_running": false,
  "is_dryer_running": false,
  "_connect_ms": 812,
  "_query_ms": 198
}
```

### Toggle lid

```bash
curl -X POST http://localhost:8080/command/toggle-lid
```
```json
{
  "status": "success",
  "command": "toggle-lid",
  "_connect_ms": 831,
  "_query_ms": 54
}
```

### Switch to on-demand mode

```bash
curl -X POST http://localhost:8080/config/ble-connection \
     -H "Content-Type: application/json" \
     -d '{"value": "on-demand"}'
```

### Set poll interval to 30 seconds

```bash
curl -X POST http://localhost:8080/config/poll-interval \
     -H "Content-Type: application/json" \
     -d '{"value": 30}'
```

### Disable background polling

```bash
curl -X POST http://localhost:8080/config/poll-interval \
     -H "Content-Type: application/json" \
     -d '{"value": 0}'
```

---

## Server-Sent Events (SSE)

Connect to `/events` to receive a real-time push stream of state changes:

```bash
curl -N http://localhost:8080/events
```

Each event is a JSON object with a `type` field:

```
data: {"type": "state", "ble_status": "connected", "is_user_sitting": false, "is_anal_shower_running": false, "is_lady_shower_running": false, "is_dryer_running": false}

data: {"type": "state", "ble_status": "disconnected"}
```

A heartbeat comment (`: heartbeat`) is sent every 30 seconds to keep the connection alive through proxies.

The web UI subscribes to this stream to update tiles and the connection panel without polling.
