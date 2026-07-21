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

**Button-press/release timing — mock vs. real device (2026-07-18)**

On a real Mera Comfort, the advertisement's `IsEmergencyConnectPermitted` flag (company ID
`0x0100` -> `0x01AA`) tracks the *physical button's actual held state* — confirmed live via
nRF Connect: present only while the button is physically pressed, gone the instant it's
released, independent of any BLE connection. See `docs/developer/mera-home-app-onboarding.md`
"BLE Advertising payload" for the full byte-level evidence.

**Reverted the same day**: the mock briefly implemented this company-ID flip (alongside
splitting the advertisement into two Manufacturer Specific Data entries) — both were reverted
together after onboarding failed completely with them in place (see the revert note in
`mera-home-app-onboarding.md`). `company` is currently always `0x0100`; only `state_b` flips.
The code example below predates the revert and shows the pre-revert intent, kept for context
on the release-timing mechanism itself (which is unaffected by the revert):

The mock has no physical button, so "release" can't be a hardware signal. Instead,
`_send_info_frame_burst()` (step 8 above) auto-releases it:
```python
if self._button_pressed:
    self._button_pressed = False
    await self._update_advert(0)      # state_b -> 0 (company stays 0x0100, pre-revert intent shown)
```
This fires right after the A6 InfoFrame burst completes — i.e. *after* the app has already
connected via BLE, once the A6 CCCD is confirmed ready (or after a 3 s timeout). So the
mock's press→release cycle is triggered by BLE-connection progress, not by a webui
button-release click or any timer tied to user action. Before 2026-07-18 this flip was only
partially real: `_update_advert(0)` correctly reset `state_b`, but the advertisement's
company ID was *always* `0x0100` regardless of `state_b` — the mock never actually sent
`0x01AA` in the first place, so there was nothing genuine to "release" on the company-ID
side. Fixed the same day alongside the ADV_IND/SCAN_RSP split — then both reverted together
the same day when that combination broke onboarding (see above).

**Re-implemented in isolation, 2026-07-20, v1.103.0b1 — and it worked, with two new side
effects.** This time only the company-ID key changed (single-entry dict, exactly the
pre-2026-07-18 structure — no ADV_IND/SCAN_RSP split attempted again). Result: the physical
Remote Control (`B0:10:A0:68:5C:8B`) connected to the mock for the first time in this entire
investigation, 45s after the flip. Two things to fix before calling this done:
1. ~~Geberit Home App's onboarding scan now shows **2 "unconfigured devices"** under "Mera
   Comfort" instead of 1.~~ **Resolved 2026-07-21, not a symptom at all**: the user's real
   Mera Comfort happened to be in BLE range during that specific test — the app was correctly
   showing two distinct physical devices, the real one and the mock. Confirmed unrelated to
   this REQ's company-ID flip (same result reproduced with v1.102.0b1, before the flip
   existed). Full history: `docs/developer/firmware-version.md`, "Resolved, 2026-07-21" under
   the firmware-version investigation.
2. The RC's connection never completed pairing — `bluetoothd` logs
   `src/device.c:new_auth() No agent available for request type 2` /
   `device_confirm_passkey: Operation not permitted`, and the kernel floods
   `unexpected SMP command 0x03` (1,945 times in 2.5 minutes) until `bluetooth.service` is
   manually restarted. The mock has never registered a BlueZ pairing agent
   (`org.bluez.Agent1`) — `bluez_peripheral.agent.NoIoAgent` is a ready-made candidate fix.
   (Distinct from the already-fixed battery-plugin SMP issue below — that
   was the mock spontaneously initiating pairing against itself; this is a real external device,
   the RC, initiating genuine pairing that the mock has no agent to answer.)

