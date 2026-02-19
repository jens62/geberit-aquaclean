# ESPHome Bluetooth Proxy — Developer Notes

Technical documentation of the ESPHome Bluetooth Proxy integration in the AquaClean console app.

---

## Why aioesphomeapi instead of bleak-esphome?

**Problem:** `bleak-esphome` v3.x requires Home Assistant's `habluetooth` infrastructure.

```python
from bleak_esphome import connect  # requires habluetooth.BluetoothManager
```

When used standalone, it fails with:

```
RuntimeError: BluetoothManager has not been set
```

**Solution:** Use `aioesphomeapi` directly — the official ESPHome API client library that Home Assistant itself uses internally.

- ✅ Works standalone (no Home Assistant dependency)
- ✅ Official, well-maintained library
- ✅ Already proven with `ble-scan.py` and probe scripts
- ✅ Direct control over the ESPHome native API
- ✅ Clean wrapper pattern maintains bleak compatibility with minimal code changes

---

## Architecture

```
BluetoothLeConnector (unchanged interface)
    ↓
ESPHomeAPIClient (new bleak-compatible wrapper)
    ↓
aioesphomeapi.APIClient (official ESPHome API client)
    ↓
ESP32 Bluetooth Proxy (ESP32-POE-ISO running ESPHome)
    ↓
AquaClean (BLE device)
```

**Key insight:** The wrapper (`ESPHomeAPIClient`) translates between bleak's UUID-based interface and aioesphomeapi's integer handle-based protocol. This keeps the rest of the codebase unchanged — `AquaCleanBaseClient` still calls `client.write_gatt_char(uuid, data)` without knowing whether it's talking to a local adapter or an ESP32 proxy.

---

## Technical Challenges

### 1. UUID vs Handle Interface

**Bleak interface (UUID-based):**
```python
await client.write_gatt_char("8bae4825-ad84-4d85-9d87-b67b8d6ac395", data)
await client.start_notify("8bae4825-ad84-4d85-9d87-b67b8d6ac395", callback)
```

**aioesphomeapi interface (handle-based):**
```python
await api.bluetooth_gatt_write(mac_int, handle=0x002a, data=data, response=True)
await api.bluetooth_gatt_start_notify(mac_int, handle=0x002a, on_notify)
```

**Solution:** Build a UUID ↔ handle mapping during service discovery:

```python
resp = await api.bluetooth_gatt_get_services(mac_int)
for svc in resp.services:
    for char in svc.characteristics:
        self._uuid_to_handle[char.uuid] = char.handle
        self._handle_to_uuid[char.handle] = char.uuid
```

Then in `write_gatt_char(uuid, data)`:

```python
handle = self._uuid_to_handle[uuid]
await self._api.bluetooth_gatt_write(self._mac_int, handle, data, response=True)
```

### 2. Connection State Management

**Challenge:** aioesphomeapi uses a callback for connection state changes instead of a blocking `connect()` method.

**Solution:** Use a `Future` to convert the callback into an awaitable:

```python
connected_future = asyncio.Future()

def on_bluetooth_connection_state(connected: bool, mtu: int, error: int):
    if connected and not error:
        self._is_connected = True
        connected_future.set_result(mtu)
    elif error:
        connected_future.set_exception(Exception(f"Connection error: {error}"))
    else:  # disconnected
        self._is_connected = False
        if self._disconnected_callback:
            self._disconnected_callback(self)

cancel = await api.bluetooth_device_connect(mac_int, on_bluetooth_connection_state)
await asyncio.wait_for(connected_future, timeout=30.0)
```

### 3. BLE Device Scanning

**Challenge:** aioesphomeapi doesn't have a "scan for devices and return a list" API. It only provides raw advertisement callbacks.

**Solution:** Subscribe to `subscribe_bluetooth_le_raw_advertisements()` and wait for the target device:

```python
mac_int = int(device_id.replace(":", ""), 16)
found_event = asyncio.Event()

def on_raw_advertisements(resp):
    for adv in resp.advertisements:
        if adv.address == mac_int:
            found_event.set()

unsub = api.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
try:
    await asyncio.wait_for(found_event.wait(), timeout=30.0)
finally:
    unsub()
```

**Device names:** Extract from raw advertisement data using AD structure parsing:

```python
def parse_local_name(data: bytes) -> str:
    """Extract device name from BLE advertisement AD structures."""
    i = 0
    name = ""
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        value = data[i + 2 : i + 1 + length]
        if ad_type == 0x09:  # Complete Local Name
            return value.decode("utf-8", errors="replace")
        elif ad_type == 0x08:  # Shortened Local Name
            name = value.decode("utf-8", errors="replace")
        i += 1 + length
    return name
```

