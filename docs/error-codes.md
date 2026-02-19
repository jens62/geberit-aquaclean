# Error Code Reference

When errors occur, the AquaClean bridge reports structured error codes across all interfaces (MQTT, REST API, CLI, and web UI).

## Error Code Format

```
E[Category][Number]
```

- **E** = Error prefix
- **Category** = Single digit (0-9) indicating error category
- **Number** = Three digits (001-999) for specific error

**Example:** `E0003` = BLE Connection Errors category (0) → BLE connection timeout (003)

---

## Error Categories

| Category | Code Range | Description |
|----------|------------|-------------|
| **Success** | E0000 | Success state - no error occurred |
| **BLE Connection** | E0001-E0999 | Bluetooth LE connection and communication errors |
| **ESP32 Proxy** | E1xxx | ESPHome Bluetooth Proxy connection and operation errors |
| **Recovery Protocol** | E2xxx | Automatic recovery protocol errors and timeouts |
| **Command Execution** | E3xxx | Command processing and MQTT command errors |
| **API/HTTP** | E4xxx | REST API request validation and operation errors |
| **MQTT** | E5xxx | MQTT broker connection and publishing errors |
| **Configuration** | E6xxx | Configuration parsing and validation errors |
| **System** | E7xxx | Internal system errors and critical failures |

---

## Success Code

| Code | Message | Description |
|------|---------|-------------|
| **E0000** | No error | Success state - no error has occurred. Published when errors are cleared or operations complete successfully. |

---

## E0xxx - BLE Connection Errors

Errors related to Bluetooth LE connectivity with the AquaClean device.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E0001** | BLE device not found (local adapter) | Device not in range or not advertising | Move closer to device; check device is powered on; verify MAC address in config |
| **E0002** | BLE device not found (ESP32 proxy) | ESP32 cannot see the device | Move ESP32 closer to toilet; check ESP32 is scanning (`esphome logs`) |
| **E0003** | BLE connection timeout | Connection attempt exceeded timeout | Check device is powered on; reduce distance; check for interference |
| **E0004** | GATT service not found | Expected service UUID not present | Device may be incompatible; check device is AquaClean model |
| **E0005** | GATT characteristics not found | Expected characteristic UUID not present | Device may be incompatible or firmware version issue |
| **E0006** | Characteristic read failed | GATT read operation failed | Retry operation; check BLE connection quality |
| **E0007** | Characteristic write failed | GATT write operation failed | Retry operation; check BLE connection quality |
| **E0008** | BLE disconnected unexpectedly | Connection lost during operation | Check interference; verify device is powered on; recovery will attempt reconnection |
| **E0009** | Start notify failed | Failed to enable notifications | Retry connection; check device compatibility |

---

## E1xxx - ESP32 Proxy Errors

Errors when using an ESPHome Bluetooth Proxy (ESP32) as a remote BLE bridge.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E1001** | ESP32 proxy connection timeout | Cannot reach ESP32 within timeout | Verify ESP32 IP address in config; check network connectivity (`ping <ip>`); verify port 6053 is open |
| **E1002** | ESP32 proxy connection failed | TCP connection refused or network error | Check ESP32 is powered on; verify ESPHome API is enabled; check firewall rules |
| **E1003** | ESP32 BLE connection error | ESP32 reported BLE connection error | Check ESP32 logs (`esphome logs`); verify device is in range of ESP32 |
| **E1004** | ESP32 log streaming failed | Optional log streaming encountered error | Check ESP32 connectivity; disable `log_streaming` if not needed |
| **E1005** | ESP32 device info failed | Could not fetch device info from ESP32 | Check ESP32 is running recent ESPHome version; verify API compatibility |
| **E1006** | ESP32 service fetch failed | GATT service enumeration failed | Retry connection; check ESP32 logs for BLE errors |
| **E1007** | ESP32 write timeout | GATT write operation timed out | Check BLE signal strength; reduce distance between ESP32 and device |
| **E1008** | ESP32 notification worker error | Internal error processing notifications | Check logs for details; restart bridge if persistent |

---

## E2xxx - Recovery Protocol Errors

