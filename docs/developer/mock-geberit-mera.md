# Mock Geberit AquaClean Mera Comfort BLE Peripheral

**File:** `tools/mock-geberit-mera.py`

Simulates the AquaClean Mera Comfort BLE peripheral on Linux (BlueZ). Supports the
full Geberit Home App "Connection 1" onboarding flow — button-press ceremony, A6
InfoFrame burst, and procedure responses (GetDeviceIdentification, GetFirmwareVersionList,
GetSystemParameterList, etc.).

No encryption, no SMP — the Mera Comfort BLE link layer is unencrypted (confirmed from
real-device packet capture).

---

## Requirements

- Linux with BlueZ ≥ 5.50 (Bluetooth daemon running)
- Python packages: `dbus-next`, `bluez_peripheral`, `aiohttp`
- Run as root (D-Bus system bus access)
- BlueZ experimental features enabled (`Experimental=true` in `/etc/bluetooth/main.conf`)

```bash
pip install dbus-next bluez_peripheral aiohttp
sudo /home/jens/venv/bin/python tools/mock-geberit-mera.py [--port 8766]
```

---

## Test session setup

### Step 1 — Start btmon (mock machine)

Captures all BLE HCI events to a timestamped btsnoop file. Run in a dedicated terminal
**before** starting the mock:

```bash
sudo btmon -w ~/mock-geberit-mera_btmon_$(date +%F_%H-%M).btsnoop
```

### Step 2 — Start the mock (mock machine)

```bash
sudo /home/jens/venv/bin/python tools/mock-geberit-mera.py 2>&1 \
  | tee ~/mock-geberit-mera_$(date +%F_%H-%M).log
```

Wait for `--- Mera Comfort Mock Active ---` and note the adapter MAC address.

### Step 3 — Trigger Connection 1

1. Open the Geberit Home App (iOS or Android).
2. Wait for the device to appear in the scan list (mock advertises with `IsButtonPressed=False`).
3. Open the mock web UI at `http://<vm-ip>:8766/` and press **"Press Button"**.
4. The advertisement updates to `IsButtonPressed=True` — the app detects this and connects.

---

## GATT profile

Single vendor service `3334429d-90f3-4c41-a02d-5cb3a03e0000`, 7 characteristics:

| UUID suffix | Properties | Role |
|-------------|-----------|------|
| `...a13e0000` | WRITE_WITHOUT_RESPONSE | Procedure requests (app → mock) |
| `...a23e0000` | WRITE_WITHOUT_RESPONSE | Multi-frame continuation writes |
| `...a53e0000` | NOTIFY | A5 — primary response channel |
| `...a63e0000` | NOTIFY | A6 — CONS continuation + Connection 1 trigger |
| `...a73e0000` | NOTIFY | A7 — CONS continuation |
| `...a83e0000` | NOTIFY | A8 — CONS continuation |
| `00003a2b-...` | READ | Button-state probe — returns `b"ro"` |

The real Mera Comfort handle map (from nRF52840 capture) is at
`docs/developer/mera-home-app-onboarding.md`.

---

## Connection 1 flow

The Geberit Home App "Connection 1" onboarding requires this exact sequence:

1. App scans BLE advertisements, detects `IsButtonPressed=True` in manufacturer data.
2. App connects and performs GATT discovery — finds all 7 characteristics.
3. App writes CCCD on A6 (enables notify).
4. **Mock sends 9× A6 InfoFrame burst** (`800130140c030003000000003130001200b70800`) —
   this is the Connection 1 trigger; the app will not call GetDeviceIdentification until
   it receives at least one A6 notify.
5. App calls GetDeviceIdentification (proc `0x82`), GetFirmwareVersionList (`0x0E`), and
   the standard polling procedures.

The burst fires automatically when iOS writes the A6 CCCD (enables A6 notify). The mock
detects this by polling `notify_a6_char._notify` at 100 ms intervals and sends the burst
the instant BlueZ sets it to `True`. A fixed timer MUST NOT be used — the timer always
fires after iOS has already shown "cannot connect" and disconnected. Event-driven is the
only correct approach (mock v1.32.0+).

**Source:** nRF52840 capture of iOS app v2.14.1 against real Mera Comfort
(`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/`).