### 4. Notification Routing

**Challenge:** Notifications arrive as `(handle, data)` tuples, but bleak expects callbacks registered by UUID.

**Solution:** Store callbacks by handle, route notifications through the handle→UUID mapping:

```python
# Registration
def start_notify(self, uuid, callback):
    handle = self._uuid_to_handle[uuid]
    self._notify_callbacks[handle] = callback
    await self._api.bluetooth_gatt_start_notify(self._mac_int, handle, self._on_notify)

# Routing
def _on_notify(self, handle: int, data: bytes):
    if handle in self._notify_callbacks:
        uuid = self._handle_to_uuid[handle]
        char_wrapper = ESPHomeGATTCharacteristic(uuid, handle)
        self._notify_callbacks[handle](char_wrapper, data)
```

### 5. ESP32 Proxy State Tracking

**Challenge:** The app needs to know if the ESP32 proxy is enabled, connected, and which device it is.

**Solution:** Track proxy state in `esphome_proxy_state` dict and publish to MQTT + webapp:

```python
self.esphome_proxy_state = {
    "enabled": esphome_host is not None,
    "connected": False,
    "name": "",
    "host": esphome_host or "",
    "port": esphome_port if esphome_host else "",
    "error": "No error"
}
```

Update state on connection:

```python
device_info = await api.device_info()
self.esphome_proxy_name = getattr(device_info, "name", "unknown")
await self._update_esphome_proxy_state(connected=True, name=self.esphome_proxy_name)
```

**MQTT topics:**
- `{topic}/esphomeProxy/enabled` — true/false
- `{topic}/esphomeProxy/connected` — true/false
- `{topic}/esphomeProxy/error` — error string

**Home Assistant discovery:** 4 entities (2 binary sensors, 2 sensors) published on startup.

### 6. ESP32-Aware Recovery Protocol

**Challenge:** The original recovery protocol used local `BleakScanner` to wait for the AquaClean to disappear/reappear after a restart command. This fails when using an ESP32 proxy because the local Bluetooth adapter can't see the device.

**Solution:** Dual-path recovery based on connection method:

```python
async def wait_for_device_restart(self, device_id):
    if esphome_host:
        await self._wait_for_device_restart_via_esphome(device_id, topic)
    else:
        await self._wait_for_device_restart_local(device_id, topic)
```

**ESP32 path:** Connect to the ESP32 API, subscribe to raw advertisements, check presence:

```python
async def _wait_for_device_restart_via_esphome(self, device_id, topic):
    api = APIClient(...)
    await api.connect(login=True)
    mac_int = int(device_id.replace(":", ""), 16)

    # Phase 1: wait for device to disappear (powered off)
    # Phase 2: wait for device to reappear (powered back on)
    await self._check_device_via_esphome(api, mac_int)
```

### 7. Log Streaming

**Challenge:** ESP32 logs are useful for debugging BLE proxy issues, but streaming them continuously is very verbose.

**Solution:** Optional feature with persistent API connection:

```python
async def _start_esphome_log_streaming(self):
    if not esphome_log_streaming or not esphome_host:
        return

    self._esphome_log_api = APIClient(...)
    await self._esphome_log_api.connect(login=True)

    self._esphome_log_unsub = await self._esphome_log_api.subscribe_logs(
        self._on_esphome_log_message,
        log_level=esphome_log_level
    )

def _on_esphome_log_message(self, log_entry):
    prefix = f"[ESP32:{log_entry.tag}]"
    # Map ESP32 log levels to Python logging
    if log_entry.level == LogLevel.LOG_LEVEL_ERROR:
        logger.error(f"{prefix} {log_entry.message}")
    # ...
```

**Log level mapping:**

| ESPHome Level | Python Level |
|---------------|-------------|
| LOG_LEVEL_ERROR | ERROR |
| LOG_LEVEL_WARN | WARNING |
| LOG_LEVEL_INFO | INFO |
| LOG_LEVEL_DEBUG | DEBUG |
| LOG_LEVEL_VERBOSE | TRACE/DEBUG |

