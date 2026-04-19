# Polling Architecture — How It Works

Technical documentation of the polling and request lifecycle in the AquaClean bridge.
Covers `ServiceMode`, `ApiMode._polling_loop`, `AquaCleanClient.start_polling()`, and
`AquaCleanBaseClient.send_request()`.

---

## Overview

Polling is the process of periodically calling `GetSystemParameterList` to read the
live state of the toilet (user sitting, shower running, dryer on/off) and publishing
the result via MQTT and SSE. The implementation differs between `persistent` and
`on-demand` BLE connection modes.

---

## How It Works

### ServiceMode — Persistent BLE Mode

`ServiceMode.run()` owns the BLE connection and the polling loop when
`ble_connection = persistent`. It runs an outer recovery loop (reconnects on error)
and an inner polling loop.

**Outer loop (recovery):**
```
while True:
    await client.connect(device_id)       ← blocks until connect() completes
    inner polling loop                     ← runs until polling_task raises
    await client.disconnect()
    await reconnect_delay()
```

**`connect()` internals** (`AquaCleanBaseClient.connect_async`):
1. `connector.connect_async(device_id)` — BLE scan + GATT connect + notify subscriptions
2. `frame_service.wait_for_info_frames_async()` — waits for the device's startup InfoFrame
   flood to stabilize (see **InfoFrame flooding** below)
3. After `connect_async` returns: `AquaCleanClient.connect()` calls
   `GetSOCApplicationVersions`, `GetDeviceIdentification`, `GetDeviceInitialOperationDate`
   via `send_request()` — these succeed during or just after the InfoFrame flood

**Inner loop (polling interval control):**
```python
while True:
    polling_task   = asyncio.create_task(start_polling(interval))
    reconnect_task = asyncio.ensure_future(_reconnect_event.wait())
    shutdown_task  = asyncio.ensure_future(_shutdown_event.wait())
    poll_change_task = asyncio.ensure_future(_poll_interval_event.wait())

    done, pending = await asyncio.wait(
        [polling_task, reconnect_task, shutdown_task, poll_change_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    if poll_change_task in done:
        _poll_interval_event.clear()
        continue                      ← restart with new interval, NO reconnect
    if reconnect_task in done:
        break                         ← fall through to outer reconnect loop
    if polling_task in done:
        break                         ← polling raised BLEPeripheralTimeoutError
    if shutdown_task in done:
        return                        ← clean shutdown
```

**`_poll_interval_event` semantics:**
- Set by `set_poll_interval()` when the interval is changed at runtime
- When set at the time `asyncio.wait` runs, `poll_change_task` completes immediately
- The inner loop cancels `polling_task` and creates a new one with the updated interval
- This cancellation is the trigger for the **CancelledError race condition** (see below)

---

### `AquaCleanClient.start_polling()`

Called as an asyncio task by `ServiceMode`. Infinite loop:

```python
async def start_polling(self, interval: float, on_poll_done=None):
    logger.info(f"Starting status polling loop (interval: {interval}s)")
    while True:
        start = datetime.datetime.now()
        await self._state_changed_timer_elapsed()   ← calls GetSystemParameterList
        delta = datetime.datetime.now() - start
        millis = int(delta.total_seconds() * 1000)
        if on_poll_done:
            await on_poll_done(millis)
        await asyncio.sleep(interval)
```

`_state_changed_timer_elapsed()` calls `get_system_parameter_list_async([0,1,2,3,4,5,7,9])`
which calls `send_request()`. If `send_request()` raises `BLEPeripheralTimeoutError`, it
propagates up through `start_polling()`, exits the task, and is caught by ServiceMode's
outer recovery loop which then triggers a reconnect.

`start_polling()` has **no circuit breaker** — it raises on the very first timeout.

---

### `ApiMode._polling_loop` — On-Demand Mode

When `ble_connection = on-demand`, polling is driven by `ApiMode._polling_loop`. It:
1. Skips via `continue` if `ble_connection != "on-demand"` (persistent mode runs its own)
2. Waits for `_poll_wakeup` event or `_poll_interval` sleep
3. Calls `_on_demand()` → `_on_demand_inner()` which: connects BLE, polls, disconnects
4. Tracks `_consecutive_poll_failures` with a **circuit breaker** at 5 failures:
   - After 5 consecutive failures: logs "Circuit open", adds 60s extra sleep before each probe
   - On recovery: logs "Poll recovered after N failures", resets `_identification_fetched`

