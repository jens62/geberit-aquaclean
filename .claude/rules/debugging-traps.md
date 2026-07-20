# Common Debugging Traps

Read this file first when something is broken.

---

1. **Polling stops after a REST query (webapp hangs)**
   → `_on_demand_lock` is stuck. Usually caused by an unhandled exception
   inside `_on_demand()` that prevents `finally` from running, or a
   mid-frame second BLE connect via a concurrent `_fetch_info` call.

2. **`set_poll_interval(0)` has no effect**
   → Check `ble_connection`. In persistent mode, only `_poll_interval_event`
   stops polling. In on-demand mode, only `_poll_wakeup` does.

3. **MQTT retained message resets poll interval at startup**
   → MqttService subscribes to `centralDevice/config/pollInterval`.
   The broker may deliver a retained message triggering `set_poll_interval`.

4. **Timing values static in webapp after a REST query**
   → `_set_ble_status("disconnected")` must NOT clear timing keys.
   Only `"connecting"` should clear them.

5. **Duplicate GATT frames growing each BLE reconnect**
   → Missing `remove_cb()` in `ESPHomeAPIClient.disconnect()`.

6. **Identification not shown in webapp on first page load (on-demand)**
   → Identification arrives via SSE after the first poll, not via `/info`.
   `onStateReceived()` calls `onIdentification()` when SSE state has
   `sap_number != null`.

7. **ESP32 proxy shows stale "Cannot reach…" error hint after recovery**
   → `_update_esphome_proxy_state` only updates `error_hint` when explicitly passed.
   Fix: when `error_code="E0000"` is set, `error_hint` is auto-cleared to `""`.
   (Added to `_update_esphome_proxy_state` logic.)

8. **Queries get slower with each poll in persistent ESPHome API mode**
   → `AquaCleanBaseClient.__init__` always does:
   `self.bluetooth_le_connector.data_received_handlers += self.frame_service.process_data`
   If `_on_demand_inner` creates a NEW `AquaCleanClientFactory(connector).create_client()`
   on every poll while reusing the same persistent connector, handlers accumulate.
   After N polls there are N handlers; each BLE GATT notification fires N times →
   N-fold slowdown.
   **Fix**: in persistent mode, `_get_esphome_connector()` also creates one
   `AquaCleanClient` (stored as `self._esphome_client`) and `_on_demand_inner` reuses it.
   **Do NOT call `AquaCleanClientFactory(connector).create_client()` on every poll
   when the connector is persistent.**

9. **App permanently stuck returning E7002 "Not connected to aquaclean-proxy" after ~90 min**
   → aioesphomeapi has a built-in 90-second ping timeout. When the TCP link goes quiet,
   aioesphomeapi sets `api._connection = None`. `self._esphome_api` stays non-None on
   `BluetoothLeConnector`, so `_ensure_esphome_api_connected()` keeps returning the dead
   client. Every subscribe attempt fails → E7002 → circuit breaker opens → 60s probes forever.
   **Fix** (in `_ensure_esphome_api_connected`): check
   `getattr(self._esphome_api, '_connection', None) is not None`.
   If `None`, log a warning, clear `self._esphome_api = None` and
   `self.esphome_proxy_connected = False`, then fall through to reconnect.

10. **"Only one API subscription is allowed at a time" on bridge restart**
    → `self._esphome_unsub_adv` was only stored AFTER the BLE GATT connect succeeded.
    When the bridge receives SIGTERM while waiting for a BLE advertisement, asyncio cancels
    the task. The `finally` block calls `disconnect_ble_only()`, but `self._esphome_unsub_adv`
    is still `None` — so the subscription is never released. The ESP32 holds it until ping
    timeout (~60–90 s).
    **Fix**: store `self._esphome_unsub_adv = unsub_adv` immediately after calling
    `api.subscribe_bluetooth_le_raw_advertisements()`, before the `asyncio.wait_for`.
    In the `TimeoutError` handler, clear `self._esphome_unsub_adv = None` after calling
    it to prevent a double-unsubscribe in `disconnect_ble_only()`.