---

## Battery plugin behavior (iOS only)

Two independent mechanisms interact when an iOS device connects for the first time in
a bluetoothd session:

1. **BlueZ battery plugin (GATT client)** — BlueZ immediately reads Battery Level
   from the *connected iOS device's* GATT server. iOS returns `0x05` (Insufficient
   Authentication). BlueZ then tries to initiate pairing.
   - `pairable=on` (**wrong**): BlueZ sends an SMP Security Request to iOS — iOS
     shows a "Kopplungsanforderung" (pairing dialog) to the user, interrupting the
     Connection 1 flow. **Do not set `btmgmt pairable on`.**
   - `pairable=off` (BlueZ default): BlueZ cannot start pairing → immediately
     disconnects with HCI reason `0x05` → **first connection killed at ~3 s**.
     This is acceptable — the first connection dying is expected behavior.

2. **iOS GATT client reads mock Battery Level** — iOS reads the mock's own Battery
   Level characteristic during GATT discovery. The mock registers a `BatteryService`
   (UUID `0x180F`) that returns `bytes([100])` without authentication, so iOS sees a
   clean value and does not disconnect.

**First connection dying is expected and harmless.** The battery plugin only probes
each iOS device once per bluetoothd session. On the second connection (same RPA),
the battery plugin skips already-probed devices → fully benign, connection proceeds
normally. The Connection 1 flow succeeds on the second connection.

The mock explicitly calls `btmgmt pairable off` at startup (v1.32.0+) to reset any
lingering `pairable=on` state from older versions — that state persists across mock
restarts because bluetoothd is not restarted. Do **not** change this to `pairable=on`.

Do **not** add `DisablePlugins = battery` — it is not needed.

---

## Known issues

### dbus_next async queue — pre-registration InterfacesAdded race (fixed in mock v1.30.0)

**Symptom:** GATT discovery finds only 2 characteristic declarations out of 7 (3a2b at
handle 0x0118 + A5 at 0x011A). A6, A7, A8, A1, A2 are allocated a handle range
(service range is exactly right for 7 chars, e.g. 0x0117–0x0129 = 19 handles) but
have **no ATT Characteristic Declaration** (type `0x2803`). iOS's `ATT Read By Type
uuid=0x2803` response for the range 0x011A–0x0129 returns only A5 (1 item, MTU=517
allows 24 — BlueZ confirms there are no more). iOS cannot subscribe to A6 → the
Connection 1 flow never completes.

**Root cause — dbus_next async write queue:**

`dbus_next`'s async `MessageBus.send()` does not write to the D-Bus socket
immediately. It queues messages via `_writer.schedule_write()` (FIFO). This means:

1. `service.register()` calls `Service._export()`, which calls `bus.export()` for
   each of the 7 characteristics.
2. Each `bus.export()` call fires `_emit_interface_added()`, which calls
   `bus.send(InterfacesAdded signal)` — queuing it for async writing.
3. After all 7 characteristics are exported, `ServiceCollection.register()` calls
   `await manager.call_register_application(path, {})`.
4. The `await` suspends the coroutine and the asyncio event loop runs the write
   callback, which drains the queue **in FIFO order**: the 7 `InterfacesAdded`
   signals are written to the D-Bus socket **before** the `RegisterApplication`
   method call.

BlueZ therefore receives `InterfacesAdded` for all 7 characteristics **before** it
receives `RegisterApplication`. BlueZ processes these pre-registration signals and
creates a preliminary GDBusClient watcher entry for each characteristic. When
`RegisterApplication` then arrives, BlueZ calls `GetManagedObjects` (which correctly
returns all 7 characteristics), but its internal watcher dedup logic sees
characteristics 2–6 as already-tracked objects and skips creating ATT Characteristic
Declaration (`0x2803`) attributes for them. The handle allocations are made (hence the
correct 19-handle service range), but no char decls → iOS char discovery finds only 2.

**Key evidence:**
- Both v1.26.1 (btsnoop `mock-geberit-mera_btmon_2026-06-22_11-27-android.btsnoop`,
  pre-Includes-fix) and v1.29.0 (iOS, `mock-geberit-mera_btmon_2026-06-22_14-08.btsnoop`,
  post-Includes-fix) show identical 2-char-decl behavior. The Includes fix only removed
  the self-include `0x2802` attribute; the 2-char-decl root cause is independent and
  was present in both versions.
- Service handle range is correct for 7 chars (allocations prove BlueZ received all 7
  from GetManagedObjects), yet only 2 char decls exist (proving BlueZ skipped creating
  `0x2803` entries for chars 2–6 after the pre-registration watcher entries were made).

**Fix (mock v1.30.0):** Suppress `_emit_interface_added` during the initial GATT
export so BlueZ learns about all characteristics exclusively from `GetManagedObjects`
(the canonical path). `bus.export()` still adds every characteristic to
`_path_exports` (line 120 of `message_bus.py` runs before `_emit_interface_added`),
so `GetManagedObjects` returns all 7 and BlueZ creates char decls for all 7.

```python
from dbus_next.message_bus import BaseMessageBus as _MB
_orig_emit = _MB._emit_interface_added
_MB._emit_interface_added = lambda *a, **kw: None
try:
    await service.register(bus, "/org/bluez/example/mera", adapter_wrapper)
    await battery_service.register(bus, "/org/bluez/example/battery", adapter_wrapper)