`_polling_loop` runs **concurrently** with `ServiceMode` even in persistent mode — but the
`if self.ble_connection != "on-demand": continue` guard keeps it from doing any work.

---

### `AquaCleanBaseClient.send_request()` — Request Serialization

All BLE requests go through `send_request()`. It serializes requests with a counter:

```python
async def send_request(self, api_call):
    # 1. Pre-entry guard — wait until no other request is in flight
    while self.call_count > 0:
        await asyncio.sleep(0.1)       # yields to event loop every 100ms

    # 2. Claim the slot
    with self.lock:
        self.call_count += 1           # threading.Lock for atomic increment

    # 3. Build and send the BLE frame
    data    = self.build_payload(api_call)
    frame   = self.frame_factory.BuildSingleFrame(message.serialize())
    self._transaction_event.clear()
    await self.frame_service.send_frame_async(frame)
    await asyncio.sleep(0.01)          # 10ms settle

    # 4. Wait for device response (max 5 seconds)
    timeout_seconds = 5.0
    try:
        await asyncio.wait_for(self._transaction_event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        with self.lock:
            self.call_count -= 1
        raise BLEPeripheralTimeoutError(...)

    # 5. Release the slot and return
    with self.lock:
        self.call_count -= 1
    return api_call
```

The `_transaction_event` is set by `on_transaction_completeForBaseClient` which is called
by the `TransactionCompleteFS` event chain when the last CONS frame arrives and
`FrameCollector.TransactionCompleteFC` fires.

**Request/response timing:** Normal cycle is ~600ms. The 5s timeout is a generous margin.

---

### InfoFrame Flooding

On every BLE connection, the device sends ~10 `INFO` frames before it will respond to
any requests. This is documented normal behavior (original README, commit `f76f8e1`).

`wait_for_info_frames_async()` polls `info_frame_count` every 100ms and breaks when:
- Count reaches ≥ 10, OR
- Count has not changed for 20 consecutive polls (2 seconds of stability)

**Observed timing:**

| Scenario | Duration |
|----------|----------|
| First BLE connect (device in clean/fresh state) | ~1 second |
| Reconnect after brief disconnect | **up to 2–3 minutes** |

After an abrupt disconnect, the device continues flooding InfoFrames for minutes before
stabilizing. During this window, `connect_async()` blocks inside `wait_for_info_frames_async`,
and any concurrent `_polling_loop` GetSystemParameterList requests will time out with E0003.

This is **expected device behavior** — the bridge must wait it out.

---

## Command vs. Poll Concurrency — Is There a Conflict?

The device only accepts one active BLE session at a time. The question is whether a user
command (from the web UI, REST API, HACS, or CLI) can collide with an in-progress
GetSystemParameterList (poll) on the same session.

### On-demand mode (default) — fully serialized

Every BLE operation goes through `ApiMode._on_demand_inner()`, which acquires
`ApiMode._on_demand_lock` (`asyncio.Lock`) before opening a BLE connection.
Both the polling loop and user commands acquire the same lock, so they are strictly
serialized:

- Poll running → user command waits at `async with self._on_demand_lock` until the
  poll's BLE session closes, then opens its own session.
- Command running → polling loop waits the same way.

**No conflict is possible in on-demand mode.**

The web UI reflects this: buttons show a spinner and are disabled during an in-flight
request (`loading` class), giving the user visual feedback that the lock is held.

### Persistent mode — soft serialization via `call_count`

The BLE connection is always open. Both polling and user commands share the same
`AquaCleanClient` instance and call `send_request()` on it.

`send_request()` guards entry with a busy-wait:

```python
while self.call_count > 0:
    await asyncio.sleep(0.1)
with self.lock:
    self.call_count += 1
```

If GetSPL is mid-flight (`call_count == 1`), a user command will spin at 100 ms
intervals until it completes, then proceed. In practice this works correctly.

**Known weakness:** there is a TOCTOU race. Two coroutines can both exit the
`while call_count > 0` loop (both see `0`), then both increment — ending at
`call_count == 2` with two requests in flight simultaneously. This is unlikely
in practice because asyncio is single-threaded and `await asyncio.sleep(0.1)` is
the only yield point between the check and the increment, but it is not a proper
mutual-exclusion guarantee.

Additionally, `call_count` is not decremented on `asyncio.CancelledError` — see
the Known Issues section below. A proper `asyncio.Lock` would fix both gaps, but
that change has not been made yet.

**Summary:** safe in practice in persistent mode, but relies on a soft busy-wait
rather than a hard lock.

---

## Known Issues

### CancelledError in `send_request()` Leaves `call_count = 1` Permanently