11. **ANSI escape codes (`\033[1;31m` …) visible in log file from aioesphomeapi**
    → At `SILLY` log level, `main.py` skips suppressing the `aioesphomeapi` loggers.
    The `aioesphomeapi.connection` logger at DEBUG level logs raw protobuf payloads which
    include ANSI color codes embedded in ESP32 log messages.
    **Fix**: add a `logging.Filter` subclass (`_AnsiFilter`) to the `aioesphomeapi`
    logger at module level in `main.py`. Applied unconditionally — harmless at INFO level,
    essential at DEBUG/SILLY.

12. **"Only one API subscription" on every BLE scan — two-TCP conflict AND stale ref**
    Three independent bugs combine:
    **Bug A**: `_start_esphome_log_streaming()` creates a SECOND TCP connection to the
    same ESP32, claims the BLE subscription slot before the BLE connector can.
    **Fix A**: `_start_esphome_log_streaming()` returns immediately (with WARNING) when
    `esphome_host` is configured. Log streaming and the ESPHome BLE proxy are mutually exclusive.
    **Bug B**: `disconnect_ble_only()` was calling and nulling `_esphome_unsub_adv` after
    each BLE cycle. The defensive cleanup in `_connect_via_esphome()` always found it `None`
    and skipped. The old subscription was never released before the next subscribe attempt.
    **Fix B**: Remove unsub+null from `disconnect_ble_only()` — the reference stays intact
    until `_connect_via_esphome()` uses it at the start of the next request.
    **Bug C**: If the `aquaclean-proxy` ESPHome integration is **enabled** in Home Assistant,
    HA permanently holds the BLE subscription slot.
    **Fix C**: Disable (not delete) the `aquaclean-proxy` integration in HA → Settings → Integrations.
    See `docs/esphome-troubleshooting.md`.

13. **`asyncio.TimeoutError` from `BleakClient.connect()` not caught — wrong error code or crash**
    `BleakClient.connect()` raises `asyncio.TimeoutError` — which is **not** a subclass of
    `BleakError`. Three places each had a gap:

    | Location | Symptom without fix |
    |---|---|
    | `ServiceMode.run()` | Falls to `handle_exception()` → `sys.exit(1)` — **bridge crashes** |
    | `_on_demand_inner` finally | Wrong error shown: E7002 instead of E0003 |
    | `_polling_loop` | Publishes E7002 to MQTT instead of E0003 |

    **Fix applied**: Added explicit `except asyncio.TimeoutError` handlers in all three
    locations, treating them identically to `BleakError` — mapped to E0003, no crash.

14. **Alba via ESPHome: ATT error 3 (Write Not Permitted) on 559eb001 — wrong write type**
    `_raw_write` in `BluetoothLeConnector._post_connect()` hardcodes `response=True`
    (ATT_WRITE_REQUEST). The Alba's `559eb001` characteristic has WRITE_NO_RESP property
    only — peripheral returns ATT error 0x03. Local BlueZ is permissive and masked this.
    **Fix:** `response=False` (ATT_WRITE_COMMAND) in `_raw_write`.

    ATT error code guide:
    | ATT error | Code | Cause | Fix |
    |-----------|------|-------|-----|
    | Invalid Handle | 0x01 | Stale NimBLE NVS GATT cache | Press "Clear Bluetooth Cache" on ESPHome proxy |
    | Write Not Permitted | 0x03 | Write type mismatch | Use `response=False` |

    If ESP32 log says "Connecting v3 without cache", the NimBLE cache is ruled out — any
    remaining write failure is a type mismatch, not a cache issue.

15. **Alba HACS: device occupied 90% of time — entities grey after first poll (habluetooth path)**
    **Root cause A**: In the HA-BLE path, a new `BluetoothLeConnector` and `AlbaClient` are
    created each poll. `post_connect()` always runs `DataPointInventory` (78 DpId definitions,
    ~200 ms per frame × 78 = ~15–16 s). Total poll time ~27 s out of a 30 s interval.
    **Root cause B**: `Ble20Client.RECV_TIMEOUT = 15.0 s` too short for 78-frame inventory
    (78 × 200 ms ≈ 15.6 s).
    **Fix (committed 2026-05-22)**:
    1. `Ble20Client.RECV_TIMEOUT` raised from 15 s to 30 s.
    2. `AlbaClient.post_connect(inventory=None)` — skips DataPointInventory if `inventory`
       is passed (coordinator cache) OR if `self._inventory` is already populated.
    3. `coordinator.py` — `_alba_inventory: dict = {}` cached after first detection;
       passed to `connect_ble_only(inventory=...)` on all subsequent polls.
    Result: poll time drops from ~27 s to ~14 s; device free for ~16 s per 30 s interval.