**Fix confirmed, 2026-07-20, v1.104.0b1 — `NoIoAgent` resolves the flood/hang completely.**
`MeraMock.run()` now registers `bluez_peripheral.agent.NoIoAgent(...).register(bus,
default=True)` right after the advertisement registration. Re-tested against the RC
(`Geberit-Remote-Control-Against-Mock-sniff-on-mock-01.pcapng`, cross-checked against the
mock's own console log for the same window): full SMP handshake completes cleanly twice in a
row — `Pairing Request` → `Pairing Response` → `Pairing Confirm` ×2 → `Pairing Random` ×2 →
`Encryption Information`/`Central Identification`/`Signing Information`/`Identity
Information`/`Identity Address Information` both directions → `LL_ENC_REQ`/`LL_ENC_RSP`/
`LL_START_ENC_REQ`/`LL_START_ENC_RSP`. Zero `No agent available` lines in the mock log, zero
`unexpected SMP command` lines in the kernel log for the same window (both greped to 0).
Post-encryption, the RC (as GATT client) reads several of its own already-known
characteristics from `_RCPairingService`/DIS (Device Name `"Geberit AC Remote"`, firmware
`"3.60.101.860/0000"`, handle `0x000E` = 12×`0xFF`, `"RS04 TS11"` — matching
`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-remote-control/pairing with RC and
toggle lid.md`'s real-device capture of the same handle 0x000C value), then writes `0x02`
(Indications-enable) to a CCCD at handle `0x0009` — acked, but nothing is ever sent back on
it. **Both sessions then end after ~21-24s with `LL_TERMINATE_IND reason=Remote User
Terminated`**, and the second session repeats the full Pairing Request from scratch rather
than resuming a stored bond. Neither is a new mystery: `_RCPairingService` is an
intentionally-scoped stub (its own docstring: "Contents beyond the service declaration are
unknown... All post-pairing RC traffic is encrypted and not yet decoded") with no
NOTIFY/INDICATE characteristic at all, so the RC has nothing to wait for and gives up; bond
non-persistence is consistent with the mock's existing `btmgmt unpair` startup sweep and
`_force_remove_and_reregister`'s `RemoveDevice` call (both pre-existing, unrelated to this
fix). **CCCD write decoded, 2026-07-20 — a dead end, in a good way.** Handle `0x0009` is not part
of the Geberit protocol at all: confirmed from the mock's own bluetoothd debug log
(`Handle range: 0x0006-0x0009  UUID: Generic Attribute Profile (0x1801)`), it's the standard
GATT "Service Changed" characteristic (`0x2A05`), auto-managed by BlueZ. Enabling indications
on it is routine BLE-central bookkeeping every well-behaved central does; no indication is
ever expected from it unless the GATT database structurally changes mid-connection, which it
doesn't here. The mock's ack was the complete, correct response — `_RCPairingService` isn't
implicated.

That reframes the ~21-24s disconnect as possibly not a gap at all. Cross-checked against
`pairing with RC and toggle lid.pcapng` (a real RC-to-real-toilet session): its own two
`CONNECT_IND`s are **~15.5s apart** (`t=4335.1s` → `t=4350.6s`), the same order of magnitude
as the mock's ~21-36s connect/disconnect/reconnect cadence — consistent with the RC simply
reconnecting on its own rhythm regardless of what the peripheral does, rather than the mock
failing to send something expected. That capture's own ATT decode goes dark after ~0.4s each
time (nRF Sniffer lost the channel-hop sync, a known sniffer limitation — not evidence either
way), so this can't be fully confirmed from evidence in hand. (Two other captures,
`mock-geberit-mera_RC_2026-06-25/26_*.md`, looked promising by filename but are misnamed: the
peer MAC `78:42:1C:38:DE:16` is iOS's stale-RPA artifact — see "Stale RPA between Connection 1
and Connection 2" below — not the RC.)

Next concrete step, not yet started: either a fresh real-device capture with the sniffer
closer/better-synced (to see what, if anything, follows those reads on real hardware beyond
what `pairing with RC and toggle lid.pcapng` captured), or a longer mock test letting the RC
cycle through several reconnects to see whether it eventually reports "paired" on its own
display.

**Major breakthrough, 2026-07-21 — real RC↔real-toilet pairing captured live, fully decrypted,
first genuine plaintext of the RC's own application protocol.** Two independent nRF52840
captures (`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-remote-control/real-mera/
pairing-ok-toggle-lid-Geberit-Remote-Control-real-mera-{mac,windows}.pcapng`, one per sniffer
host/OS, byte-identical results) watching the RC's SMP pairing *as it happened* (not just an
already-encrypted session) let the sniffer capture the LTK live and decrypt every subsequent
ATT frame — the key methodological difference from every earlier RC capture attempt, all of
which only ever saw ciphertext (see RELAY-ISS-002).

**The real device exposes GATT services to the RC that this mock has never implemented.**
`--gatt-map` on both captures gives an identical handle table:

| Handle | UUID | Notes |
|---|---|---|
| 0x000C/E/10/12/14 | `0x2A24/25/26/27/29` | standard DIS strings (Model/Serial/FW-rev/HW-rev/Manufacturer) |
| 0x0017 | `1db512c1-2aa1-45d7-894e-1e9441bc8389` | custom, role unconfirmed |
| 0x001A | `25dcdfd2-8867-48da-b1d6-1b5985c4f259` | custom, NOTIFY (CCCD at 0x001B) |
| 0x001D | `867710fb-5e31-49ba-84e0-a10d5d832ad7` | custom, WRITE |
| 0x001F | `7152f4a9-6523-4517-80a2-96d8b9273538` | custom, role unconfirmed |
| 0x0021 | `464ead99-ec2c-49d4-a186-af6ff8979a96` | custom, role unconfirmed |
| 0x0023 | `0e069b0a-967c-4002-91ac-1e51906a84b2` | custom, WRITE |
| 0x0026 | `5a4d406b-b210-47ba-b7e6-db6b9f2e9997` | custom, NOTIFY |

`FIND_BY_TYPE_VALUE` searches also confirm two 16-bit-alias service UUIDs never seen before,
`0x8A30` (group ends 0x0015) and `0xE0DB` (group ends 0x0018), alongside the already-known
`0x180A` (DIS, ends 0x000A) and `0xC526` (RC-pairing, ends 0x0024 — same end handle our mock
already returns). Handles 0x0026/0x0027 sit *beyond* 0xC526's own end handle, so they belong
to a still-unidentified further service. Net finding: **the real 0xC526 service (or its
neighbors) has at least 5–7 characteristics that the RC actually uses — this mock's
`_RCPairingService` has exactly one, read-only, unused by any of this exchange.** That is the
concrete, evidenced gap behind the mock's stalled RC sessions — not the CCCD-0x0009 dead end
above, and not (necessarily) just cadence.

**Decoded protocol sequence, byte-identical in both captures, repeated identically across two
connection attempts within each:**
1. `WRITE 0x0027 = 0x01` → acked
2. Multi-frame `WRITE` to `0x001D` (three ATT_WRITE_CMDs) — raw payload bytes decode cleanly as
   **UTF-16BE text**: `"      Pairing ok      "` (space-padded). Confirmed independently in
   both captures, byte-for-byte.
3. Multi-frame `WRITE` to `0x0023` — all zero bytes except a single `0x7B` at offset 2; not
   text, semantics not yet understood (a status/icon code is one plausible guess, unconfirmed).
4. `WRITE 0x001B = 0x01` (CCCD-enable on the NOTIFY characteristic at `0x001A`)
5. `NOTIF` from the toilet on `0x0026`: payload `03 02`

**Caveat on `nrf-ble-analyze.py`'s own decode**: the tool's generic multi-frame parser labels
the `0x001D`/`0x0023` writes as `Proc(ctx=0x20, proc=0x00)` / `Proc(ctx=0x00, proc=0x00)` —
this is almost certainly a **false positive**, not a genuine third protocol context alongside
the documented `ctx=0x00` (default) and `ctx=0x40` (firmware-update) in `.claude/rules/
ble-protocol.md`. The raw bytes are plain UTF-16BE text, not the standard Mera proc-call
framing (`ctx`/`proc` bytes at fixed offsets) — the tool's parser is built for that framing and
is misapplying it here to a differently-structured, RC-specific custom protocol. Do not treat
`ctx=0x20` as confirmed; `--raw` (dumping the literal bytes) is what actually revealed the text.

**Open gap, not resolved by this pass**: the user toggled the lid twice via the RC after
pairing succeeded, but neither capture's decoded output shows lid-toggle command bytes —
nothing decodes after the `03 02` notify in either file. Both captures show the RC doing a
second, brief reconnect (a `LL_ENC_RSP` with no preceding `LL_ENC_REQ` visible, i.e. the
session key for that specific reconnect may not be fully recoverable from what's on hand) that
repeats the identical "Pairing ok" handshake rather than showing anything toggle-specific. The
lid-toggle bytes may be in a later reconnect this pass didn't reach, or use a session key not
captured — not yet resolved.

**Implemented, 2026-07-21, v1.105.0b1 — `_RCPairingService` expanded from a single stub
characteristic to the five confirmed ones, plus the two newly-discovered ancillary services
(`0x8A30`, `0xE0DB`).** `_maybe_send_ack()` sends the confirmed `03 02` notify on `0x5a4d406b`
once the `0x25dcdfd2` CCCD is enabled, polling for it the same way `_send_info_frame_burst`
already waits for the A6 CCCD — mirroring the real captures' observed order, not a confirmed
trigger condition (the real device's actual reason for replying isn't known, only where the
reply falls in the sequence). Untested against a real RC whether this makes it progress any
further than the old stub did, or what the `0x0e069b0a` write's `0x7B` byte and the two
never-observed characteristics (`0x7152f4a9`, `0x464ead99`) actually need — next step is a
fresh RC test against this version.

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
discoverable, just not pairable through this path anymore.

**The rule above may now be obsolete — needs re-verification, not a third revert on sight
(2026-07-19).** `ee3171b` (2026-07-16 17:19) diagnosed the same root cause as the original
`b374e24` fix: BlueZ's built-in **Battery plugin** acting as a GATT *client*, trying to read
Battery Level from the connected iOS device on every connection; iOS refuses the
unauthenticated read; BlueZ escalates by spontaneously issuing an SMP Security Request, which
iOS surfaces as the system pairing dialog. This is entirely a Linux/BlueZ-host artifact —
real Mera hardware has no Linux desktop-style "show battery icon for connected accessories"
feature, so this mechanism has nothing to do with the actual Geberit protocol or real hardware
behavior.

**The very next day (2026-07-17)**, this same Battery-plugin mechanism was independently
diagnosed and fixed at the systemd level on `anneubuntu-studio` — a `bluetooth.service.d`
drop-in override forcing `bluetoothd --noplugin=battery` (see
`memory/mera-mock-battery-plugin-fix.md`), verified across two fresh test sessions with zero
recurrences of the SMP pairing-failure cycle. **Nobody has gone back to check whether
`pairable=on` is now actually safe again with that systemd override in place** — `ee3171b`'s
revert was correct for the environment it was tested against (battery plugin still active),
but that environment no longer matches the current one on `anneubuntu-studio`. Before deciding
between "scope pairing to just the RC" (this section's prior recommendation) and "leave
`pairable on` permanently, matching real hardware's apparent always-on behavior," re-test:
confirm the systemd override is active (`systemctl show bluetooth.service -p ExecStart` should
show `--noplugin=battery`), re-enable `pairable=on` in the mock, and check whether the iOS
pairing dialog still appears during a normal Home App connection. **The systemd override is
not automated or scripted anywhere in this repo** — it's a manual host-level config applied
once on one specific machine; if RC testing happens on a different host, or that host's
systemd config is ever reset, the original bug reappears silently with no repo-level warning.

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
`EstablishAsync()` returns (line 175 in the analyzed source). `ConnectionState` is set to
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

Field names corrected 2026-07-18: offset 0 is `SapNumber` (dotted format), not
"ArticleNumber" as this table previously labeled it — confirmed by the app's own
`DeviceIdentification` log line (`SapNumber=146.21x.xx.1, SerialNumber=...`) and
`docs/mqtt.md`'s dotted-format `Identification/SapNumber` topic. Offset 12 is
plain `SerialNumber` (previously mislabeled "SerialNumber (SAP)").

| Field | Value |
|---|---|
| SapNumber (offset 0) | `146.21x.xx.1` |
| SerialNumber (offset 12) | `HB2300EU000001` |
| ProductionDate | `11.04.2023` |
| Description | `AquaClean Mera Comfort` |

### GetNodeList (proc `0x05`) — 129 bytes

Node IDs: `[03, 04, 05, 06, 07, 08, 09, 0A, 0B, 0C, 0E, 0F]` (12 nodes)

### GetSOCApplicationVersions (proc `0x81`)

`"10"` + `0x12` + `0x00` → version `10.18`

### GetFirmwareVersionList (proc `0x0E`) — per requested component

**RESOLVED 2026-07-18 (mock v1.99.1b1) — the "must be uniform RS30.0" theory below was
wrong; the real root cause was a request-parsing bug, not firmware version content at
all.** Confirmed working end-to-end with the `rs28` profile (genuinely non-uniform,
real per-component values — component 1 = RS28.0 TS199, component 11 = RS07.0 TS22, and
each other component at its own real, differing version) plus the real device's serial
number: no blocking update screen, no "Fehler", correct version shown in
Maintenance→Firmware. Two compounding mock bugs, both in `_handle_request`:

1. The mock never reassembled multi-frame incoming WRITE requests — it dispatched on the
   FIRST frame alone and silently discarded any CONS continuation. The app's 12-component
   query (13 args bytes, more than the 9 that fit in one 20-byte frame) was always
   truncated to 8 components (missing 10, 11, 12, 14), regardless of what version values
   were configured — this, not the firmware values, is what looked like an unconditional
   blocking screen across every profile ever tested.
2. Fixing (1) alone made onboarding *worse* (0/4 connections): the real device sends a
   FlowControl CTRL ack after every frame of an incoming request (confirmed byte-for-byte
   from `onboarding-real-mera.md`, 14:11:09.414-.564) — the app will not send the CONS
   continuation without that ack. The mock now sends it per-frame before dispatch.

Full writeup, including why the earlier "uniform RS30.0" empirical finding was a red
herring: `memory/mera-firmware-update-request-truncation.md` (local Claude memory, not in
this repo).

<details>
<summary>Original (incorrect) theory, kept for history</summary>

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

</details>

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
| GetFirmwareVersionList (proc `0x0E`) | ✅ v1.54.0b1 — confirmed request/response shape; ⚠️ **request truncated to 8/12 components until v1.99.1b1** — see below |
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

### Firmware-update-request blocker — RESOLVED, mock v1.99.1b1 (2026-07-18)

Full onboarding confirmed end-to-end with a genuinely non-uniform, real firmware profile
(`rs28`) and the real device's serial number: no blocking "update required" screen, no
"Fehler", correct firmware version shown in Maintenance→Firmware. See the corrected
"GetFirmwareVersionList" section above for the root cause (request-frame truncation +
missing per-frame FlowControl ack — a request-side wire-protocol bug, not a firmware-value
issue). Full incident writeup: `memory/mera-firmware-update-request-truncation.md`.

Not yet tested: an actual simulated firmware *upgrade* through this now-working onboarding
path (Phase 9b's `ctx=0x40` state machine) — that's the next thing being tested.

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