**File:** `aquaclean_core/Clients/AquaCleanBaseClient.py`, `send_request()`, lines ~315–334

**The bug:** `call_count` is decremented on `asyncio.TimeoutError` and on success, but
**not on `asyncio.CancelledError`**. If the task running `send_request()` is cancelled
while at `await asyncio.wait_for(...)`, the `CancelledError` propagates without
decrementing `call_count`. Every subsequent `send_request()` call blocks forever on
`while call_count > 0`.

**Trigger mechanism — `_poll_interval_event` race:**

1. A retained MQTT message (e.g. `centralDevice/config/pollInterval`) fires during
   `wait_for_info_frames_async` → `set_poll_interval()` → `_poll_interval_event.set()`
2. `connect()` returns → ServiceMode inner loop creates `polling_task` #1
3. Event loop runs `polling_task` #1 until it yields: logs "Starting status polling loop",
   enters `send_request()`, increments `call_count` to 1, sends GetSystemParameterList,
   reaches `await asyncio.wait_for(...)`
4. `asyncio.wait(...)` returns immediately — `poll_change_task` was already done
5. `polling_task` #1 is cancelled → `CancelledError` at `await asyncio.wait_for(...)`
6. `call_count` stays at **1** (not decremented)
7. ServiceMode `continue`s → creates `polling_task` #2
8. `polling_task` #2 starts: `while call_count > 0` → **blocks forever**

ServiceMode is now stuck waiting for `polling_task` to complete. The bridge appears alive
(REST API, SSE still work) but all polling is permanently dead until restart.

**Log signature to identify this failure:**
```
HH:MM:SS.xxx  AquaCleanClient 60 INFO: Starting status polling loop (interval: Xs)
HH:MM:SS.xxx  Sending GetSystemParameterList — self.call_count: 0 <= 0   ← enters OK
HH:MM:SS.xxx  AquaCleanClient 60 INFO: Starting status polling loop (interval: Xs)
HH:MM:SS.xxx  Sending GetSystemParameterList — self.call_count: 1 > 0    ← STUCK
HH:MM:SS.xxx  CONTROL frame — FlowControlFrame ErrorCode=0x00              ← device ACK
... self.call_count: 1 > 0 every 100ms for minutes — no further progress ...
```

The two "Starting status polling loop" lines appear within ~2ms of each other. The device
ACK (FlowControlFrame `ErrorCode=0x00`) confirms the first request was sent, but the
response will never be processed because the receiving task was cancelled.

**Fix (not yet applied):**
```python
with self.lock:
    self.call_count += 1
try:
    # ... build frame, send, sleep 10ms ...
    await asyncio.wait_for(self._transaction_event.wait(), timeout=timeout_seconds)
except asyncio.TimeoutError:
    raise BLEPeripheralTimeoutError(...)
finally:
    with self.lock:
        self.call_count -= 1           # ← always decrement, including on CancelledError
```

---

## MQTT Retained Message — Trap 3

MQTT brokers deliver retained messages immediately on subscribe. When the bridge starts
and subscribes to `{topic}/centralDevice/config/pollInterval`, a retained message from a
previous session is delivered immediately. If this arrives during `wait_for_info_frames_async`
it calls `set_poll_interval()` → sets `_poll_interval_event`.

Combined with the CancelledError bug above, this retained message can reliably trigger
the permanent polling deadlock on every restart where the InfoFrame flood is long.

**Workaround (until fix is applied):** Remove the retained MQTT message by publishing
an empty payload to the topic, or ensure MQTT is unreachable during testing.

---

## Related Files

| File | Role |
|------|------|
| `aquaclean_console_app/main.py` | `ServiceMode.run()` — outer/inner loop; `ApiMode._polling_loop()` |
| `aquaclean_console_app/aquaclean_core/Clients/AquaCleanClient.py` | `start_polling()`, `_state_changed_timer_elapsed()`, `connect()` |
| `aquaclean_console_app/aquaclean_core/Clients/AquaCleanBaseClient.py` | `send_request()`, `connect_async()`, `on_transaction_completeForBaseClient()` |
| `aquaclean_console_app/aquaclean_core/Frames/FrameService.py` | `wait_for_info_frames_async()`, `send_frame_async()`, `TransactionCompleteFS` event |
| `aquaclean_console_app/aquaclean_core/Frames/FrameCollector.py` | `add_frame()`, `TransactionCompleteFC` event |
| `memory/ble-internals.md` | Full frame processing chain, CancelledError bug detail, InfoFrame timing |
