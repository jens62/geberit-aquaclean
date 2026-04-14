# BLE Connection Without Advertisement — Research Report

**Date:** 2026-04-04
**Context:** After a `ToggleLid` (or any `SetCommand`) call via the HACS integration + ESPHome
proxy, the Geberit stops advertising for ~60–90 seconds. The ESP32 proxy cannot connect during
that window (E0002), triggering the circuit breaker after 5 × 30 s failed scans. The same
scenario works fine with direct bleak (no ESP32 proxy).

---

## 1. BLE Spec: "Connectable Without Advertising" Is a Myth

Per the Bluetooth Core Spec (4.x / 5.x), connection establishment is advertisement-driven:

- A peripheral must send **ADV_IND** (undirected connectable) or **ADV_DIRECT_IND** (directed
  connectable) packets for a central to initiate a connection.
- When a central issues `HCI_LE_Create_Connection`, its controller scans advertising channels
  (37/38/39) for a matching advertisement from the target device, then sends `CONNECT_IND`.
- The peripheral's controller only accepts the `CONNECT_IND` while it is itself in an
  advertising state.

**There is no "connectable but not advertising" state** for undirected connections in the BLE
spec. A device that has stopped sending ADV_IND cannot accept an incoming GATT connection in
the strict spec sense.

### Why the bleak path "works" anyway — see section 3.

---

## 2. ESPHome Firmware — `has_cache` Does Not Bypass the Advertisement Requirement

### aioesphomeapi client.py

```python
if has_cache:
    request_type = BluetoothDeviceRequestType.CONNECT_V3_WITH_CACHE   # = 4
elif feature_flags & BluetoothProxyFeature.REMOTE_CACHING:
    request_type = BluetoothDeviceRequestType.CONNECT_V3_WITHOUT_CACHE  # = 5
else:
    raise ValueError(...)  # ESPHome < 2022.12 not supported
```

`has_cache=True` selects `CONNECT_V3_WITH_CACHE`. `has_cache=False` (with REMOTE_CACHING
feature bit set, which the AquaClean proxy has — `feature_flags=127`) selects
`CONNECT_V3_WITHOUT_CACHE`.

### ESPHome firmware — bluetooth_proxy.cpp

Both `CONNECT_V3_WITH_CACHE` and `CONNECT_V3_WITHOUT_CACHE` go through **identical** BLE
connection logic:

```cpp
connection->set_connection_type(V3_WITH_CACHE or V3_WITHOUT_CACHE);
uint64_to_bd_addr(msg.address, connection->remote_bda_);
connection->set_remote_addr_type(static_cast<esp_ble_addr_type_t>(msg.address_type));
connection->set_state(espbt::ClientState::DISCOVERED);  // same for both
```

Both variants set state to `DISCOVERED` and return. **No advertisement subscription check.
No scan-seen check.**

### ESPHome firmware — esp32_ble_tracker.cpp

The `loop()` function promotes `DISCOVERED` clients to `CONNECTING`:

```cpp
if (counts.discovered && !counts.connecting &&
    (this->scanner_state_ == ScannerState::RUNNING ||
     this->scanner_state_ == ScannerState::IDLE)) {
  this->try_promote_discovered_clients_();
}
```

**Both `RUNNING` and `IDLE` scanner states work.** This means `bluetooth_device_connect()`
can be called without an active `subscribe_bluetooth_le_raw_advertisements()` subscription
and the ESP32 will still attempt to connect.

`try_promote_discovered_clients_()` then calls `client->connect()` which calls:

```c
esp_ble_gattc_open(gattc_if, remote_bda, remote_addr_type, is_direct=true)
```

**`is_direct=true` is used for both `WITH_CACHE` and `WITHOUT_CACHE`.**

### The only actual difference between WITH_CACHE and WITHOUT_CACHE

After the GATT link opens (the `OPEN_EVT`):

- **`CONNECT_V3_WITHOUT_CACHE`**: calls `esp_ble_gattc_search_service()` to fetch the full
  GATT service list and sends it to the API client. Waits for MTU + services before declaring
  the connection `ESTABLISHED`.