Errors during the automatic recovery protocol when BLE connections fail.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E2001** | Recovery: Device won't disappear (ESP32) | Device still advertising after power cycle wait | Manually power cycle device; check ESP32 scanning |
| **E2002** | Recovery: Device won't reappear (ESP32) | Device not advertising after expected recovery time | Check device is powered on; verify ESP32 can scan BLE devices |
| **E2003** | Recovery: Device won't disappear (local) | Device still advertising after power cycle wait | Manually power cycle device; check Bluetooth adapter |
| **E2004** | Recovery: Device won't reappear (local) | Device not advertising after expected recovery time | Check device is powered on; verify local Bluetooth adapter |
| **E2005** | Recovery: ESP32 proxy connection failed | Cannot reconnect to ESP32 during recovery | Check ESP32 network connectivity; verify ESP32 is running |

---

## E3xxx - Command Execution Errors

Errors when executing commands via CLI, REST API, or MQTT.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E3001** | Command failed: BLE not connected | Command requires BLE connection but not connected | Wait for connection to establish; check BLE connectivity |
| **E3002** | Command failed: Unknown command | Invalid or unsupported command | Check command syntax; see CLI/API/MQTT documentation |
| **E3003** | Command failed: Execution error | Command execution failed | Check logs for details; verify device state |
| **E3004** | MQTT command: Toggle anal failed | Failed to toggle anal shower via MQTT | Check BLE connection; verify MQTT payload format |
| **E3005** | MQTT command: Set connection failed | Failed to change connection mode via MQTT | Verify payload value (`persistent` or `on-demand`) |
| **E3006** | MQTT command: Set poll interval failed | Failed to change poll interval via MQTT | Verify payload is valid number (≥2.5) |
| **E3007** | MQTT command: Disconnect failed | Failed to disconnect BLE via MQTT | Check logs for details |

---

## E4xxx - API/HTTP Errors

Errors from REST API requests and validation.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E4001** | Invalid BLE connection mode | Invalid value in `/set_connection_mode` | Use `persistent` or `on-demand` only |
| **E4002** | Invalid poll interval | Invalid value in `/set_poll_interval` | Use number ≥2.5 seconds |
| **E4003** | BLE client not connected | REST API endpoint requires BLE connection | Wait for connection; check BLE status at `/status` |
| **E4004** | SSE timeout (heartbeat) | Server-Sent Events heartbeat timeout | Reconnect web UI; check network connection |

---

## E5xxx - MQTT Errors

Errors related to MQTT broker connectivity and publishing.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E5001** | MQTT connection failed | Cannot connect to MQTT broker | Verify broker IP/hostname in config; check broker is running; verify credentials if authentication enabled |
| **E5002** | MQTT publish failed | Failed to publish message to topic | Check MQTT broker connection; verify topic permissions |
| **E5003** | MQTT disconnect failed | Error during MQTT disconnection | Non-critical; check logs if persistent |
| **E5004** | MQTT invalid poll interval value | Poll interval command has invalid value | Send numeric value ≥2.5 to poll interval topic |

---

## E6xxx - Configuration Errors

Errors parsing or validating configuration values.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E6001** | Config: Poll interval parse failed | Cannot parse `[POLL] interval` value | Check config.ini `[POLL] interval` is valid number |
| **E6002** | Config: API poll interval parse failed | Cannot parse API poll interval value | Verify numeric value in config |

---

## E7xxx - Internal/System Errors

Internal system errors and critical failures.

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **E7001** | Shutdown timeout | Graceful shutdown exceeded timeout | Non-critical; application will force-stop |
| **E7002** | Poll loop error | Error in background polling loop | Check logs for details; may auto-recover |
| **E7003** | Service discovery error (fatal) | Critical error during GATT service discovery | Restart bridge; check device compatibility |
| **E7004** | General exception | Unhandled exception occurred | Check logs for stack trace; report as bug if persistent |

---

## Error Formats by Interface

Errors are formatted differently depending on which interface you're using:

### MQTT

Published to `{topic}/centralDevice/error` as JSON:

```json
{
  "code": "E0003",
  "message": "BLE connection timeout: Device not responding",
  "timestamp": "2026-02-19T10:30:45.123Z"
}
```

Subscribe to errors:
```bash
mosquitto_sub -h 192.168.0.xxx -t "Geberit/AquaClean/centralDevice/error" -v
```

### REST API

Included in HTTP error responses:

```json
{
  "status": "error",
  "error": {
    "code": "E4001",
    "message": "Invalid BLE connection mode: must be 'persistent' or 'on-demand'"
  }
}
```

HTTP status codes:
- `400 Bad Request` - Invalid input (E4xxx)
- `503 Service Unavailable` - BLE not connected (E4003)
- `500 Internal Server Error` - System errors (E7xxx)

### CLI

Printed to stderr with severity prefix:

```
ERROR [E0003]: BLE connection timeout: Device not responding after 30s
WARNING [E2001]: Recovery: Device won't disappear (ESP32): Device still advertising after 2 minutes
```

JSON output mode includes error code:
```json
{
  "status": "error",
  "error_code": "E0003",
  "message": "BLE connection timeout: Device not responding after 30s"
}
```

### Web UI

Displayed in the connection status card:

```
Status: Error
[E0003] BLE connection timeout
```

Also logged to browser console for debugging.

---

## Common Error Scenarios

### Scenario: ESP32 proxy unreachable

**Symptoms:**
- CLI/API/UI shows `E1001` or `E1002`
- MQTT error topic shows ESP32 connection timeout

**Troubleshooting:**
1. Ping ESP32: `ping 192.168.0.xxx`
2. Check port 6053: `nc -zw1 192.168.0.xxx 6053 && echo open || echo closed`
3. Check ESP32 logs: `esphome logs esphome/aquaclean-proxy-eth.yaml --device 192.168.0.xxx`
4. Verify config.ini `[ESPHOME] host` matches ESP32 IP

### Scenario: Device not found

**Symptoms:**
- `E0001` (local adapter) or `E0002` (ESP32 proxy)
- Recovery protocol may trigger repeatedly

**Troubleshooting:**
1. Verify device is powered on and in range
2. Check MAC address in config.ini matches device
3. For ESP32: verify device is in range of ESP32 (`esphome logs` shows BLE advertisements)
4. For local: verify Bluetooth adapter is working (`bluetoothctl scan on`)

### Scenario: Connection works but commands fail

**Symptoms:**
- `E3001` (BLE not connected) or `E3003` (execution error)
- REST API returns 503 Service Unavailable

**Troubleshooting:**
1. Check connection mode: `curl http://localhost:8080/status`
2. In on-demand mode: expect per-request connect/disconnect
3. In persistent mode: verify `ble_status: "connected"` in status response
4. Check logs for underlying BLE errors (E0xxx)

### Scenario: Recovery protocol stuck

**Symptoms:**
- `E2001`/`E2002` (ESP32) or `E2003`/`E2004` (local)
- Recovery protocol loops without success

**Troubleshooting:**
1. Manually power cycle the AquaClean device
2. Check ESP32 can scan BLE devices (try `python esphome/ble-scan.py <esp32-ip>`)
3. Restart the bridge application
4. Check interference from other BLE devices

---

## Automation with Error Codes

Error codes enable automated responses in home automation systems.

### Home Assistant Automation Example

```yaml
automation:
  - alias: "AquaClean Error Alert"
    trigger:
      platform: mqtt
      topic: "Geberit/AquaClean/centralDevice/error"
    condition:
      template: >
        {{ trigger.payload_json.code in ['E0003', 'E1001', 'E1002'] }}
    action:
      - service: notify.mobile_app
        data:
          title: "AquaClean Connection Error"
          message: >
            {{ trigger.payload_json.message }}
```

### Node-RED Error Handling

```javascript
// Check for critical errors and restart service
if (msg.payload.code.startsWith('E1') || msg.payload.code.startsWith('E7')) {
    // ESP32 or system error - restart service
    msg.payload = { command: "systemctl restart aquaclean" };
    return msg;
}
```

---

## Getting Help

- **Check logs first:** Set `[LOGGING] log_level = DEBUG` in config.ini
- **ESP32 issues:** See [esphome-troubleshooting.md](esphome-troubleshooting.md)
- **Report bugs:** Include error code and full log output at [GitHub Issues](https://github.com/thomas-bingel/geberit-aquaclean/issues)

---

## See Also

- [Configuration Reference](configuration.md) - All config.ini settings
- [ESPHome Setup](esphome.md) - ESP32 Bluetooth Proxy setup
- [MQTT Topics](mqtt.md) - MQTT topic structure and commands
- [REST API](rest-api.md) - REST API endpoint reference
