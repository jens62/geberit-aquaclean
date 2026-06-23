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

The Geberit Home App "Connection 1" onboarding requires **two BLE connections**
(v1.36.0+, see [SC flush](#sc-flush--ios-corebluetooth-cache)):

**BLE Connection 1 — cache update (force-disconnected at 700 ms):**

1. App detects `IsButtonPressed=True` in the BLE advertisement and connects.
2. iOS CoreBluetooth runs ATT characteristic discovery. With the patched
   `gatt-server.c`, all 7 chars are returned via two Read By Type passes
   (`RBT [0015–0027]: 7 attr(s)`). CoreBluetooth updates its peripheral cache
   from the pre-patch stale 2-char entry to the correct 7-char result.
3. Mock force-disconnects at 700 ms — ATT discovery finishes in ~500 ms;
   700 ms is enough to update the cache before the app layer acts on the
   (potentially stale) cached list.

**BLE Connection 2 — protocol exchange:**

4. iOS retries automatically with the same RPA (`IsButtonPressed` stays `True`).
5. CoreBluetooth delivers the updated 7-characteristic list to the app delegate.
6. App writes CCCD on A6 (enables notify).
7. **Mock sends 9× A6 InfoFrame burst** (`800130140c030003000000003130001200b70800`) —
   this is the Connection 1 trigger; the app will not call GetDeviceIdentification until
   it receives at least one A6 notify.
8. App calls GetDeviceIdentification (proc `0x82`), GetFirmwareVersionList (`0x0E`), and
   the standard polling procedures.

The burst fires automatically when iOS writes the A6 CCCD (enables A6 notify). The mock
detects this by polling `notify_a6_char._notify` at 100 ms intervals and sends the burst
the instant BlueZ sets it to `True`. A fixed timer MUST NOT be used — the timer always
fires after iOS has already shown "cannot connect" and disconnected. Event-driven is the
only correct approach (mock v1.32.0+).

**Source:** nRF52840 capture of iOS app v2.14.1 against real Mera Comfort
(`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/`).

---

## SC flush — iOS CoreBluetooth cache (v1.36.0+)

iOS CoreBluetooth caches GATT characteristic lists by **peripheral MAC address**
(not by iOS RPA). Sessions from before the `gatt-server.c` RBT patch (2026-06-23)
left a stale 2-char cache for our adapter MAC (`A0:AD:9F:72:C4:0F`) — only `3a2b`
and `A5`, the only two characteristics the broken BlueZ reported. This cache persists
across iPad reboots.

**Symptom without SC flush:** iOS connects, CoreBluetooth delivers the stale 2-char
list to the app delegate immediately while concurrently running fresh ATT discovery in
the background. The ATT layer correctly finds all 7 chars (visible in the `bluetoothd
-d` log as `RBT [0015-0027]: 7 attr(s)`), but the app already moved on with 2 chars —
A6 not in the list → no CCCD write → no burst → app shows "connection could not be
established."

**SC flush mechanism (v1.36.0+):** BLE Connection 1 lets iOS run ATT discovery (which
updates the CoreBluetooth cache from 2 → 7 chars), then force-disconnects at 700 ms
before the app layer acts on the stale list. iOS retries automatically with the same
RPA as BLE Connection 2, where CoreBluetooth delivers the fresh 7-char list to the app.

The `_sc_flush_done` flag (one-shot) ensures only BLE Connection 1 is flushed; all
subsequent connections go directly to the A6 burst flow.

The flush only triggers when `IsButtonPressed=True`. Pre-button-press auto-reconnects
from iOS (old RPA arriving before the user presses the physical button) bypass the
flush — their A6 task exits cleanly because no CCCD is written.

**Stale-disconnect guard:** `_on_device_disconnected` gates on `_current_device_path`.
iOS sometimes sends a deferred `Connected=False` PropertiesChanged signal for an
already-disconnected old RPA after a new RPA has connected. Without this guard that
stale signal would clobber `_connected` for the live connection.

### Battery plugin interaction

The BlueZ battery plugin (GATT client) reads Battery Level from the connected iOS
device immediately on connection. iOS returns `0x05` (Insufficient Authentication).
With `pairable=off` (BlueZ default), BlueZ cannot start pairing → disconnects at ~3 s.

The SC flush fires at 700 ms, well before the battery plugin's 3 s kill. Run
bluetoothd with `--noplugin=battery` to eliminate the battery plugin entirely and
keep the two-connection flow clean and predictable:

```bash
sudo bluetoothd --noplugin=battery -d 2>&1 | tee ~/bluetoothd-debug.log
```

The mock registers a `BatteryService` (UUID `0x180F`) returning `bytes([100])`
without authentication, so iOS reading the mock's own battery level never causes
a disconnect (same mechanism as `mock-geberit-alba.py`).

The mock calls `btmgmt pairable off` at startup (v1.32.0+). Do **not** change this
to `pairable=on` — that triggers an iOS pairing dialog interrupting the flow.

---

## Known issues

### 2-char-decl bug — FIXED via `gatt-server.c` patch (2026-06-23)

**Symptom:** GATT discovery finds only 2 characteristic declarations out of 7 (3a2b at
handle 0x0016 + A5 at 0x0018). iOS stops char discovery after A5 and never finds
A6–A2. Without A6 discoverable, iOS cannot write A6's CCCD → Connection 1 never
completes.

**Confirmed GATT handle layout** (from `bluetoothd -d` debug log, 2026-06-22 21:17):

| Handle | Attribute | Type |
|--------|-----------|------|
| 0x0015 | Service decl | — |
| 0x0016 | 3a2b char decl | 16-bit UUID → item\_len=7 |
| 0x0017 | 3a2b value | READ |
| 0x0018 | A5 char decl | 128-bit UUID → item\_len=21 |
| 0x0019 | A5 value | NOTIFY |
| 0x001a | A5 CCC | — |
| 0x001b | A6 char decl | 128-bit |
| 0x001c | A6 value | NOTIFY |
| 0x001d | A6 CCC | — |
| 0x001e | A7 char decl | 128-bit |
| 0x001f | A7 value | NOTIFY |
| 0x0020 | A7 CCC | — |
| 0x0021 | A8 char decl | 128-bit |
| 0x0022 | A8 value | NOTIFY |
| 0x0023 | A8 CCC | — |
| 0x0024 | A1 char decl | 128-bit |
| 0x0025 | A1 value | WRITE\_WITHOUT\_RESPONSE |
| 0x0026 | A2 char decl | 128-bit |
| 0x0027 | A2 value | WRITE\_WITHOUT\_RESPONSE |

All 7 chars + 4 CCCDs = 19 handles (correct). **BlueZ has all 7 char decls at the
correct handles.** Proven by `database_add_chrc()` firing 7 times with correct handles.

**Why iOS only sees A5** (confirmed from debug log lines 360–364, iOS RBT sequence):

```
Read By Type [0x0015, 0x0027]:
  → 3a2b at 0x0016  (item_len=7, 1 item)   ← 3a2b is 16-bit UUID; A5 has different
                                               item_len=21, so BlueZ stops at size boundary
  → iOS next start = value_handle(0x0017)+1 = 0x0018

Read By Type [0x0018, 0x0027]:
  → A5 only at 0x0018  (item_len=21, 1 item, PDU=23 bytes)
  → 23 < MTU(517)–1=516: ATT spec says "no more matching attrs in range" → iOS STOPS
  → iOS jumps to battery service [0x0028, 0x002a]
```

All 6 remaining char decls (A5–A2) have the same 128-bit UUID format → same item_len=21.
BlueZ should pack them all into one 128-byte response, but returns only A5 (23 bytes).

**A5 char decl content is correct:** props=0x10 (NOTIFY), value_handle=0x0019 (correct).
The problem is that BlueZ returns only 1 item instead of all 6 same-size items.

**Mock's `BlueZ registered only 0/7` diagnostic is a false alarm.**
The v1.35.0b1 "GATT readback" code always returns 0 regardless of BlueZ state — it is
a bug in the mock's own diagnostic. The bluetoothd debug log proves all 7 ARE registered.
This dead-code diagnostic should be removed.

**What the btsnoop and debug log confirm:**

| Observation | Implication |
|---|---|
| Vendor service range = 19 handles (0x0015–0x0027) | BlueZ counted all 7 chars from `GetManagedObjects` |
| All 7 `database_add_chrc()` calls succeed (debug log) | All 7 char decls exist in BlueZ GATT DB |
| `Read By Type [0x0018, 0x0027]` → only A5 returned | BlueZ's `gatt_db_read_by_type` returns 1 item instead of 6 |
| PDU=23 bytes < MTU-1 → iOS stops | ATT spec conclusion: no more attrs in range |
| Bug identical before and after `systemctl restart bluetooth` | Not stale state |

**Theories DISPROVED:**

1. ~~`_emit_interface_added` pre-registration race~~ — suppression working (12 signals suppressed)
2. ~~Stale BlueZ watcher entries~~ — `UnregisterApplication` pre-cleanup + restart unchanged
3. ~~Battery service sharing the D-Bus connection~~ — removal has no effect
4. ~~iOS GATT cache~~ — first connection to fresh bluetoothd shows same bug
5. ~~BlueZ doesn't register the chars~~ — debug log proves all 7 ARE registered

**macOS behaves identically to iOS — no continuations from either client.**

Earlier analysis incorrectly stated "macOS works because it uses large MTU and handles
multiple continuation queries." This was wrong. Four test sessions across 2026-06-23
all show the same result: BlueZ returns A5 only, and both iOS and macOS stop after
that single short response — no follow-up range query is issued by either client.

The `gatt-discovery-test.py` "PASS — 7/7" result observed on macOS was entirely from
CoreBluetooth's peripheral cache. The cache is keyed by **peripheral UUID**
(e.g. `4E695123-…`), not by service UUID. Changing the vendor service UUID suffix from
`…0000` to `…0001` moved the service to new ATT handles, but CoreBluetooth returned
the cached 7-characteristic database for that peripheral UUID regardless.

| Session | Service range | ATT live result | PASS source |
|---------|--------------|-----------------|-------------|
| 08:55 — `…0000` | [0x0015, 0x0027] | A5 only → jumps to battery | cache (Alba peripheral) |
| 14:02 — `…0000` | [0x00C3, 0x00D5] | A5 → tries 0x00CF → A1 → stops | cache (partial) |
| 14:18 — `…0001` | [0x00E9, 0x00FB] | A5 only → disconnects | cache (peripheral UUID) |

In all sessions: BlueZ returns A5, client stops, no continuation.

**Root cause confirmed: `gatt-server.c` / `process_read_by_type` packing loop.**

The `gatt-db.c` diagnostic (printf after `gatt_db_foreach_in_range`) was run against a
compiled BlueZ 5.77 on 2026-06-23 and produced:

```
>>> RBT [0015-0027]: 7 attr(s)   ← gatt_db_read_by_type queues ALL 7 correctly
>>> RBT [0021-0027]: 3 attr(s)
>>> RBT [0024-0027]: 2 attr(s)
```

`gatt-db.c` / `foreach_in_range` is correct — all 7 char decls go into the queue.
The bug is in `gatt-server.c` / `process_read_by_type`: it receives a full queue of
7 items but the packing loop stops at the first `item_len` boundary. `0x3A2B` has
`item_len=7`; A5–A2 have `item_len=21`. Only the first size group is packed and sent.

**Why `mock-geberit-alba.py` is unaffected:** `GeberitServiceA` in the alba mock has
exactly two characteristics, both with 128-bit UUIDs (`559eb101-…` and `559eb110-…`),
so all char decls in the service have `item_len=21`. No size boundary exists → BlueZ
packs both into one response without hitting the packing loop's stopping condition.
The mera mock is the only mock that mixes a 16-bit UUID (`0x3A2B`, `item_len=7`)
with 128-bit UUIDs (`item_len=21`) inside the same service.

**Fix applied — `gatt-server.c` `read_by_type_read_complete_cb` (BlueZ 5.77, 2026-06-23):**

When the packing loop hits a mismatched `item_len`, instead of calling `op->done = true`
and sending a short PDU, the patched code calls `process_read_by_type(op)` and returns —
skipping the mismatched item and continuing the scan for same-size items. The skipped item
is found by the client's follow-up RBT with a fresh handle range.

Patch file: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/bluez-5.77/src/shared/gatt-server.c`
(backup at `gatt-server.c.bak`).

**Verified 2026-06-23 — `minimal-peripheral.py` + `minimal-central.py` against macOS (MTU=515):**

```
RBT [0015-0027]: 7 attr(s) queued → 3a2b returned alone (item_len=7, short PDU)
                                   → client continues: next RBT start = 0x0018
RBT [0018-0027]: 6 attr(s) queued → all 6 × 128-bit chars returned (item_len=21)
Result: PASS — 7/7 characteristics discovered ✓
```

The client (CoreBluetooth) received the short first response and issued a follow-up
RBT at 0x0018 (3a2b value_handle 0x0017 + 1) — exactly as the ATT spec requires.
BlueZ returned all 6 remaining same-size items in the second response. This is live
discovery, NOT CoreBluetooth cache (the central was restarted against a fresh peripheral
with correct service UUID `3334429d-…a03e0000`).

**Char ordering note:** `inspect.getmembers(type(self), ...)` sorts alphabetically →
`button_state_read` (b) → `notify_a5/a6/a7/a8` (n) → `write_0/1` (w). This is the
observed char0–char6 order and is correct behaviour, not a bug.

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

## Current status — mock v1.35.0b2 (2026-06-23)

Requires patched `bluetoothd` (BlueZ 5.77 `gatt-server.c` — see 2-char-decl bug section above).

| Feature | Status |
|---------|--------|
| BLE advertising with `IsButtonPressed` toggle | ✅ |
| All 7 char declarations visible to iOS/macOS | ✅ `gatt-server.c` patch applied — 7/7 confirmed on macOS 2026-06-23 |
| A6 InfoFrame burst (Connection 1 trigger) | ⏳ unblocked — pending iOS test with patched bluetoothd |
| No pairing dialog (`btmgmt pairable off` at startup) | ✅ v1.32.0 |
| `IsButtonPressed` latched until burst sent | ✅ v1.28.0 |
| GetDeviceIdentification (proc `0x82`) | ✅ |
| GetFirmwareVersionList (proc `0x0E`) | ✅ |
| GetSystemParameterList (proc `0x0D`) | ✅ |
| GetDeviceInitialOperationDate (proc `0x86`) | ✅ |
| GetFilterStatus (proc `0x59`) | ✅ |
| Web UI button press + live state | ✅ |
| Full Connection 1 → GetDeviceIdentification flow | ⏳ unblocked — pending iOS test with patched bluetoothd |