16. **Mock service: Geberit Home App can't find the mocked device ("not in Bluetooth range")
    even at close range, immediately after a mock restart — adapter looks fine at HCI level**
    → Check `sudo bluetoothctl show hci0` (or whichever adapter). If it reports "Controller
    hciN not available" **even as root**, while `hciconfig -a` shows the same adapter UP
    RUNNING at the kernel level, `bluetoothd`'s D-Bus-level view of the controller has
    desynced from the kernel — the raw HCI socket still works, but BlueZ's daemon doesn't
    expose it as a controller, so nothing above it (bluetoothctl, the mock, the phone) can
    use it.
    **Root cause (confirmed via `dmesg`, 2026-07-17):** repeated
    `Bluetooth: hciN: Unexpected advertising set terminated event` kernel messages — a known
    quirk on some BLE controllers (confirmed on the Realtek USB dongle used for Mera's `hci0`)
    where the kernel's advertising-set bookkeeping gets out of sync with the controller.
    Triggered by rapid advertise/unregister/re-register cycles — both mocks intentionally do
    this on every connect to force a fast BLE connection interval (see `_Advertisement`'s
    "unregister then re-register with `{MinInterval: 100, MaxInterval: 100}`" fix in
    `alba_mock.py`/`mera_mock.py`) — and made worse by `kill -9`-ing a mock process, since
    that skips the `finally:` cleanup that unregisters the GATT app/advertisement cleanly.
    **Fix (confirmed sufficient in practice):**
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl restart bluetooth
    sudo bluetoothctl show hci0   # confirm it now shows up
    ```
    **If that's not enough, escalate in order:**
    ```bash
    sudo hciconfig hci0 down && sudo hciconfig hci0 up   # kernel-level toggle only
    sudo modprobe -r btusb && sudo modprobe btusb        # full USB driver reload
    # last resort: physically unplug/replug that USB Bluetooth dongle
    ```
    **Prevention:** prefer a clean stop (Ctrl-C/SIGTERM, wait for actual exit) over `kill -9`
    when possible — `kill -9` skips advertisement/GATT unregistration and makes this kind of
    controller-state confusion more likely to accumulate over repeated cycles.

17. **A real device attempting SMP pairing floods the kernel log with "unexpected SMP command
    0x03" (hundreds/thousands per minute) and the mock never completes pairing**
    → Check `journalctl -u bluetooth` for the same time window. `src/device.c:new_auth() No
    agent available for request type 2` / `device_confirm_passkey: Operation not permitted`
    confirms the cause: no BlueZ pairing agent registered.
    **Fixed, v1.104.0b1**: `mera_mock.py` registers `bluez_peripheral.agent.NoIoAgent` at
    startup — confirmed via re-test that the flood and the missing-agent error are both gone
    and full SMP bonding completes. If this recurs anyway (e.g. on `alba_mock.py`, which
    hasn't had the same fix applied yet), `sudo systemctl stop bluetooth` is still the only
    known way to silence an active flood.
    Full incident, root-cause reasoning, and fix confirmation:
    `docs/developer/mock-geberit-mera.md` §"Button-press/release timing".

---

## Known open bugs (not yet fixed)

**`send_request()` — `call_count` not decremented on `asyncio.CancelledError`**
File: `aquaclean_core/Clients/AquaCleanBaseClient.py`, `send_request()`, lines ~315–334.
When a task running `send_request()` is cancelled externally, `CancelledError` propagates
and `call_count` stays at 1 permanently. Every subsequent `send_request()` call blocks forever.
Fix: replace separate `call_count -= 1` sites with a single `finally` block.
Confirmed in: `aquaclean-silly_2026-03-06_16-17-48.log`.

**`wait_for_info_frames_async` — no absolute timeout**
File: `aquaclean_core/Frames/FrameService.py`.
On some devices, the InfoFrame flood after a BLE reconnect lasts up to ~3 minutes.
`wait_for_info_frames_async` blocks until stable for 2s or count ≥ 10 — no hard cap.
Fix: add a 60s wall-clock deadline. Confirmed in same log as above.