- **`CONNECT_V3_WITH_CACHE`**: skips service discovery entirely. On `OPEN_EVT`, immediately
  calls `send_device_connection(address, true, mtu)` and transitions to `ESTABLISHED`.

**`has_cache` is purely a GATT service list caching optimisation. It does not affect BLE
link-layer connection establishment. Both variants require the device to be advertising.**

### What `is_direct=true` means in ESP-IDF

`esp_ble_gattc_open()` with `is_direct=true` tells the BLE controller to issue
`HCI_LE_Create_Connection` immediately, entering **INITIATING state**. The controller waits
for the target device to send an ADV_IND packet, then sends `CONNECT_IND`. If the device is
not advertising, the controller stays in INITIATING state until the connection timeout expires.

This is identical to what BlueZ's kernel does (see section 3).

### Why our probe test showed immediate disconnect with `has_cache=True`

The `esphome-no-adv-probe.py` script had a sequencing bug: Phase 2 called `unsub_phase2()`
which queued an `UnsubscribeBluetoothLEAdvertisementsRequest` frame. This frame flushed at
the first `await` inside `bluetooth_device_connect()`, disrupting the ESP32's BLE stack state
and causing an immediate disconnect. The firmware behavior itself (immediate fail with
`has_cache=True`) was an artifact of the probe script, not the firmware.

**Conclusion: `has_cache=True` would likely have worked in the probe — the test was invalid.**
However, it still does not help with devices that have stopped advertising, because the
underlying `is_direct=true` connect still requires the device to advertise.

---

## 3. BlueZ — Why Direct Bleak Works After ToggleLid

### `BleakScanner.find_device_by_address()` — does NOT use a cache

```python
async def find_device_by_filter(cls, filterfunc, timeout=10.0, **kwargs):
    async with cls(**kwargs) as scanner:
        async for bd, ad in scanner.advertisement_data():
            if filterfunc(bd, ad):
                return bd
```

This starts a **live scan** and waits for a fresh advertisement. If called during the
~60-90 second quiet window after ToggleLid, this call would also time out. So standalone
bleak does NOT magically bypass the advertisement requirement via a cache lookup.

### `BleakClient.connect()` — uses BlueZ device object, NOT a live scan

```python
reply = await self._bus.call(
    Message(
        destination=defs.BLUEZ_SERVICE,
        interface=defs.DEVICE_INTERFACE,
        path=self._device_path,
        member="Connect",
    )
)
```

This calls `org.bluez.Device1.Connect()` over D-Bus. The `_device_path` points to a
**previously created BlueZ device object** (e.g. `/org/bluez/hci0/dev_38_AB_41_2A_0D_67`).
BlueZ retains device objects indefinitely after a device has been seen at least once.

### What BlueZ does internally — device.c + adapter.c

```c
// device.c — device_connect_le()
io = bt_io_connect(att_connect_cb, dev, NULL, &gerr,
    BT_IO_OPT_DEST_BDADDR, &dev->bdaddr,
    BT_IO_OPT_DEST_TYPE, dev->bdaddr_type,
    BT_IO_OPT_CID, BT_ATT_CID,
    BT_IO_OPT_INVALID);
```

This issues `HCI_LE_Create_Connection` → kernel enters **INITIATING state**. The kernel
then enables passive scanning (`trigger_passive_scanning` in `adapter.c`) and adds the device
to its `connect_list`. When the device's next ADV_IND packet arrives, the kernel catches it
and completes the connection.

BlueZ internally uses a **connect list** (`GSList *connect_list`) that persists until the
connection succeeds or is explicitly cancelled. The kernel stays in INITIATING state and
catches the **first advertisement** the device sends after resuming.

### The asymmetry explained

| | Direct bleak (BlueZ) | ESPHome proxy |
|---|---|---|
| How connection is initiated | `HCI_LE_Create_Connection` → kernel INITIATING state, waits for next advertisement indefinitely (within timeout) | `subscribe_bluetooth_le_raw_advertisements()` → Python-side 30 s wait for advertisement → then `bluetooth_device_connect()` |
| Device quiet for 60–90 s after command | Kernel catches first re-advertisement when device resumes → **connection succeeds** | 30 s scan window expires before device resumes → E0002 |
| Advertisement scan required? | No — uses existing BlueZ device object | Yes — currently required to discover address_type |
| Connection timeout | Long (BlueZ/kernel default, minutes) | 30 s advertisement scan + 15 s GATT connect |