**Key decisions:**
- Separate API connection for logs (doesn't interfere with BLE operations)
- Disabled by default (`log_streaming = false`)
- Configurable log level filter
- `[ESP32:tag]` prefix for clarity

### 8. API Connection Lifecycle

**BLE operations (on-demand mode):**
```
connect() → bluetooth_device_connect() → bluetooth_gatt_get_services() →
write_gatt_char() → bluetooth_gatt_write() → disconnect() →
bluetooth_device_disconnect() + api.disconnect()
```

**Log streaming (persistent connection):**
```
_start_esphome_log_streaming() → api.connect() → subscribe_logs() →
[runs until shutdown] → unsubscribe → api.disconnect()
```

**Critical fix:** `ESPHomeAPIClient.disconnect()` must call `await self._api.disconnect()` in the finally block to close the TCP connection to the ESP32. Without this, the API connection stays open and prevents clean reconnection.

---

## Key Design Decisions

### 1. Wrapper Pattern vs Direct Integration

**Decision:** Create a bleak-compatible wrapper (`ESPHomeAPIClient`) instead of refactoring the entire codebase.

**Rationale:**
- ✅ Minimal code changes — only `BluetoothLeConnector._connect_via_esphome()` changed
- ✅ `AquaCleanBaseClient` unchanged — still uses `client.write_gatt_char(uuid, data)`
- ✅ Easy to maintain both local BLE and ESP32 proxy paths
- ✅ Future-proof — if bleak-esphome becomes standalone in the future, we can swap back

### 2. On-Demand API Connections

**Decision:** Create a fresh `APIClient` connection per BLE request, disconnect after completion.

**Rationale:**
- ✅ Matches the on-demand BLE philosophy — no persistent state
- ✅ Clean separation between requests
- ✅ No connection pooling complexity
- ❌ ~200 ms overhead per request (TCP + ESPHome handshake)
- ❌ More verbose logs (connect/disconnect per request)

**Exception:** Log streaming uses a persistent API connection separate from BLE operations.

### 3. State Updates at Disconnect

**Decision:** Update `esphome_proxy_state` and call `_update_esphome_proxy_state(connected=False)` in the finally block of `ServiceMode.run()`.

**Rationale:**
- ✅ State always reflects reality (even after exceptions)
- ✅ MQTT and webapp SSE receive "disconnected" events
- ✅ Home Assistant entities update correctly

### 4. Interface Consistency

**Decision:** All control interfaces (REST API, MQTT, webapp, CLI) must support ESPHome proxy operations consistently.

**Example:** Connect/disconnect buttons work via:
- REST API: `POST /connect`, `POST /disconnect`
- MQTT: `{topic}/centralDevice/connect`, disconnect topic
- Webapp: Connect/Disconnect buttons → POST /connect, POST /disconnect
- All trigger the same `request_reconnect()` / `request_disconnect()` methods

---

## FAQ

### Q: Why not use bleak-esphome?

**A:** `bleak-esphome` v3.x requires Home Assistant's `habluetooth.BluetoothManager`, which is not available in standalone applications. Using `aioesphomeapi` directly removes this dependency.

### Q: Why integer handles instead of UUIDs?

**A:** The ESPHome native API protocol uses integer handles for GATT operations. We build a UUID↔handle mapping during service discovery to translate between bleak's UUID interface and aioesphomeapi's handle interface.

### Q: Does the ESP32 proxy work with on-demand BLE mode?

**A:** Yes! Each request connects to the ESP32 API, scans for the device, connects via BLE, performs GATT operations, disconnects BLE, and disconnects the API connection. The entire cycle takes ~1-2 seconds.

### Q: Why is log streaming disabled by default?

**A:** ESP32 logs are very verbose — at DEBUG level the device can log hundreds of messages per minute (BLE scans, WiFi events, system status). This pollutes the console app logs with unrelated ESP32 noise. Enable it only when debugging proxy issues.

### Q: Can I use multiple ESP32 proxies?

**A:** Not with the current implementation — `config.ini` specifies a single `[ESPHOME] host`. To support multiple proxies you'd need a device-to-proxy mapping or proximity detection.

### Q: What happens if the ESP32 dies mid-operation?

**A:** The recovery protocol is ESP32-aware. If the ESP32 becomes unreachable, the next connection attempt will fail with a timeout. The app does NOT fall back to local BLE automatically — you must change `config.ini` to disable the proxy (`host` commented out) and restart.

**Future improvement:** Auto-fallback to local BLE if ESP32 is unreachable.

### Q: Does the webapp show ESP32 proxy status?

**A:** Yes! The webapp has an "ESPHome Proxy" panel that shows:
- Enabled/disabled status
- Connected/disconnected state
- ESP32 device name, host, and port
- Error messages (if any)

The panel auto-hides when the proxy is disabled.

### Q: How do I debug BLE connection issues with the ESP32 proxy?

**A:** Enable log streaming:

```ini
[ESPHOME]
log_streaming = true
log_level = DEBUG
```

Restart the app and watch for `[ESP32:bluetooth_proxy]` and `[ESP32:esp32_ble_tracker]` log entries. You'll see:
- BLE scan results
- Connection attempts
- GATT operations
- Disconnection events
- WiFi signal strength

### Q: Can I use the ESP32 web UI to see BLE activity?

**A:** Yes! Open `http://<esp32-ip>/` in a browser. The ESPHome web UI shows live logs. Change the log level to DEBUG in the YAML and OTA reflash to see BLE events.

### Q: What's the latency overhead of the ESP32 proxy vs local BLE?

**A:** Approximately:
- **Local BLE:** 1.0-1.5 s per on-demand request
- **ESP32 proxy:** 1.5-2.0 s per on-demand request (+500 ms for API connection overhead)

The overhead is acceptable for occasional polling (every 10+ seconds). For real-time monitoring at sub-second intervals, local BLE is faster.

### Q: Does the ESP32 proxy support persistent BLE mode?

**A:** Yes, but it's not recommended. Persistent mode keeps the ESP32 BLE connection open permanently, which defeats the purpose of on-demand mode and can lead to the same "device stops responding" issue as the original implementation. Use on-demand mode instead.

### Q: How do I secure the ESPHome API?

**A:** **API encryption/authentication is UNTESTED and not recommended.** Start with an open API (no encryption) on a trusted LAN.

If you absolutely need encryption, proceed at your own risk:

1. Generate an encryption key:
   ```bash
   openssl rand -base64 32
   ```

2. **ESP32 (`esphome/secrets.yaml`):**
   ```yaml
   api_encryption_key: "your-base64-key-here"
   ```

3. **Python bridge (`config.ini`):**
   ```ini
   [ESPHOME]
   noise_psk = your-base64-key-here
   ```

4. Uncomment the `api: encryption:` block in the proxy YAML and OTA reflash.

**Warning:** This configuration has not been tested. Connection failures, authentication errors, and other issues may occur. If the bridge fails to connect after enabling encryption, revert to no encryption (comment out `noise_psk` and remove `api: encryption:` from the YAML).

### Q: What if the ESP32 and Python bridge are on different network segments?

**A:** The ESPHome native API uses TCP port 6053. Ensure firewall rules allow traffic between the two hosts. Test with `nc -zw1 <esp32-ip> 6053`.

### Q: Can I run multiple instances of the Python bridge connecting to the same ESP32?

**A:** Not recommended. The ESP32 can only maintain one BLE connection at a time. Multiple bridge instances would compete for the BLE slot, causing connection failures.

---

## Reference Files

| File | Purpose |
|------|---------|
| `aquaclean_console_app/bluetooth_le/LE/ESPHomeAPIClient.py` | Bleak-compatible wrapper around aioesphomeapi |
| `aquaclean_console_app/bluetooth_le/LE/BluetoothLeConnector.py` | Connection logic — `_connect_via_esphome()` method |
| `aquaclean_console_app/main.py` | ESP32 proxy state tracking, MQTT publishing, log streaming |
| `esphome/aquaclean-proxy-eth.yaml` | ESP32 config for Ethernet/PoE |
| `esphome/aquaclean-proxy-wifi.yaml` | ESP32 config for WiFi |
| `esphome/ble-scan.py` | Test script — scan via ESP32 proxy |
| `local-assets/esphome-aioesphomeapi-probe.py` | Proof-of-concept — connect, read, write via aioesphomeapi |
| `docs/esphome.md` | User-facing ESPHome setup guide |
| `docs/esphome-troubleshooting.md` | Common ESPHome issues and fixes |

---

## Lessons Learned

1. **Always close the API connection** — Forgetting `await api.disconnect()` in `ESPHomeAPIClient.disconnect()` left TCP connections open, preventing clean reconnection.

2. **Dual-path recovery is critical** — The recovery protocol must handle both local BLE and ESP32 proxy, or it silently falls back to the wrong adapter.

3. **Log streaming needs lifecycle management** — Don't mix BLE operation API connections with log streaming connections. Keep them separate for clean shutdown.

4. **UUID↔handle mapping is fragile** — If service discovery fails, the entire connection breaks. Always log the mapping for debugging.

5. **Interface consistency prevents bugs** — MQTT, REST API, and webapp must all update `esphome_proxy_state` consistently, or the UI shows stale data.

6. **AD structure parsing is brittle** — BLE advertisement data follows a specific format (length, type, value). Off-by-one errors cause device names to be garbled or empty.

7. **On-demand mode works great with ESP32** — The ~500 ms API connection overhead is acceptable when requests are spaced 10+ seconds apart.

8. **Test both paths** — Always verify that local BLE still works after adding ESP32 proxy support. Don't accidentally break the default path.
