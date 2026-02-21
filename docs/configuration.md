# Configuration

All settings live in `aquaclean_console_app/config.ini`.  The file is always resolved relative to `main.py`, so you can invoke the script from any working directory.

```ini
[MQTT]
client   = aquaclean_python
server   = 192.168.0.xxx     # IP address of your MQTT broker
port     = 1883
topic    = Geberit/AquaClean  # root prefix for every published topic
; username = monty
; password = python

[BLE]
device_id = 38:AB:XX:XX:ZZ:67   # Bluetooth MAC address of the AquaClean

[POLL]
interval = 10.5                  # seconds between state polls (see below)

[SERVICE]
mqtt_enabled  = true             # publish to MQTT broker (true/false)
ble_connection = persistent      # persistent | on-demand

[API]
host = 0.0.0.0
port = 8080

[ESPHOME]
; host = 192.168.0.xxx            # IP address of ESP32 Bluetooth Proxy (optional)
; port = 6053                     # ESPHome native API port
; noise_psk =                     # base64 encryption key (matches api_encryption_key in secrets.yaml)
; log_streaming = false           # Stream ESP32 logs to console app (for debugging)
; log_level = INFO                # ESP32 log level: ERROR | WARN | INFO | DEBUG | VERBOSE

[LOGGING]
log_level = DEBUG                # DEBUG | INFO | WARNING | TRACE
```

---

## Section reference

### `[BLE]`

| Key | Description |
|-----|-------------|
| `device_id` | Bluetooth MAC address of the toilet. Find it with `bluetoothctl scan on` — look for `Geberit AC PRO`. |

### `[MQTT]`

| Key | Default | Description |
|-----|---------|-------------|
| `client` | `aquaclean_python` | MQTT client ID (a timestamp is appended at runtime to avoid collisions). |
| `server` | — | IP address or hostname of the MQTT broker. **Required.** |
| `port` | `1883` | MQTT broker port. |
| `topic` | `Geberit/AquaClean` | Root topic prefix. All published and subscribed topics are prefixed with this value. |
| `username` | *(commented out)* | Optional MQTT username. |
| `password` | *(commented out)* | Optional MQTT password. |

### `[POLL]`

| Key | Default | Description |
|-----|---------|-------------|
| `interval` | `10.5` | Seconds between `GetSystemParameterList` polls. Applies to **service mode** (persistent BLE loop) and to **api mode on-demand** (background polling). Set to `0` to disable background polling in api/on-demand mode. Can be changed at runtime via `POST /config/poll-interval` or the MQTT topic `centralDevice/config/pollInterval` — without editing this file. |

A longer interval reduces BLE request frequency, which can help avoid the device becoming unresponsive after several days of continuous use.

### `[SERVICE]`

| Key | Default | Description |
|-----|---------|-------------|
| `mqtt_enabled` | `true` | When `true`, every REST API result and every state change is published to the MQTT broker. When `false`, a silent no-op stub is used — no guards are needed in application code. |
| `ble_connection` | `persistent` | Controls the BLE connection strategy in **api mode**. `persistent` keeps a permanent BLE connection and polls on a timer (same as service mode). `on-demand` connects, queries, and disconnects for each request. Can be switched at runtime via `POST /config/ble-connection` or the MQTT topic `centralDevice/config/bleConnection`. Has no effect in service or cli mode. |

### `[API]`

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `0.0.0.0` | Interface to bind the HTTP server to. Use `127.0.0.1` to restrict to localhost. |
| `port` | `8080` | TCP port for the REST API and web UI. |

Used only in **api mode** (`--mode api`).

### `[ESPHOME]`

**Optional.** Use an ESP32 as a remote Bluetooth antenna instead of a local BLE adapter.

| Key | Default | Description |
|-----|---------|-------------|
| `host` | *(commented out)* | IP address or hostname of the ESP32 running ESPHome Bluetooth Proxy. When set, all BLE traffic is routed through the ESP32 over IP (port 6053). When absent or empty, the local Bluetooth adapter is used. **Example:** `192.168.0.154` or `aquaclean-proxy.local` |
| `port` | `6053` | ESPHome native API port. Default is `6053` — rarely needs changing. |
| `noise_psk` | *(commented out)* | **OPTIONAL and UNTESTED.** Base64-encoded encryption key for the ESPHome API. **Recommendation: Leave empty (no encryption) for initial setup.** Only add if you need API encryption: generate with `openssl rand -base64 32` and set matching `api_encryption_key` in the ESP32's `secrets.yaml`. Authentication and encryption have not been tested with this bridge. |
| `log_streaming` | `false` | Stream live logs from the ESP32 device and integrate them into the console app logging. Useful for debugging BLE proxy issues, but very verbose — keep disabled for production use. |
| `log_level` | `INFO` | Log level for ESP32 log streaming. Options: `ERROR`, `WARN`, `INFO`, `DEBUG`, `VERBOSE`. Only applies when `log_streaming = true`. |
For full setup instructions see [docs/esphome.md](esphome.md).

### `[LOGGING]`

| Key | Default | Description |
|-----|---------|-------------|
| `log_level` | `DEBUG` | Python logging level. `TRACE` and `SILLY` are custom levels added by the `haggis` library. `TRACE` logs every function entry/exit which is very verbose. |

---

## Config.ini is logged at startup

On startup (service and api modes) the full contents of `config.ini` are logged at INFO level. Sensitive keys (`noise_psk`, `password`) are redacted to `***`. This makes it easy to correlate a log file with the configuration that was active at the time.