**The root cause of E0002 after ToggleLid**: the ESPHome proxy path requires a live
advertisement within a 30-second window. The Geberit stops advertising for ~60–90 seconds
after a command. The scan window expires first.

---

## 4. The Fix: Skip the Advertisement Scan on Reconnects

### Why the advertisement scan exists

The scan in `_connect_via_esphome()` serves one purpose: to discover `address_type` (PUBLIC=0
or RANDOM=1) from the advertisement packet, which is required as a parameter to
`bluetooth_device_connect()`.

For the Geberit AquaClean, `address_type` is **always 0 (PUBLIC)** — confirmed across all
connection logs and probe tests.

### The fix

Cache `address_type` after the first successful scan. On all subsequent connection attempts:

1. Skip `subscribe_bluetooth_le_raw_advertisements()` entirely.
2. Call `bluetooth_device_connect(mac, on_state, address_type=0, feature_flags=flags,
   has_cache=False, timeout=90)` directly.
3. The ESP32 firmware enters INITIATING state (via `esp_ble_gattc_open(..., is_direct=true)`).
4. When the Geberit resumes advertising (~60–90 s after the command), the ESP32 catches the
   first ADV_IND and completes the GATT connection.
5. No E0002, no circuit breaker, no user-visible error.

This is **exactly what BlueZ does**. The ESP32 firmware already supports it — the scan is
only a Python-side prerequisite for knowing address_type, not a firmware requirement.

### Timeout considerations

- **90-second timeout** on the `bluetooth_device_connect()` call (vs the current 30-second
  scan + 15-second connect).
- This is sufficient: the Geberit resumes advertising within ~60–90 seconds in all observed
  cases.
- For genuinely absent devices, the 90-second timeout still fires, returns E0002, and the
  circuit breaker handles it normally.

### Implementation location

- `coordinator._cached_ble_address_type: int | None = None` — cache persists across polls
- `BluetoothLeConnector._connect_via_esphome()`: if `address_type` is passed in (not None),
  skip the advertisement scan and go straight to `bluetooth_device_connect()` with the
  longer timeout.
- First-time connection (cache is None): run the advertisement scan as today, cache the result.

---

## 5. Secondary Finding: `stop_notify` AttributeError on ESP32 Path

Separate from the E0002 issue. In `BluetoothLeConnector._list_services()`, "preemptive
`stop_notify()`" is called on existing characteristics before setting up new notify
subscriptions. The method `stop_notify()` exists on a `BleakClient` (local BLE path) but
does NOT exist on `ESPHomeAPIClient`. The correct method name for the ESP32 path is
different (ESPHome manages GATT subscriptions server-side via `bluetooth_gatt_stop_notify`
or equivalent).

Functionally harmless: when `bluetooth_device_disconnect()` is called, the ESP32 firmware
tears down all GATT subscriptions server-side automatically. The client-side `stop_notify`
is redundant on the ESP32 path. But it makes the logs noisy and should be fixed.

---

## 6. Summary

| Finding | Implication |
|---------|-------------|
| BLE spec requires advertisement for connection | No "magic" bypass; device must cooperate |
| `has_cache` only affects GATT service discovery, not BLE link-layer | `has_cache=True` is not a fix |
| ESP32 firmware supports `bluetooth_device_connect()` in IDLE scanner state | Scan is a Python-side choice, not a firmware requirement |
| ESP32 uses `is_direct=true` → INITIATING state waits for re-advertisement | Same mechanism as BlueZ |
| BlueZ catches first re-advertisement via kernel `connect_list` | Not a cache — it's a patient kernel wait |
| Geberit quiet window: ~60–90 s after command (not 6 minutes) | The 6 min outage was caused by 5 × 30 s scans + 60 s circuit breaker sleep |
| Fix: cache address_type, skip scan, 90 s `bluetooth_device_connect()` timeout | Mirrors BlueZ behavior; E0002 chain eliminated |
