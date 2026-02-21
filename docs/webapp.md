# Web UI

The web UI is served at `http://<host>:<port>/` when running in **api mode**.

```bash
python main.py --mode api
```

An interactive Swagger UI (auto-generated from FastAPI) is available at `http://<host>:<port>/docs`.

---

## Layout

### Status tiles

Four live tiles show the current device state:

| Tile | Field | Active when |
|------|-------|-------------|
| User Sitting | `is_user_sitting` | Someone is seated |
| Anal Shower | `is_anal_shower_running` | Anal shower is running |
| Lady Shower | `is_lady_shower_running` | Lady shower is running |
| Dryer | `is_dryer_running` | Dryer is running |

Tiles turn blue and show **ON** when active, grey and **OFF** when inactive.  The User Sitting tile switches between a person-on-toilet icon and an empty-toilet icon.

Below the tiles a **poll countdown bar** shows time until the next background poll, derived from `poll_epoch` and `poll_interval` in the SSE stream.

### Device Info

A grid shows static device information fetched on connection or page load:

- Description (model name)
- Serial number
- SAP number
- Production date
- Initial operation date

### Commands

| Button | Action |
|--------|--------|
| Toggle Lid | `POST /command/toggle-lid` |

### Connection panel

Shows current BLE status (`connected`, `connecting`, `error`, or `disconnected`), the device name and MAC address, how long ago it connected, the active BLE connection mode, and the poll interval.

Timing fields are shown here when available:

| Field | Label | Description |
|-------|-------|-------------|
| `last_connect_ms` | **Connect:** | Total time for the last connect (ESP32 TCP + BLE) |
| `last_esphome_api_ms` | **ESP32:** | Portion spent on ESP32 TCP connect; `0` = reused; hidden for local BLE |
| `last_ble_ms` | **BLE:** | Portion spent on BLE scan + handshake; hidden for local BLE |
| `last_poll_ms` | **Poll:** | Last background poll round-trip |

When an error occurs, the error code, message, and resolution hint are shown in the BLE status widget.

### ESP32 proxy panel (when ESPHome is configured)

Shows ESP32 connection status, host/port, and the active API connection mode.

| Control | Effect |
|---------|--------|
| `ESP32: Connect` / `ESP32: Disconnect` | Connect or disconnect the ESP32 API TCP connection |
| `ESP32: Switch to On-Demand` / `ESP32: Switch to Persistent` | Toggles ESP32 API connection mode |

### Actions

All connection buttons use a consistent `PREFIX: Action` label format.

| Control | Effect |
|---------|--------|
| `BLE: Reconnect` / `BLE: Disconnect` | `POST /connect` or `POST /disconnect` depending on current state |
| `BLE: Switch to On-Demand` / `BLE: Switch to Persistent` | Toggles BLE connection mode via `POST /config/ble-connection` |
| Poll interval input + Set Interval button | Updates poll interval via `POST /config/poll-interval` |

### Queries section (on-demand mode only)

Visible only when the BLE connection mode is **on-demand**.  Each button triggers a live query to the device and shows the result immediately, without waiting for the next background poll.

| Button | Endpoint |
|--------|----------|
| System Parameters | `GET /data/system-parameters` |
| Identification | `GET /data/identification` |
| Initial Op Date | `GET /data/initial-operation-date` |
| SOC Versions | `GET /data/soc-versions` |
| Anal Shower State | `GET /data/anal-shower-state` |
| User Sitting State | `GET /data/user-sitting-state` |
| Lady Shower State | `GET /data/lady-shower-state` |
| Dryer State | `GET /data/dryer-state` |

After each query, timing fields show the breakdown:
- **ESP32:** — TCP connect time (`0 ms` if reused, hidden for local BLE)
- **BLE:** — BLE scan + handshake time (hidden for local BLE)
- **Query:** — time for the data request itself; shown as `— (cached)` when data was served from the in-memory cache without a BLE connect

---

## Live updates (SSE)

The page connects to `/events` on load and keeps a persistent Server-Sent Events connection.  All tiles and the connection panel update in real time without page refresh.

The green/red dot in the top-right corner of the header indicates whether the SSE stream is live.  If the server goes offline, a red banner appears and the UI resets to `—` values until reconnected.

---

## Responsive design

On screens narrower than 480 px the four status tiles stack into a single column.  The page works on mobile browsers.
