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

Single vendor service `3334429d-90f3-4c41-a02d-5cb3a03e0000`, 9 characteristics:

| UUID suffix | Properties | Role |
|-------------|-----------|------|
| `...a13e0000` | WRITE_WITHOUT_RESPONSE | A1 — procedure requests (app → mock, cy[0]) |
| `...a23e0000` | WRITE_WITHOUT_RESPONSE | A2 — write channel cy[1] |
| `...a33e0000` | WRITE_WITHOUT_RESPONSE | A3 — write channel cy[2] |
| `...a43e0000` | WRITE_WITHOUT_RESPONSE | A4 — write channel cy[3] |
| `...a53e0000` | NOTIFY | A5 — primary response channel |
| `...a63e0000` | NOTIFY | A6 — CONS continuation + Connection 1 trigger |
| `...a73e0000` | NOTIFY | A7 — CONS continuation |
| `...a83e0000` | NOTIFY | A8 — CONS continuation |
| `00003a2b-...` | READ | Button-state probe — returns `b"ro"` |

**All four write channels (A1–A4) are required.** The app calls
`GetCharacteristic()` for each and throws "Bulk transfer characteristic missing"
if any returns null — showing "connection could not be established" before writing
any CCCD. Root cause confirmed from `AquaCleanProduct.cs` line 1062.

The real Mera Comfort handle map (from nRF52840 capture) is at
`docs/developer/mera-home-app-onboarding.md`.

---

## Connection 1 flow

