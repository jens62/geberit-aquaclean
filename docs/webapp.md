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

Timing fields — **Connect** (last BLE connect duration) and **Poll** (last poll round-trip) — are shown here when available.

### Actions

| Control | Effect |
|---------|--------|
| Reconnect / Disconnect button | `POST /connect` or `POST /disconnect` depending on current state |
| Switch to On-Demand / Switch to Persistent button | Toggles BLE connection mode via `POST /config/ble-connection` |
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

After each query, **Connect** and **Query** timing fields show the BLE round-trip durations.

---

## Live updates (SSE)

The page connects to `/events` on load and keeps a persistent Server-Sent Events connection.  All tiles and the connection panel update in real time without page refresh.

The green/red dot in the top-right corner of the header indicates whether the SSE stream is live.  If the server goes offline, a red banner appears and the UI resets to `—` values until reconnected.

---

## Responsive design

On screens narrower than 480 px the four status tiles stack into a single column.  The page works on mobile browsers.