finally:
    _MB._emit_interface_added = _orig_emit
```

After both `service.register()` calls return, `_emit_interface_added` is restored.
Any post-startup dynamic GATT updates (not used by this mock) would work normally.

---

### bluez_peripheral 0.1.7 — self-include bug (fixed in mock v1.27.0)

**Symptom:** On mock versions before v1.27.0, the self-include issue added an
`ATT Include Declaration` (`0x2802`) inside the vendor service (visible in
pre-v1.27.0 btsnoop captures). This consumed one handle slot and compounded the
2-char-decl problem from the root cause above.

**Root cause:** `bluez_peripheral.gatt.service.Service.Includes` unconditionally
appended `self._path`:

```python
# bluez_peripheral 0.1.7 — BUGGY
def Includes(self) -> "ao":
    paths = []
    for service in self._includes:
        if not service._path is None:
            paths.append(service._path)
    paths.append(self._path)   # ← always appends own path → self-include
    return paths
```

**Fix (mock v1.27.0):** `MeraService` overrides `Includes` to return an empty list,
eliminating the spurious Include Declaration:

```python
@dbus_property(PropertyAccess.READ)
def Includes(self) -> "ao":  # type: ignore
    return []
```

This override survives venv reinstalls without any manual library patching. The
pre-registration InterfacesAdded race (separate root cause, above) was already present
in v1.27.0–v1.29.0 and was only fixed in v1.30.0.

---

## btmon correlation tool

`tools/analyze-btmon-mock.py` correlates a btmon btsnoop capture with a mock log
to produce a unified timeline. Auto-detects the clock offset between btmon and mock
by matching ATT Write Command payloads.

```bash
/Users/jens/venv/bin/python tools/analyze-btmon-mock.py \
  path/to/capture.btsnoop path/to/mock.log
```

Flags: `--att-only`, `--no-color`, `--summary-only`, `--gap MS`, `--offset-ms FLOAT`

Always use this tool for btsnoop analysis — do not write ad-hoc decoders.

---

## Current status — mock v1.31.0 (2026-06-22)

| Feature | Status |
|---------|--------|
| BLE advertising with `IsButtonPressed` toggle | ✅ |
| All 7 char declarations visible | ✅ v1.30.0 (pre-registration InterfacesAdded fix) |
| A6 InfoFrame burst (Connection 1 trigger) | ✅ v1.26.0 |
| No pairing dialog (`btmgmt pairable off` at startup) | ✅ v1.32.0 |
| `IsButtonPressed` latched until burst sent | ✅ v1.28.0 |
| GetDeviceIdentification (proc `0x82`) | ✅ |
| GetFirmwareVersionList (proc `0x0E`) | ✅ |
| GetSystemParameterList (proc `0x0D`) | ✅ |
| GetDeviceInitialOperationDate (proc `0x86`) | ✅ |
| GetFilterStatus (proc `0x59`) | ✅ |
| Web UI button press + live state | ✅ |
| Full Connection 1 → GetDeviceIdentification flow | ⏳ pending iOS test on v1.32.0 |