The Geberit Home App "Connection 1" onboarding requires **two BLE connections**
(v1.36.0+, see [SC flush](#sc-flush--ios-corebluetooth-cache)):

**BLE Connection 1 — cache update (force-disconnected at 700 ms):**

1. App detects `IsButtonPressed=True` in the BLE advertisement and connects.
2. iOS CoreBluetooth runs ATT characteristic discovery via multiple Read By Type
   passes (GATT §4.6.1 follow-ups). CoreBluetooth updates its peripheral cache
   from any stale 2-char entry (leftover from early mock sessions) to the correct
   9-char result.
3. Mock force-disconnects at 700 ms — ATT discovery finishes in ~500 ms;
   700 ms is enough to update the cache before the app layer acts on the
   (potentially stale) cached list.

**BLE Connection 2 — protocol exchange:**

4. iOS retries automatically with the same RPA (`IsButtonPressed` stays `True`).
5. CoreBluetooth delivers the updated 7-characteristic list to the app delegate.
6. App writes CCCDs on A5, A6, A7, A8 (in order, within ~400 ms).
7. **Mock sends 10× InfoFrame burst on A5** — triggered by A5 CCCD enable. Required by
   the bridge (`wait_for_info_frames_async`, threshold=10 on A5).
8. **Mock sends 9× InfoFrame burst on A6** — triggered once A6 CCCD is set (~200 ms after
   A5 CCCD). Required by iOS: `GeberitDeviceCoreService.Connect()` checks
   `ConnectionState == Ready` after `EstablishAsync()` returns; `ConnectionState` is set
   to `Ready` only when InfoFrames are received on **A6** (not A5).
   Without this burst, `Connect()` returns `TryResult.Fail` → "Fehler" popup.
9. App calls GetDeviceIdentification (proc `0x82`), GetFirmwareVersionList (`0x0E`), and
   the standard polling procedures.

Both bursts fire event-driven: the mock polls CCCD state at 100 ms intervals and sends
each burst the instant BlueZ sets the respective CCCD to `True`. A fixed timer MUST NOT
be used — it fires after iOS has already shown "cannot connect" and disconnected.
The `_a6_burst_done` event keeps A5 responses blocked during both bursts (v1.41.0b1+).

**Stale RPA between Connection 1 and Connection 2 (v1.37.0+):**
After the SC flush, iOS sometimes reconnects briefly with an old RPA (a leftover device
object from a previous session, e.g. `78:42:1C:38:DE:16`). This connection fails
immediately (GATT init fails, bond error `0xe`). BlueZ marks it temporary; its ~20 s
cleanup timer then fires `device_remove()` right in the middle of Connection 2, tearing
down our GATT app registration and sending a Service Changed indication to iOS — which
triggers a full GATT re-discovery, finds nothing, and shows "cannot connect".

`_force_remove_and_reregister` (v1.37.0b1) detects this via the
`_sc_flush_primary_path` guard and immediately calls `Adapter1.RemoveDevice` on the
stale device, pulling the teardown into the safe 18-second window before Connection 2.
Both GATT apps are then re-registered before Connection 2 arrives. See the
[Stale RPA GATT teardown](#stale-rpa-gatt-teardown--v1370) section below.

**Source:** nRF52840 capture of iOS app v2.14.1 against real Mera Comfort
(`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/`).

---

## SC flush — iOS CoreBluetooth cache (v1.36.0+)

iOS CoreBluetooth caches GATT characteristic lists by **peripheral MAC address**
(not by iOS RPA). Early mock sessions (before v1.40.0b1, when the mock only
registered `3a2b` + `A5`) left a stale 2-char cache for our adapter MAC
(`A0:AD:9F:72:C4:0F`). This cache persists across iPad reboots.

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

**Regression and re-fix (2026-07-16, v1.77.0b1):** the RC-pairing-stub commit `2b565b0`
(v2.14.x era) reintroduced `btmgmt pairable on` in `_handle_button()`, scoped to the
web-UI button-press window, specifically so the physical Remote Control accessory could
complete SMP pairing. This silently broke the rule above again — `pairable=on` is
adapter-wide, so it also invited iOS's own system Bluetooth stack to offer pairing with
"ro" (the mock's device name), not just the RC. Confirmed live: iOS showed
"Kopplungsanforderung ... „ro" möchte sich mit deinem iPad koppeln" during a normal Home
App connection attempt. Removed again in v1.77.0b1 (both `tools/mock-geberit-mera.py` and
`aquaclean_ble_relay/mera_mock.py`) — the button press now only updates the advertisement
byte, no `pairable` toggle. Trade-off: RC pairing via this button-press window no longer
completes SMP; the RC pairing GATT service stub (0xC526) still exists and is still
discoverable, just not pairable through this path anymore. If RC pairing testing is
needed again, it needs a way to scope pairing to just the RC's connection, not a blanket
adapter-wide `pairable=on` — don't reintroduce this a third time without solving that.

### Connection-interval request was always dead code — removed 2026-07-17

`_request_short_ci()` tried to request a shorter BLE connection interval (8.75–10ms) from
iOS right after CCCD-A5 subscription, via `org.bluez.Device1.call_update_connection_parameters()`.
Intent: at the default ~30ms connection interval, the largest multi-frame proc response
(`GetDeviceIdentification`, 6 frames) doesn't fully arrive within iOS's ~54ms FlowControl ACK
window, causing a partial ACK and one retransmit round — visible in every mock log as
`FlowControl: bitmask=0x0f (expected ...) — retransmit #1 of frame(s) [...]`. A faster CI
would have delivered all frames in time and avoided that.

**Confirmed 2026-07-17: this call has silently failed on every single connection since it was
written.** `org.bluez.Device1` has never exposed `UpdateConnectionParameters`/`LEConnParamUpdate`
in its documented D-Bus API — checked against BlueZ's own `device-api` docs (only `Connect`,
`Disconnect`, `ConnectProfile`, `DisconnectProfile`, `Pair`, `CancelPairing` exist). The
`try/except` around the call masked an `AttributeError` on every attempt; the mock has always
run at whatever default connection interval BlueZ/iOS negotiate (observed: 30ms, 0 latency,
1000ms supervision timeout — which do satisfy Apple's Bluetooth Accessory Design Guidelines
compliance formulas, for what it's worth).

**Investigated as part of the 2026-07-17 firmware-update-mystery investigation** (see
`docs/developer/firmware-version.md` § "Investigation update") because iOS is separately known
to disconnect BLE peripherals over non-compliant connection parameters — a real, well-documented
class of issue (Apple Developer Forums, multiple hardware-vendor reports). Checked our actual
negotiated values against Apple's published formulas from the Bluetooth Accessory Design
Guidelines (§3.6) — all pass. So this dead code, while real and now removed, was **not** the
cause of the periodic ~35–90s app-initiated disconnects chased that day; that mystery remains
open. The retransmit-then-succeed pattern it would have prevented is cosmetic (single retry,
always resolves) — not shown to cause any actual failure on its own.

**Removed** in `mera_mock.py` v1.87.0b1 rather than fixed, since there's no evidence a working
D-Bus equivalent exists for a BlueZ peripheral to request connection parameters — achieving the
original intent (if ever revisited) would need a different mechanism entirely (e.g. kernel-level
`btmgmt`/debugfs LE connection parameter defaults, not a per-device D-Bus call).

---

## Known issues

### 2-char-decl investigation — gatt-server.c patch NOT required (2026-06-25)

**CONFIRMED 2026-06-25:** Geberit Home App v2.14.1 works against mock v1.63.0b1 with
**original (unpatched) BlueZ 5.77 `bluetoothd`**. No `gatt-server.c` patch needed.
See correction note at the end of this section for what the investigation actually found.

**Original symptom (before v1.40.0b1):** GATT discovery found only 2 characteristic
declarations (3a2b + A5). This was because early mock versions registered only those
2 chars (service end handle 0x0019). iOS correctly stopped after A5 — there were no
more chars in the service range to discover.

**Confirmed GATT handle layout** (current — 9 characteristics as of v1.40.0b1; 7-char layout was confirmed from `bluetoothd -d` debug log, 2026-06-22 21:17):

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
| 0x0028 | A3 char decl | 128-bit |
| 0x0029 | A3 value | WRITE\_WITHOUT\_RESPONSE |
| 0x002a | A4 char decl | 128-bit |
| 0x002b | A4 value | WRITE\_WITHOUT\_RESPONSE |

All 9 chars + 4 CCCDs = 23 handles. **BlueZ has all 9 char decls at the
correct handles.** Proven by `database_add_chrc()` firing 9 times with correct handles.

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
| Vendor service range = 23 handles (0x0015–0x002b) | BlueZ counted all 9 chars from `GetManagedObjects` |
| All 9 `database_add_chrc()` calls succeed (debug log) | All 9 char decls exist in BlueZ GATT DB |
| `Read By Type [0x0018, 0x0027]` → only A5 returned | BlueZ's `gatt_db_read_by_type` returns 1 item instead of 6 |
| PDU=23 bytes < MTU-1 → iOS stops | ATT spec conclusion: no more attrs in range |
| Bug identical before and after `systemctl restart bluetooth` | Not stale state |

**Theories DISPROVED:**

1. ~~`_emit_interface_added` pre-registration race~~ — suppression working (12 signals suppressed)
2. ~~Stale BlueZ watcher entries~~ — `UnregisterApplication` pre-cleanup + restart unchanged
3. ~~Battery service sharing the D-Bus connection~~ — removal has no effect
4. ~~iOS GATT cache~~ — first connection to fresh bluetoothd shows same bug
5. ~~BlueZ doesn't register the chars~~ — debug log proves all 9 ARE registered

**`gatt-db.c` diagnostic** (`printf` after `gatt_db_foreach_in_range`, BlueZ 5.77, 2026-06-23):

```
>>> RBT [0015-0027]: 7 attr(s)   ← gatt_db_read_by_type queues ALL 7 correctly
>>> RBT [0021-0027]: 3 attr(s)
>>> RBT [0024-0027]: 2 attr(s)
```

`gatt-db.c` / `foreach_in_range` is correct — all 7 char decls go into the queue.

`gatt-server.c` / `process_read_by_type` stops packing at the first `item_len` boundary
(`0x3A2B` has `item_len=7`; A5–A2 have `item_len=21` — only the first size group is packed
per response). The client issues follow-up RBTs to find the rest.

**Char ordering note:** `inspect.getmembers(type(self), ...)` sorts alphabetically →
`button_state_read` (b) → `notify_a5/a6/a7/a8` (n) → `write_0/1` (w). This is the
observed char0–char6 order and is correct behaviour, not a bug.

---

**CORRECTION (2026-06-25) — original bluetoothd is correct; patch NOT required:**

The `process_read_by_type` stop-at-mismatch behavior is **spec-correct** per ATT §3.4.4.2.
iOS CoreBluetooth implements GATT §4.6.1 and always issues follow-up RBTs after receiving a
response shorter than MTU-1 — it always finds all 9 characteristics without any patch.

With mock v1.63.0b1 (9 chars, service range 0x0015–0x002b), the full RBT sequence on
original bluetoothd is:

```
RBT [0015-002b]: 3a2b alone (item_len=7, 1 item)     → client follow-up at 0x0018
RBT [0018-002b]: A5–A2 packed (6 × item_len=21)      → client follow-up at 0x0028
RBT [0028-002b]: A3+A4 packed (2 × item_len=21)      → discovery complete
All 9 chars found. ✓
```

The early 2-char stale CoreBluetooth cache came from sessions before v1.40.0b1, when the
mock only registered `3a2b` + `A5`. iOS cached those 2 chars; the cache persisted across
sessions. The SC flush (BLE Connection 1) is still needed to update this stale cache.

**The gatt-server.c "skip-and-continue" patch** (at
`local-assets/…/bluez-5.77/src/shared/gatt-server.c`, backup `gatt-server.c.bak`)
is NOT needed and carries a regression risk: if the short-UUID char decl falls between
two same-length chars in handle order, the client's follow-up jumps past the middle
short char permanently. **Do not apply or submit this patch.**

The `minimal-peripheral.py` / `minimal-central.py` test scripts in `tools/` show PASS
with both original and patched BlueZ because `char_short` sorts alphabetically before
`notify_*` → gets the lowest handle → first in queue → both versions return identical
first responses. The scripts do not demonstrate a behavioural difference.

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

### Stale RPA GATT teardown — v1.37.0+

**Symptom:** After the SC flush, Connection 2 connects successfully and iOS begins GATT
service discovery. Approximately 20 seconds after Connection 2 starts, both GATT app
registrations are torn down: BlueZ sends Service Changed indications to iOS, iOS
re-discovers services, finds nothing, shows "cannot connect." The `bluetoothd -d` log
shows (in sequence):

```
device_remove()     Removing device /org/bluez/hci0/dev_78_42_1C_38_DE_16
btd_device_unref()  Freeing device
device_free()       0x…
proxy_removed_cb()  Proxy removed - removing service: /org/bluez/example/mera/service0
gatt_db_service_removed()  Local GATT service removed
send_notification_to_device()  GATT server sending indication    ← SC to iOS
client_disconnect_cb()  Client disconnected
proxy_removed_cb()  Proxy removed - removing service: /org/bluez/example/battery/service0
… (same for battery) …
src/advertising.c:client_disconnect_cb()  Client disconnected
service_changed_conf()   ← iOS acknowledged SC
service_changed_conf()
```

**Root cause — BlueZ stale device cleanup timer:**

When `78:42:1C` (an old iOS RPA from a pre-SC-flush session) connects briefly after the
SC flush and immediately disconnects, BlueZ marks it a "temporary" (non-bonded, no
stored keys) device and starts a ~20 second cleanup timer. When the timer fires,
`btd_adapter_remove_device()` → `device_remove()` → `device_free()` is called.

This triggers `service_disconnect` in BlueZ's GDBusClient for our mock's D-Bus name.
`service_disconnect` walks the client's proxy list and calls `proxy_removed_cb` for each
registered proxy. For the mera and battery service proxies, `proxy_removed_cb` calls
`service_free()` → `gatt_db_remove_service()` → `gatt_db_service_removed()` →
`send_notification_to_device()`. Since iOS (`5E:F9`) is actively connected and
subscribed to Service Changed at this point, BlueZ sends a SC indication. iOS
re-discovers GATT, finds no Geberit services, and shows "cannot connect".

**Investigation artifacts:**
- `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/minimal-peripheral_bluetoothd-debug_2026-06-23_19-56.log` lines 758–783 — definitive capture
- Confirmed in 18-51 log (lines 543–574) and 18-32 log (lines 601–622) — same mechanism across all runs
- BlueZ source traced: `gdbus/client.c` `service_disconnect()` (line 1294) → `g_list_free_full(proxy_list, proxy_free)` → `proxy_free` (line 554) → `proxy_removed_cb` — `app_free()` clears callbacks BEFORE `g_dbus_client_unref`, so the call chain is confirmed via `service_disconnect`, not `g_dbus_client_unref`
- `interfaces_removed` watch (line 1385 `gdbus/client.c`) only fires for signals FROM our mock's D-Bus name — not from BlueZ itself

**Fix (v1.37.0b1):**

`_sc_flush_primary_path` is set at the start of `_sc_flush()` to the Connection 1
device path (e.g. `…/dev_5E_F9_F9_11_DA_81`). In `_on_device_disconnected`, if
`_sc_flush_done=True`, `_button_pressed=True`, and the disconnecting device path is
NOT `_sc_flush_primary_path`, the disconnecting device is a stale interloper. The mock
immediately calls `Adapter1.RemoveDevice` (pure D-Bus, no subprocess), which triggers
the GATT teardown at this safe moment — iOS is not yet connected for Connection 2, so
the Service Changed indication is sent to no one. After 500 ms (to let BlueZ settle),
both GATT apps are re-registered via `GattManager1.RegisterApplication`. The D-Bus
object exports are still live (no re-export needed); BlueZ's new GDBusClient calls
`GetManagedObjects` and finds all 7 characteristics. Connection 2 arrives ~18 seconds
later to a clean registration.

**Why `_sc_flush_primary_path` rather than just `_sc_flush_done`:**
The SC flush itself disconnects `5E:F9` (the primary device). At that moment
`_sc_flush_done` is set to `True` — so the primary `5E:F9` disconnecting after SC flush
would also match a naive `_sc_flush_done` check. The primary path guard ensures only
genuinely foreign devices (old RPAs) trigger the force-remove.

**Advertising note:** The same teardown mechanism also fires `src/advertising.c:client_disconnect_cb()`. Advertising re-registration is not performed by `_force_remove_and_reregister` (not needed for Connection 2, which reuses an existing BLE connection).

---

### Missing write channels A3/A4 — fixed in v1.40.0b1

**Symptom (v1.39.0b1 and earlier):** The `bluetoothd -d` log shows GATT discovery
completing and both CCC writes but the Geberit Home App shows "connection could not be
established" immediately — zero ATT reads or CCCD writes to any Geberit characteristic.

**Root cause:** The Geberit Home App's `AquaCleanProduct.cs` (line 1062) checks all four
write channels immediately after GATT discovery:

```
cy[0] = service.GetCharacteristic("...a13e0000");  // A1 ✓ mock had
cy[1] = service.GetCharacteristic("...a23e0000");  // A2 ✓ mock had
cy[2] = service.GetCharacteristic("...a33e0000");  // A3 ✗ missing
cy[3] = service.GetCharacteristic("...a43e0000");  // A4 ✗ missing
if (... || cy[2] == null || cy[3] == null)
    throw new Exception("Bulk transfer characteristic missing");
```

`cy[2]/cy[3]` were null → app threw immediately → "connection could not be established"
— before writing a single CCCD.

**Fix (v1.40.0b1):** `mock-geberit-mera.py` adds `write_2` (A3) and `write_3` (A4) as
`WRITE_WITHOUT_RESPONSE` characteristics. All four write channels dispatch to
`_handle_request` identically.

---

### FlowControlFrame misidentified as CONS — fixed in v1.41.0b1

**Background — Geberit frame type encoding:**

Bits [7:5] of the header byte encode the frame type
(see `FrameFactory.getFrameTypeFromHeaderByte()` in the bridge):

| Bits [7:5] | FrameType | Header range |
|---|---|---|
| 0 | SINGLE | 0x00–0x1F |
| 1 | FIRST | 0x20–0x3F |
| 2 | CONS | 0x40–0x5F |
| 3 | CONTROL | 0x60–0x7F |
| 4 | INFO | 0x80–0x9F |

**FlowControlFrame wire format** (`FlowControlFrame.create_flow_control_frame(data)`):

| Offset | Field |
|---|---|
| 0 | Header byte (0x60–0x7F; FrameType.CONTROL) |
| 1 | ErrorCode |
| 2 | UnackdFrameLimit (= 8) |
| 3 | TransactionLatency |
| 4–11 | AckdFrameBitmask (8 bytes; bit N = 1 means frame N was received) |

**Symptom (v1.40.0b1):** After sending a multi-frame A5 response (FIRST + 3 × CONS for
GetDeviceIdentification), the app sends a FlowControlFrame on A1 acknowledging which
frames it received. A FlowControlFrame has header `0x70` (CONTROL type, bits[7:5]=3).
The old check `hdr & 0x01` (bit 0=0) silently discarded the frame. The app expected
retransmission of the missing frame and retried GetDeviceIdentification three times,
then showed "connection could not be established."

**Root cause of frame loss:** The A6 InfoFrame burst (9 frames × 50 ms = 450 ms window)
was running concurrently with the 4-frame A5 response. iOS CoreBluetooth dropped the
last CONS frame (CONS[2]) due to ATT pipeline congestion. The app sent FlowControlFrame
with `AckdFrameBitmask[0] = 0x07` (frames 0–2 received; frame 3 missing).

**Fix (v1.41.0b1) — two changes:**

1. **Frame type dispatch** — use `FrameFactory.getFrameTypeFromHeaderByte(hdr)` (imported
   from the bridge — no code copied) instead of the bit 0 check. CONTROL → FlowControl
   handler; parse `AckdFrameBitmask`, identify missing frames by index, retransmit them
   from `_last_a5_frames`.

2. **A6 burst serialization** — `_a6_burst_done` asyncio.Event is cleared before the
   9-frame burst and set after. `_handle_request` awaits it (3 s timeout) before sending
   any A5 frames, preventing the ATT congestion that caused the frame loss.

**Bridge imports used (DRY — not copied, imported directly):**
```python
from aquaclean_console_app.aquaclean_core.Frames.FrameFactory              import FrameFactory     as _FrameFactory
from aquaclean_console_app.aquaclean_core.Frames.Frames.FrameType          import FrameType        as _FrameType
from aquaclean_console_app.aquaclean_core.Frames.Frames.FlowControlFrame   import FlowControlFrame as _FlowControlFrame
```

---

### App slow on mock — ~60 s Remote Control delay (infrastructure limitation)

**Symptom:** Opening "Remote Control" in Geberit Home App v2.14.1 against the mock takes
~60 seconds. Against a real device the same screen opens instantly (< 1 s).

**Confirmed timing** (mock log `mock-geberit-mera_2026-06-25_07-22.log`, v1.57.0b1):

| Time | Event |
|------|-------|
| 07:23:57 | GetStoredProfileSetting ×20 begins (proc 0x53, settings 0–14 × repeat) |
| 07:24:36 | GetStoredProfileSetting sequence completes (~39 s) |
| 07:24:36 | GetPerNodeProfileSetting ×11 (proc 0x07) and SetActiveProfileSetting ×7+ (proc 0x08) interleaved |
| 07:24:59 | User taps "Remote Control" in app |
| 07:25:59 | Remote Control screen appears (~60 s after first GetStoredProfileSetting) |

All of the above interleaved with continuous `GetSystemParameterList` (proc 0x0D) +
`GetFilterStatus` (proc 0x59) polls every ~2 s.

**Root cause — BLE round-trip latency:**

| Environment | Per-request latency | 60-request sequence |
|---|---|---|
| Real device (hardware BLE) | ~100 ms | ~6 s (imperceptible) |
| Mock (UTM VM + USB-BT500 + BlueZ) | ~1,000 ms | ~60 s |

The ~1 s per-request latency on the mock is due to the USB-BT500 adapter inside a UTM
virtual machine. Every ATT write → notify round-trip crosses: USB host → UTM VM → BlueZ
userspace → HCI → USB → Bluetooth radio → iOS → response over air → USB → BlueZ → VM.
Each hop adds latency; the aggregate is ~10× slower than hardware.

**Not fixable from the protocol side.** The app issues the same requests against both
targets; the delay is purely a function of infrastructure latency. Accepted limitation of
mock testing on UTM/USB.

---

### "Error" popup after first FilterStatus poll — fixed in v1.61.0b1

**Symptom:** Geberit Home App shows "Fehler / Ein Fehler ist aufgetreten" popup ~1 s after
the first complete `GetFilterStatus` (proc 0x59) response on Connection 2 (the Save flow
reconnect). Appeared consistently from v1.54.0b1.

**Timing** (log `mock-geberit-mera_2026-06-25_08-46.log`):

```
08:47:43  proc 0x59 GetFilterStatus         → ok, 4 frames ACKed
08:47:44  "Fehler / Ein Fehler ist aufgetreten" shown in app
```

**Root cause A — InfoFrame burst sent on A5 instead of A6 (primary, fixed v1.61.0b1):**

`GeberitDeviceCoreService.Connect()` checks `ConnectionState == Ready` after
`EstablishAsync()` returns (line 175 in decompiled source). `ConnectionState` is set to
`Ready` only when InfoFrames are received on **A6** — not A5. The mock was sending the
burst on A5 only (since v1.41.0b1). The procs (0x82, 0x0E, 0x0D, 0x59) all succeed
because they are independent of `ConnectionState`. But `Connect()` finds
`ConnectionState != Ready` → returns `TryResult.Fail` → error popup fires.

Confirmed from `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-2.md`: InfoFrame
burst fires on A6 after CCCD-A7 enable (lines 60–65, t=69.1 s). No error occurs on real
device. The 0.6 s gap after GetFilterStatus (lines 440–444) contains NO spontaneous notifies.

**Fix (v1.61.0b1):** `_send_info_frame_burst()` (renamed from `_send_a5_info_frames`)
sends 10× on A5 first (bridge compatibility), then waits for CCCD-A6 and sends 9× on A6.

**Root cause B — FilterStatus id=4/id=8 zero (partial fix v1.60.0b1):**

After fixing id=3 and id=6 in v1.59.0b1, the remaining differences were id=4 and id=8:

| id | Real device | Mock (v1.59.0b1) | Meaning |
|---|---|---|---|
| 0 | 1 | 1 | ✓ |
| 1 | 130 | 130 | ✓ |
| 2 | 14 | 14 | ✓ |
| 3 | 1 | 1 | ✓ (fixed v1.59.0b1) |
| **4** | **0x69e8e6d4** (~March 2026) | **0** | **TimestampAtLastFilterChange** |
| 5 | 0 | 0 | ✓ |
| 6 | 3 | 3 | ✓ (fixed v1.59.0b1) |
| 7 | 348 | 348 | DaysUntilNextFilterChange ✓ |
| **8** | **0x6a218efe** (~May 2026) | **0** | **TimestampAtLastFilterChangePrompt** |
| 9 | 0 | 0 | ✓ |
| 10 | 5 | 5 | ✓ |

`id=10=5` (5 filter changes) and `id=7=348` (days remaining) indicate the filter has
been replaced before, but `id=4=0` (epoch = "never changed") contradicts this. May
contribute to the error but is NOT the primary cause — the A6 burst was the missing piece.

**Fix (v1.60.0b1):** `_proc_59()` sets id=4 and id=8 to `int(time.time()) - 17*24*3600`.

**Both fixes combined in v1.61.0b1.** Needs test confirmation.

---

### BlueZ SMP bonding failure — 29 s hang on first two connections

**Symptom:** Connections 1 and 2 each hang ~29 s before proceeding, then disconnect with
`device_bonding_failed() status 14` ("Repeated Attempts"). Connection 3 always succeeds.
Pre-existing since v1.54.0b1.

**Root cause:** BlueZ SMP state machine. After Connection 1 fails SMP pairing and records
the failure, Connection 2 immediately from the same iOS device triggers the SMP
"Repeated Attempts" timer (status 0x0E = 14). BlueZ waits the full timer (~29 s) before
permitting a retry.

**Not a protocol issue.** The mock BLE link is unencrypted; SMP pairing is not required.
The hang is an artefact of BlueZ's SMP rate-limiting triggered by iOS attempting
pairing on each connection. Connection 3 succeeds because the timer has expired.

**Impact:** Each test session takes ~60 s longer than on real hardware. Workaround:
ensure `btmgmt pairable off` is in effect (mock sets this at startup, v1.32.0+).

---

### "Descaling necessary" warning — fixed in v1.59.0b1

**Symptom:** Geberit Home App showed "descaling necessary" warning banner after onboarding
against the mock. Present from v1.54.0b1; confirmed fixed in v1.59.0b1 (2026-06-25).

**Root cause:** SPL index 13 (`DaysUntilNextDescale`) was 0 in mock responses. iOS
requests `[13, 12, 0..7]` during first-time onboarding (Connection 1). Index 13 = 0
is interpreted as "0 days remaining" → descaling overdue warning.

**Fix (v1.59.0b1):** Added index 13 to `_SPL_MERA_INDICES` with value 69
(`_SPL_MERA_VALUES[13] = 69`). Confirmed by user: "descaling warnings are gone" (2026-06-25).

**Investigation history:**
- v1.57.0b1 added indices 12+13 but incorrect — did not fix (user confirmed).
- v1.58.0b1 reverted (wrong diagnosis — root cause was index 13=0, not the index list).
- v1.59.0b1 re-added index 13=69 correctly — **confirmed fixed**.

**`_proc_45()` annual cycle mismatch (low priority):** Returns `last_descale = 21 days ago` +
`days_until_next = 69` = 90-day cycle. Real device is annual (365-day):
`last_descale_elapsed + DaysUntilNextDescale = 365`. Not called during polling; does
not affect the warning.

---

### FilterStatus vs. descaling — two separate maintenance systems

| System | BLE source | Key field |
|--------|-----------|-----------|
| Descaling (water heater, citric acid) | SPL proc 0x0D index 13; proc 0x45 history | Index 13 = DaysUntilNextDescale; proc 0x45 = 16-byte history struct |
| Ceramic honeycomb filter (annual replacement) | proc 0x59 GetFilterStatus | id=7 = DaysUntilNextFilterChange |

Both are annual (365-day) cycles. `id=7` in `GetFilterStatus` is the **ceramic filter**,
not descaling. Real device: id=7=348 (filter changed 2026-06-04, 17 days elapsed at
time of capture). Mock id=7=348, id=4/id=8 = dynamic timestamps 17 days ago — consistent.

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

## Complete procedure response values — v1.64.0b1

Download current mock:

```bash
curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/9dd3b2f0a01d1c4e2c856cc6dc1ba75290a9447c/tools/mock-geberit-mera.py -o tools/mock-geberit-mera.py
```

### Device identity constants — how the iOS app interprets them

The app interprets the proc `0x82` payload in two ways that directly affect mock behavior:

**1. First character of SerialNumber → device variant (article `146.21` only)**

`AcDeviceTypeHelper.GetDeviceType(articleNumber, serialNumber)` checks `serialNumber[0]`:
- `'H'` → `AcMeraComfort` (DeviceVariant used for cloud firmware lookup + ProductIdentifier)
- `'G'` → `AcMeraClassic`

The mock SAP `HB2304EU298414` starts with `'H'` → correctly identified as `AcMeraComfort`. ✅

**2. Full SerialNumber string → CRC32 → `ProductIdentifier.UniqueId`**

```csharp
// AquaCleanProduct.c()
uint value = new Crc32(Crc32Algorithm.Standard).Calculate(Encoding.ASCII.GetBytes(serialNumber));
return new ProductIdentifier(series=248, variant, deviceNumber=0, uniqueId=value);
```

The full SAP string is CRC32'd (standard, ASCII) → `UniqueId`. The `ProductIdentifier`
(`{Series:X2}{Variant:X2}-0000000[{CRC32(SAP):X8}]`) is the app's **per-device local storage key**:
onboarding state, connection history, and firmware update flow are all indexed by it.

**Consequences for the mock:**

| SAP | CRC32 | App sees |
|-----|-------|---------|
| `HB2304EU298413` (real device) | some uint A | Known device → reconnect path |
| `HB2300EU000001` (mock) | some uint B ≠ A | Unknown device → first-time pairing path |

The mock uses a fictional SAP to avoid conflicts with any real device in range. The tradeoff:
the mock always takes the first-time-pairing path, which is why proc `0x0E` must return
RS30.0 TS206 for ALL components to avoid the blocking firmware update screen.
See § GetFirmwareVersionList below.

### GetDeviceIdentification (proc `0x82`) — 82 bytes

| Field | Value |
|---|---|
| ArticleNumber | `146.21x.xx.1` |
| SerialNumber (SAP) | `HB2300EU000001` |
| ProductionDate | `11.04.2023` |
| Description | `AquaClean Mera Comfort` |

### GetNodeList (proc `0x05`) — 129 bytes

Node IDs: `[03, 04, 05, 06, 07, 08, 09, 0A, 0B, 0C, 0E, 0F]` (12 nodes)

### GetSOCApplicationVersions (proc `0x81`)

`"10"` + `0x12` + `0x00` → version `10.18`

### GetFirmwareVersionList (proc `0x0E`) — per requested component

All components: version `"30"`, build `206` → `RS30.0 TS206`

**ALL components MUST return RS30.0 TS206 — including sub-nodes 3–15.**
Setting only component 1 to RS30.0 while sub-nodes return real per-device versions
(RS07–RS11) still triggers the blocking firmware update UI. `FirmwareForceUpdateViewModel`
performs a per-node update check against the local bundled Ble2V1 package; any sub-node
below its target version makes `GetActiveUpdateAsync()` return non-null → blocking screen.

With all components at RS30.0: no per-node delta → null → dismissible "Fehler" popup only
→ mock is fully operational.

The real Mera HB2304EU298413 sends component 1 = RS28.0 TS199 (`32 38 c7`) and the same
real sub-node versions, yet does NOT trigger the blocking screen. This discrepancy is
unexplained — see `docs/developer/firmware-version.md` § "iOS app — firmware update check
mechanism" and `local-assets/geberit-home-v2.14.1-from-iOS/firmware-update-check-analysis.md`
§ "v1.75.0b1 empirical finding" for the full analysis.

### GetDeviceInitialOperationDate (proc `0x86`)

`2023-01-01`

### SubscribeNotif `0x11` — per requested node

12-byte ASCII: `818.802.00.0` (same for all nodes)

### SubscribeNotif `0x13` — per requested node

12 zero bytes per node, except node `0x05`: byte[6] = `0x04`

### GetPerNodeProfileSetting (proc `0x07`) — per node

| Node | Value |
|---|---|
| `0x00` | 1 |
| `0x01` | 1 |
| `0x02` | 4 |
| `0x03` | 1 |
| `0x04` | 2 |
| `0x05` | 1 |
| `0x06` | 4 |
| `0x07` | 0 |
| `0x08` | 3 |
| `0x09` | 1 |
| `0x0D` | 1 |
| any other | 0 |

### GetActiveProfileSetting (proc `0x0A`) and GetStoredProfileSetting (proc `0x53`) — per setting ID

Both procs return identical values.

| ID | Name | Value |
|---|---|---|
| 0 | OdourExtraction | 1 |
| 1 | OscillatorState | 3 |
| 2 | AnalShowerPressure | 2 |
| 3 | LadyShowerPressure | 2 |
| 4 | AnalShowerPosition | 2 |
| 5 | LadyShowerPosition | 0 |
| 6 | WaterTemperature | 1 |
| 7 | WcSeatHeat | 1 |
| 8 | DryerTemperature | 0 |
| 9 | DryerState | 0 |
| any other | 0 |

### GetStoredCommonSetting (proc `0x51`) — per setting ID

| ID | Name | Value |
|---|---|---|
| 0 | WaterHardness | 1 |
| 1 | OrientationLightBrightness | 3 |
| 2 | OrientationLightColour | 2 |
| 3 | OrientationLightMode | 2 |
| 4 | LidSensorRange | 2 |
| 5 | OdourExtractionRunOn | 0 |
| 6 | LidAutoOpen | 1 |
| 7 | LidAutoClose | 1 |
| 8 | AutoFlush | 0 |
| 9 | DemoMode | 0 |
| any other | 0 |

### GetSystemParameterList (proc `0x0D`) — 9 indices

| Index | Name | Value |
|---|---|---|
| 0 | StateUserPresent | 0 |
| 1 | StateShowerAnal | 0 |
| 2 | StateShowerLady | 0 |
| 3 | StateDryer | 0 |
| 4 | StateDescaling | 0 |
| 5 | DurationDescaling | 0 |
| 6 | LastError | 0 |
| 7 | StateService | 0 |
| 11 | EndiannessCheck | 0 |

### GetFilterStatus (proc `0x59`) — 11 items

| ID | Value |
|---|---|
| 0 | 1 |
| 1 | 130 |
| 2 | 14 |
| 3 | 1 |
| 4 | `now − 17 days` (Unix timestamp) |
| 5 | 0 |
| 6 | 3 |
| 7 | 348 |
| 8 | `now − 17 days` (Unix timestamp) |
| 9 | 0 |
| 10 | 5 |

### GetStatisticsDescale (proc `0x45`) — 16 bytes

| Field | Value |
|---|---|
| unposted_shower_cycles | 12 |
| days_until_next_descale | 69 |
| days_until_shower_restricted | 76 |
| shower_cycles_until_confirmation | 20 |
| date_time_at_last_descale | `now − 21 days` (Unix timestamp) |
| date_time_at_last_descale_prompt | `now − 21 days` (Unix timestamp) |
| number_of_descale_cycles | 3 |

### GetDeviceRegistrationLevel (proc `0x55`)

`0` (not registered)

### Procs returning empty ACK

`0x09` SetCommand, `0x0B` SetActiveProfileSetting, `0x54` SetStoredProfileSetting,
`0x08` / `0x14` / `0x15` SetStored*

### GATT notifications (unsolicited)

**A6 InfoFrame burst** — 9 frames, fired on CCCD-A6 enable:
`80 01 30 14 0c 03 00 03 00 00 00 00 31 30 00 12 00 b7 08 00`

**A5 InfoFrame burst** — 10 frames, fired on CCCD-A5 enable: same 20-byte payload

---

## Current status — mock v1.64.0b1 (2026-06-25)

Works with **original (unpatched) bluetoothd** (BlueZ 5.77) — `gatt-server.c` patch is **NOT required** (confirmed 2026-06-25).

**v1.54.0b1 — first confirmed iOS onboarding (2026-06-24).** Full Connection 1 + Connection 2
flow confirmed working with Geberit Home App v2.14.1 on real iPhone.

| Feature | Status |
|---------|--------|
| BLE advertising with `IsButtonPressed` toggle | ✅ |
| All 9 char declarations visible to iOS/macOS | ✅ original bluetoothd — confirmed with Geberit Home App 2026-06-25 |
| SC flush (iOS CoreBluetooth cache update) | ✅ v1.36.0b1 — confirmed working (mock log 2026-06-23 19-56) |
| Stale RPA force-remove + GATT re-register | ✅ v1.37.0b1 — prevents GATT teardown during Connection 2 |
| All four write channels A1–A4 present | ✅ v1.40.0b1 — cy[2]/cy[3] null-check passes |
| FlowControlFrame dispatch + A5 retransmit | ✅ v1.41.0b1 — CONTROL frames parsed, missing frames retransmitted |
| A6 burst serialized before A5 response | ✅ v1.41.0b1 — `_a6_burst_done` event prevents ATT congestion |
| A5+A6 InfoFrame burst (bridge + iOS ConnectionState.Ready) | ✅ v1.61.0b1 — A5 burst for bridge; A6 burst for iOS ConnectionState=Ready |
| No pairing dialog (`btmgmt pairable off` at startup) | ✅ v1.32.0 |
| `IsButtonPressed` latched until burst sent | ✅ v1.28.0 |
| GetDeviceIdentification (proc `0x82`) | ✅ v1.54.0b1 — confirmed |
| GetFirmwareVersionList (proc `0x0E`) | ✅ v1.54.0b1 — confirmed |
| GetSystemParameterList (proc `0x0D`) | ✅ v1.55.0b1 — format fixed (index bytes per item, 9 Mera Comfort items) |
| GetDeviceInitialOperationDate (proc `0x86`) | ✅ v1.54.0b1 — confirmed |
| GetFilterStatus (proc `0x59`) | ✅ v1.60.0b1 — id=4/id=8 set to dynamic Unix timestamps (17 days ago); id=3=1, id=6=3, id=7=348, id=10=5 |
| SubscribeNotif 0x11/0x13 — correct node IDs | ✅ v1.55.0b1 — uses requested node IDs from args; 0x11 with firmware version string |
| GetStatisticsDescale (proc `0x45`) | ✅ v1.56.0b1 — 16-byte struct; called only from descaling history screen (never during polling) |
| Web UI button press + live state | ✅ |
| Full Connection 1 → GetDeviceIdentification flow | ✅ v1.54.0b1 — confirmed iOS onboarding 2026-06-24 |
| "Error" popup after first FilterStatus | ⚠️ still occurring as of v1.64.0b1 — root cause unknown; investigation deferred |
| GetActiveProfileSetting (proc `0x0A`) | ✅ v1.63.0b1 — per-ID values from real device capture (WaterHardness crash fix) |
| GetStoredCommonSetting (proc `0x51`) | ✅ v1.63.0b1 — per-ID values from real device capture; WaterHardness(0)=1 (was 0, caused crash) |
| GetStoredProfileSetting (proc `0x53`) | ✅ v1.64.0b1 — per-ID values from real device capture (was returning 0 for all IDs) |
| GetPerNodeProfileSetting (proc `0x07`) | ✅ v1.64.0b1 — per-node values from real device capture (was returning 0 for all nodes) |
| "Descaling necessary" warning | ✅ v1.59.0b1 — confirmed fixed (2026-06-25); root cause was SPL index 13=0 |

---

### SPL and GetFilterStatus format — fixed in v1.55.0b1

**Symptom (v1.54.0b1):** iOS remote-control screen blocked with "running descaling" message after
successful onboarding. Also possible "Save" error on first device registration.

**Root cause:** Two response format bugs:

1. **`GetSystemParameterList` (proc `0x0D`) — missing index bytes.** Mock sent
   `count(1) + count×value_le(4)`. Real Mera Comfort sends `count(1) + count×(index(1)+value_le(4))`.
   iOS maps each value by its index field, not by position. Without index bytes, all 12 items were
   interpreted as index=0 (StateUserPresent). StateDescaling (index 4) was never updated → iOS
   retained a stale or default non-zero descaling state → remote control blocked.

2. **`GetFilterStatus` (proc `0x59`) — wrong format.** Mock returned `bytes(10)` (count=0, no items).
   Real device returns 11 items in `count(1) + count×(id(1)+value_le(4))` format.

**`descaling_state = 0` is correct for idle device.** Confirmed from
`docs/developer/descaling-protocol.md`: state 0 = idle, state 1–3 = active descaling cycle.
The `0=Error` entry in `docs/developer/mera-comfort-alba-mapping.md` applies to **Alba DpId 585**
enum (`DESCALING_STATUS`) — a different encoding from Mera Comfort's raw `uint32` SPL parameter.

**Real device SPL response** (from `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-2.md`):
9 items for a 12-index request — skips indices 8/9/10 (dangerous on Mera Comfort; permanently
corrupts `GetFilterStatus` until power-cycle). Returns indices `[0,1,2,3,4,5,6,7,11]`, all values 0
when idle.

**Fix:**
- `_proc_0d`: returns `_SPL_MERA_INDICES = [0,1,2,3,4,5,6,7,11]` with proper `(index+value)` format
- `_proc_59`: new function returning 11 items; id=7 (`DaysUntilNextFilterChange`) = 365
- `_proc_subscribenotif`: new function; parses requested node IDs from args; `0x11` returns
  12-byte ASCII firmware version `"818.802.00.0"` per node; `0x13` returns 12 zero bytes
  (node 5: byte[6]=0x04 from real device capture)
