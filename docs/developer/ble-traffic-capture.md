# Capturing BLE Traffic Between the Geberit Home App and the Toilet

Recording the Bluetooth Low Energy traffic between the Geberit Home App and an AquaClean toilet is the primary method for analyzing device behavior: identifying unknown procedure codes, mapping parameter indices, and verifying protocol implementation.

Three capture methods are available:

| Method | Platform | What it can see | Output file | Analysis tool |
|---|---|---|---|---|
| Apple PacketLogger | iPhone + Mac | iPhone↔toilet only | `.txt` (Raw Data export) | `tools/ble-decode.py` |
| Android HCI Snoop Log | Android + Wireshark | Android phone↔toilet only | `.pcapng` | `tools/android-ble-analyze.py` |
| nRF52840 dongle sniffer | any OS + Wireshark | **any** device↔toilet, including remote and bridge | `.pcapng` | Wireshark + tshark (see below) |

The nRF52840 method is the only one that can capture traffic from devices that are not a phone (physical remote, bridge on a Raspberry Pi). It is required for comparing KE Request frames across all three client types.

---

## iPhone — Apple PacketLogger

### Prerequisites

- An iPhone running the Geberit Home App
- A Mac (any recent macOS version)
- A USB cable (Lightning or USB-C depending on your iPhone model)
- **Additional Tools for Xcode** — download from [developer.apple.com/download/applications](https://developer.apple.com/download/applications) (free, no paid developer account required). PacketLogger is inside the `Hardware I/O` folder. Drag it to `/Applications`.

### Step 1 — Install the iOS Bluetooth Logging Profile

The iPhone does not log Bluetooth traffic by default. A configuration profile activates it.

1. On your **iPhone**, open **Safari** and go to [developer.apple.com/bug-reporting/profiles-and-logs/](https://developer.apple.com/bug-reporting/profiles-and-logs/).
2. Log in with your Apple ID.
3. Scroll to the **Bluetooth** section and tap **Profile** to download it. Tap **Allow** on the popup.
4. Open **Settings**. At the very top you will see **Profile Downloaded** — tap it.
5. Tap **Install** (top right), enter your passcode, and follow the prompts until the profile shows as **Verified**.
6. **Restart your iPhone.** The logging daemon does not start until after a reboot.

> **Finding the profile later:** Settings → General → VPN & Device Management → under "Configuration Profile". If the entry is missing, the download in Safari did not complete — try again.

> **Profile expiry:** Apple's debug profiles expire after a few days. If logging stops working, delete the profile and re-download it.

### Step 2 — Record Traffic

1. Connect your iPhone to your Mac via USB.
2. On your iPhone, tap **Trust This Computer** if prompted.
3. Open **PacketLogger** on your Mac.
4. Click **File → New iOS Trace**. A stream of packets will appear immediately.
   ![PacketLogger — connected and capturing](images/ble-traffic-capture/iphone-packetlogger/packetlogger-connected.png)

5. Make sure all settings are configured as shown in the screenshot below.
   ![PacketLogger — settings and results](images/ble-traffic-capture/iphone-packetlogger/packetlogger-settings-including-results.png)

6. On your iPhone, open the **Geberit Home App** and perform the action you want to capture (connect to the toilet, trigger a shower, etc.).
7. Stop the trace once you have captured the action.


### Step 3 — Filter by Device (recommended)

The iPhone talks to every nearby Bluetooth device. Filtering keeps the file small and focused.

1. Find a packet labeled **LE Connection Complete** for the toilet.
2. In the detail pane (bottom), note the **Connection Handle** (e.g. `0x0040`).
3. Type `Handle: 0x0040` in the filter bar at the top of the window.

Alternatively, paste the toilet's MAC address (e.g. `38:AB:41:2A:0D:67`) directly into the filter bar.
   ![PacketLogger — select device](images/ble-traffic-capture/iphone-packetlogger/packetlogger-select-device.png)


### Step 4 — Save the Capture

**File → Export → Raw Data…**

The exported file should look as shown below. The disclosure triangles are expanded here to show the captured data.
![PacketLogger — settings and results (expanded)](images/ble-traffic-capture/iphone-packetlogger/packetlogger-settings-including-results-expanded.png)

Save the file with a descriptive name (e.g. `geberit-open-lid-2026-04-22.txt`). This produces a plain-text log that `tools/ble-decode.py` reads directly.

### Troubleshooting

| Symptom | Fix |
|---|---|
| PacketLogger does not see the iPhone | Unplug and re-plug the cable; tap **Trust This Computer** on the iPhone |
| Packet list is empty | Check Settings → General → VPN & Device Management — profile must be installed and not expired |
| Profile is missing from Settings | The Safari download did not complete — retry on iPhone, not on Mac |
| `File → New iOS Trace` is greyed out | iPhone not yet trusted; try unplugging and reconnecting |

> **Privacy:** while the profile is installed, the iPhone logs *all* Bluetooth activity. Delete the profile once you are done (Settings → General → VPN & Device Management → tap profile → Remove Profile).

### Step 5 — Analyze with `ble-decode.py`

```bash
/Users/jens/venv/bin/python tools/ble-decode.py session.txt --markdown --output session-analysis.md
```

Produces annotated markdown grouped by logical phase (Init, Identification, Common
Settings, State Poll, …) with request annotations and decoded responses.

| Option | Description |
|--------|-------------|
| `--markdown` | Full annotated markdown grouped by logical phase |
| `--output FILE` | Write markdown to FILE instead of stdout |
| `--filter 0xNN` | Show only one procedure (e.g. `--filter 0x51`) |
| `--from HH:MM:SS` / `--to HH:MM:SS` | Restrict to a time window |
| `--raw` | Print raw 20-byte frames without decoding |

---

## Android — HCI Snoop Log + Wireshark

### Prerequisites

- An Android phone running the Geberit Home App
- A computer (Mac, Windows, or Linux) with [Wireshark](https://www.wireshark.org/) installed
- A USB cable
- ADB (Android Debug Bridge) — included in [Android SDK Platform Tools](https://developer.android.com/studio/releases/platform-tools); on macOS also available via `brew install android-platform-tools`

### Step 1 — Enable Developer Options

Developer Options are hidden by default. Unlock them by tapping the **Build Number** entry in Settings 7 times rapidly.

**Standard Android (Pixel, stock):**
Settings → About Phone → tap **Build Number** 7 times

**Samsung:**
Settings → About Phone → Software Information → tap **Build Number** 7 times

**Xiaomi / Redmi / MIUI** (e.g. Redmi Note 9):
Settings → About Phone → tap **MIUI Version** 7 times
(Xiaomi uses "MIUI Version" instead of "Build Number")

**Oppo / Realme / ColorOS:**
Settings → About Phone → Version → tap **Build Number** 7 times

The phone shows *"You are now a developer!"* once unlocked.

> **If you cannot find it:** open the Settings search bar and type "Build" or "Developer".

Developer Options appear under Settings → System (stock Android) or Settings → Additional Settings (Xiaomi/MIUI).

### Step 2 — Enable Bluetooth HCI Snoop Log

1. Open **Developer Options** (see above).
2. Also enable **USB Debugging** here.
3. Find **Enable Bluetooth HCI snoop log** and toggle it on.
4. **Toggle Bluetooth off and then on again.** Android requires a Bluetooth restart to begin writing to the log buffer.

**Xiaomi/MIUI extra step:** also enable **USB Debugging (Security Settings)** in Developer Options. Without it, ADB cannot stream Bluetooth log data.

### Step 3 — Capture in Wireshark

1. Connect your phone to your computer via USB.
2. On the phone, tap **Allow USB Debugging** when the authorization prompt appears. Check *Always allow from this computer*.
3. In a terminal, run `adb devices` to verify the connection:
   ```
   List of devices attached
   ZY223XG967  device        ← success
   ZY223XG967  unauthorized  ← tap Allow on the phone
   (empty)                   ← bad cable or USB Debugging not enabled
   ```
4. Open **Wireshark**. In the interface list, look for:
   `Android Bluetooth HCI Snoop [device-serial-number]`

   ![Wireshark — select Android interface](images/ble-traffic-capture/android-wireshark/wireshark-select-interface.png)

5. Double-click it to start the live capture.
6. On your phone, open the **Geberit Home App** and perform the action you want to capture.
7. Stop the capture (red square button) once done.

> **Interface not appearing:** run `adb devices` first, then in Wireshark go to **Capture → Refresh Interfaces** (`Cmd+R`). If it still does not appear, close Wireshark completely, run `adb devices` in the terminal, then reopen Wireshark.

![Wireshark — Capture → Refresh Interfaces](images/ble-traffic-capture/android-wireshark/wireshark-refresh-interfaces.png)

> **Verify Wireshark's androiddump:** run `/Applications/Wireshark.app/Contents/MacOS/extcap/androiddump --extcap-interfaces` in a terminal. If your phone's serial number appears, Wireshark supports it.

> **macOS + Homebrew Wireshark:** you may need `brew install --cask wireshark-chmodbpf` to grant capture permissions.

### Step 4 — Save the Capture

1. **File → Save As…**
2. In the format dropdown, select **Wireshark/tcpdump/... - pcapng**.
3. Save with a descriptive name (e.g. `geberit-open-lid-2026-04-22.pcapng`).

This is the format expected by `tools/android-ble-analyze.py`.

### Step 5 — Analyze with `android-ble-analyze.py`

```bash
/Users/jens/venv/bin/python tools/android-ble-analyze.py session.pcapng --markdown --output session-analysis.md
```

Also accepts the raw Android `BTSNOOP_LOG.log` file directly — no need to save as pcapng first.

| Option | Description |
|--------|-------------|
| `--mac AA:BB:CC:DD:EE:FF` | Filter to one device MAC (default: `38:AB:41:2A:0D:67`) |
| `--markdown` | Full annotated markdown grouped by logical phase |
| `--output FILE` | Write markdown to FILE instead of stdout |
| `--all-macs` | Show events for all MAC addresses |
| `--raw` | Print raw HCI packets without decoding |

### Troubleshooting

| Symptom | Fix |
|---|---|
| No packets appear after connecting | Toggle Bluetooth off and on on the phone |
| Wireshark does not show the Android interface | Run `adb devices` in terminal, then Capture → Refresh Interfaces in Wireshark |
| `adb devices` shows `unauthorized` | Tap **Allow USB Debugging** on the phone screen |
| `adb devices` shows nothing | Check cable; verify USB Debugging is enabled |
| Xiaomi: ADB connects but no BLE data streams | Enable **USB Debugging (Security Settings)** in Developer Options |

---

## nRF52840 Dongle — Passive BLE Sniffer (any device↔toilet)

This method uses a Nordic nRF52840 USB dongle (PCA10059) flashed with Nordic's
sniffer firmware. Unlike the phone-based methods, it works passively: it captures
BLE traffic between **any** two devices without needing access to either of them.

> A BLE scanner app (e.g. Nordic nRF Scanner on Android) is like standing in a room
> and only hearing people who are talking **to you** — it only sees devices that are
> actively advertising, which means BLE centrals (like a physical remote control that
> initiates connections) are completely invisible to it.
> The nRF52840 is a **hidden microphone that hears every conversation in the room**
> regardless of who is talking to whom.

| | nRF Scanner (Android app) | nRF52840 dongle |
|---|---|---|
| **Role** | BLE central — actively scans and connects | Passive sniffer — listens to everything over-the-air |
| **What it sees** | Only advertising packets (`ADV_IND`) from peripherals | Every BLE frame from every device: `ADV_IND`, `ADV_DIRECT_IND`, `CONNECT_IND`, all data channel packets |
| **Can see BLE centrals?** | No — centrals don't advertise | Yes — sees their `CONNECT_IND` and all data frames |
| **Can decode GATT traffic?** | Only from connections it initiates itself | Yes — any connection it catches the `CONNECT_IND` for |
| **Needs to participate?** | Yes — it's a party in the conversation | No — completely passive, invisible to both devices |
| **Can capture remote ↔ toilet?** | No | Yes |

**Use this method when you need to capture:**
- Physical remote↔toilet (no phone involved — the remote is a BLE central and never
  appears in a scanner app)
- Bridge↔toilet (bridge runs on a Raspberry Pi with no HCI log)
- All three clients in the same session to compare frame-by-frame

Alba does not use BLE link-layer encryption (zero SMP frames in all captures), so all
ATT write payloads — including raw Arendi handshake frames (KE Request, EP Response) —
are visible in plaintext to the sniffer.

### Prerequisites

- nRF52840 dongle (PCA10059) — Nordic Semiconductor, ~€10
- macOS, Windows, or Linux
- Wireshark **≥ 3.4.7**

**Reference documentation:**
[nRF Sniffer for Bluetooth LE User Guide v4.0.0 (PDF)](https://docs.nordicsemi.com/bundle/nrfutil_ble_sniffer_pdf/resource/nRF_Sniffer_BLE_UG_v4.0.0.pdf) — the authoritative Nordic reference covering firmware flashing, Wireshark integration, device following, and capture analysis.

> **Note on the old nrfutil Python package:** `pip install nrfutil` is deprecated
> since 2022 and no longer maintained. Do not use it. The current tool is a standalone
> binary with the same name, described below.

### Step 1 — Install nRF Util

Download the standalone binary from:
**https://www.nordicsemi.com/Products/Development-tools/nRF-Util/Download#infotabs**

Choose your platform (macOS universal, Linux x64, Windows x64).

```bash
# macOS / Linux
chmod +x nrfutil
sudo mv nrfutil /usr/local/bin/
nrfutil --version
```

### Step 2 — Install the ble-sniffer and device modules

```bash
nrfutil install ble-sniffer   # downloads sniffer firmware files alongside the command
nrfutil install device        # needed for flashing the dongle
```

After `nrfutil install ble-sniffer`, the firmware files are placed in:
```
<nrfutil install dir>/share/nrfutil-ble-sniffer/firmware/
```

The PCA10059 dongle uses a **`.zip`** firmware file (not `.hex`):
```
sniffer_nrf52840dongle_nrf52840_<version>.zip
```

### Step 3 — Flash the firmware onto the dongle

**3a. Enter bootloader mode**

The dongle has one button: the small round **SW1** on the PCB face.

Press and hold **SW1**, plug the dongle into USB, then release SW1.
The LED pulsates **red** — bootloader is active.

**3b. Find the dongle's serial number**

```bash
nrfutil device list
```

Example output (macOS M1, dongle in bootloader mode):
```
WARNING: JLinkARM DLL not found. Devices that require J-Link will not be recognized
correctly, and J-Link operations will not be available. Install SEGGER J-Link from
https://www.segger.com/downloads/jlink/. Currently tested version: JLink_V9.24a.

E4E7F6146B56
Product         Open DFU Bootloader
Ports           /dev/tty.usbmodemE4E7F6146B561
Traits          nordicDfu, nordicUsb, serialPorts, usb

Supported devices found: 1
```

The J-Link warning is harmless — the PCA10059 dongle uses `nordicDfu`, not J-Link.
Look for the device with `nordicDfu` in its traits. Its serial number is the 12-character
alphanumeric string on the first line (`E4E7F6146B56` in this example).

**3c. Find the firmware file**

```bash
find ~ -name "sniffer_nrf52840dongle_*.zip" 2>/dev/null
# /Users/jens/.nrfutil/share/nrfutil-ble-sniffer/firmware/sniffer_nrf52840dongle_nrf52840_4.1.1.zip
```

**3d. Program the firmware**

```bash
nrfutil device program \
  --serial-number E4E7F6146B56 \
  --firmware ~/.nrfutil/share/nrfutil-ble-sniffer/firmware/sniffer_nrf52840dongle_nrf52840_4.1.1.zip
```

Replace the serial number and firmware path with the values from the previous steps.

Example output:
```
WARNING: JLinkARM DLL not found. [...]

[00:00:02] ###### 100% [1/1 E4E7F6146B56] Programmed
```

After programming, unplug and replug the dongle (without holding SW1). The LED stops
pulsating — it is now running the sniffer firmware.

**Alternative: nRF Connect for Desktop (GUI)**

If you prefer a graphical tool, install **nRF Connect for Desktop** from nordicsemi.com,
open the **Programmer** app, enter bootloader mode on the dongle, select the dongle,
load the `.zip` firmware file, and click **Write**.

### Step 4 — Install the Wireshark plugin (one command)

```bash
mkdir -p ~/.local/lib/wireshark/extcap
nrfutil ble-sniffer bootstrap
```

Example output:
```
Bootstrapping ble-sniffer...
Bootstrap succeeded
Next step
---------

Program a device with the appropriate sniffer firmware. [...]

Supported devices
-----------------
* nRF52840 Dongle (firmware = /Users/jens/.nrfutil/share/nrfutil-ble-sniffer/firmware/sniffer_nrf52840dongle_nrf52840_4.1.1.zip)
[...]
```

The `mkdir` is required on macOS — `bootstrap` fails if the directory does not exist.
It copies the extcap shim into that directory automatically; no manual file copying needed.

Open Wireshark. The interface list should include:
```
nRF Sniffer for Bluetooth LE [nRF52840 Dongle /dev/cu.usbmodemXXX]
```

If it does not appear: Capture → Refresh Interfaces.

To find Wireshark's extcap directory manually: Help → About Wireshark → Folders tab →
look for **Personal Extcap path**.


### Physical setup — critical for reliable CONNECT_IND capture

The single most important factor is **physical proximity** of the dongle to the toilet's
Bluetooth module.

> **Confirmed working setup (2026-06-01):** A 2-metre USB extension cable with the dongle
> placed **directly on the toilet housing** at the location of the internal Bluetooth
> component (typically behind the top/rear panel). CONNECT_IND was caught on the second
> attempt. Previous captures from a desk 1–2 metres away failed completely.

Additional checklist:
- Plug the dongle into a **USB 2.0 port** (USB 3.0 generates 2.4 GHz RF noise that drops
  BLE packets)
- Use a **passive USB extension cable** if needed — active cables add latency
- If CONNECT_IND is still missed after 2–3 attempts, move the dongle closer

### Step 5 — Start capturing and select a device

**5a. Make the nRF Sniffer toolbar visible**

The Device dropdown only exists on the nRF Sniffer toolbar, which is hidden by default:
View → Interface Toolbars → **nRF Sniffer for Bluetooth LE** (enable the checkmark).

![Enabling the nRF Sniffer toolbar via View → Interface Toolbars](images/ble-traffic-capture/nRF52840-dongle/Wireshark%20-%20Enable%20Interface%20Toolbars%20-%20for%20nRF%20Sniffer%20for%20Bluetooth%20LE.png)

**5b. Start the capture**

Double-click the `nRF Sniffer for Bluetooth LE` interface in the Wireshark home screen.
A flood of BLE advertising packets will appear. The nRF Sniffer toolbar is now visible
just below the main toolbar row.

![Wireshark start screen with nRF Sniffer for Bluetooth LE interface highlighted and ready to double-click](images/ble-traffic-capture/nRF52840-dongle/Wireshark%20-%20Capture%20-%20Refresh%20Interfaces.png)

The toolbar also contains a **Key** dropdown (`Legacy Passkey` by default) and a
**Value** field. These are for decrypting BLE link-layer encrypted sessions. The
Geberit Mera Comfort and Alba use no BLE link-layer encryption (zero SMP frames),
so leave **Key** at any setting and leave **Value** empty — it has no effect on capture.

**5c. Select a device — always lock before triggering the action**

The toolbar contains a **Device** dropdown, initially set to `All advertising devices`.

1. Let it run for a few seconds — nearby BLE devices populate the dropdown.
2. Click the **Device** dropdown and select the toilet's MAC address from the list.
   The toolbar shows the RSSI value (e.g. `-58 dBm`) once the device is found.
3. Wireshark locks onto that device and follows its connections.

The toilet's MAC can be found in the HA integration's device entry, in a previous
btsnoop capture, or by scanning via `tools/aquaclean-connection-test.py`.

![Device dropdown showing all advertising devices — select the Geberit MAC before opening the app](images/ble-traffic-capture/nRF52840-dongle/Wireshark%20-%20nRF%20Sniffer%20for%20Bluetooth%20LE%20-%20Device%20-%20All%20advertisig%20devices.png)

After selecting the Geberit MAC the toolbar switches to "Following device" mode:

![Device dropdown after selecting the Geberit MAC — sniffer is now locked and following](images/ble-traffic-capture/nRF52840-dongle/Wireshark%20-%20nRF%20Sniffer%20for%20Bluetooth%20LE%20-%20Device%20-%20Follow.png)

**Why "Following device" — not "All advertising devices":**

In "All advertising devices" mode the dongle hops randomly across channels; it
captures advertising packets from everything nearby but misses most `CONNECT_IND`
frames. In "Following device" mode the firmware synchronises to the device's
advertising interval, predicts which channel the next `ADV_IND` will arrive on, and
waits there. Because the `CONNECT_IND` always arrives on the same channel as the
preceding `ADV_IND`, a synchronised sniffer is far more likely to catch it.

**Why `CONNECT_IND` matters:** the dongle uses the channel-hop parameters inside the
`CONNECT_IND` to follow the BLE connection onto its data channels. If the `CONNECT_IND`
is missed, the dongle has no way to decode the connection — Wireshark goes silent for the
entire session even though the connection is active and the app is working.

**5d. Lua alert plugin**

A Wireshark Lua plugin plays an audio alert when the `CONNECT_IND` is caught, so you
know exactly when the session is locked and when to tap the button in the app.

| Sound | Event | What it means |
|-------|-------|---------------|
| **Tink** (soft) | SCAN_REQ — phone found the toilet | CONNECT_IND is ~100–300 ms away |
| **Ping** (clear) | CONNECT_IND caught | Sniffer locked; ATT frames will decode; tap the app now |
| Tink but no Ping | CONNECT_IND missed | Close app, wait for re-advertising, try again |

Install:

```bash
mkdir -p ~/.config/wireshark/plugins
cp tools/wireshark/mera_alert.lua ~/.config/wireshark/plugins/
```

Edit `TOILET` at the top of the file to match your device's BLE MAC address (lowercase,
colon-separated). Then reload without restarting Wireshark:
**Analyze → Reload Lua Plugins** (`Cmd+Shift+L`).

Verify in **Tools → Lua Console**:
```
[mera_alert] loaded — watching 38:ab:41:2a:0d:67
```

**Capture workflow with the plugin:**

1. Start capture, select device MAC in the toolbar
2. Open the Geberit Home app — do not tap anything yet
3. Hear **Tink** → app found the toilet, connection forming
4. Hear **Ping** → CONNECT_IND captured; tap Toggle Lid (or any command) in the app
5. If Tink but no Ping → sniffer missed CONNECT_IND; close app, wait for the toilet
   to resume advertising (ADV_IND frames reappear in Wireshark), try again

### Step 6 — Captures to make (Alba remote displacement investigation)

#### Capture A — Official app session (baseline)
1. Start sniffer, device = Alba MAC
2. Open the Geberit Home app → connect to the Alba → let it complete the init sequence
3. Stop after 30 s. Save as `app-session.pcapng`

#### Capture B — Bridge session
1. Start sniffer, device = Alba MAC
2. Trigger a HACS poll (re-enable the integration, or wait for auto-poll)
3. Stop after 30 s. Save as `bridge-session.pcapng`

#### Capture C — Remote control

**Finding the remote's MAC first:**
Before locking the sniffer to the remote, capture the toilet's advertising phase for a
few seconds. The toilet maintains a bond with the physical remote and periodically sends
`ADV_DIRECT_IND` frames addressed to the remote's MAC. In Wireshark, filter
`btle.advertising_header.pdu_type == 1` (ADV_DIRECT_IND) to find the destination
address — that is the remote's BLE MAC. Note it down before proceeding.

1. Start sniffer, device = Alba MAC
2. Press a button on the physical remote (it briefly BLE-connects to send the command)
3. Stop. Save as `remote-session.pcapng`

This is the only way to see the remote's raw KE Request — no phone-based capture can
do this. It will confirm the remote's `keyset_id` and whether its frame structure
differs from the app or bridge in any other field.

#### Capture D — Displacement in action
1. Start sniffer, device = Alba MAC (or `All advertising devices`)
2. With the remote in its normal "registered" state (no yellow exclamation mark)
3. Trigger a bridge poll
4. Observe: does the toilet send any notification to the remote? Does the remote
   start re-advertising after the bridge disconnects?
5. Save as `displacement.pcapng`

Capture D is the most diagnostic: if the toilet sends something to the remote
**before** the bridge's KE Request even completes, the displacement trigger is at
the EP exchange level or earlier. If it happens after the bridge's KE Response,
the trigger is in the KE layer.

### Step 6b — Mera Comfort: remote vs app displacement experiment

**Why Mera first:** the Mera Comfort uses the unencrypted legacy AC protocol — every ATT frame
is visible in plaintext, with no KE exchange to decode.  Running the displacement experiment on
the Mera gives a clean baseline: if the same pattern appears on Mera and Alba, the root cause is
in the device firmware (not the Arendi security layer); if only Alba displaces, the Arendi
`keyset_id` is the likely culprit.

#### Find the remote's MAC first

During idle (remote not actively connected), the Mera toilet periodically sends `ADV_DIRECT_IND`
frames addressed to the remote's BLE MAC.  Capture a few seconds with the sniffer in
**"All advertising devices"** mode and filter in Wireshark:

```
btle.advertising_header.pdu_type == 1
```

The destination address in those frames is the remote's MAC.  Note it down.

#### When to start Wireshark — the golden rule

**Select the device MAC in the toolbar before triggering any connection.**
The sniffer must be synchronized to the toilet's advertising interval *in advance* — it cannot
retroactively follow a connection whose `CONNECT_IND` it missed.  The sequence is always:

1. Start capture
2. Select Mera MAC in the Device dropdown → toolbar shows "Following device"
3. *Then* open the app / press the remote button

Never trigger a connection before step 2.

#### The CONNECT_IND hopping problem

BLE advertising rotates across three fixed channels (37, 38, 39) roughly every 100–300 ms.
The phone or remote sends `CONNECT_REQ` on the *same channel* as the `ADV_IND` it just heard.
The sniffer predicts which channel comes next and waits there — but the prediction can be wrong
once, causing the `CONNECT_IND` to land on a different channel.

**What happens on a miss:** Wireshark goes silent immediately after the connection forms — the
`LE LL` entries stop and no ATT frames appear, even though the connection is active and the app
is working normally.  The sniffer cannot decode data-channel packets without the `CONNECT_IND`
parameters.

**The Lua plugin tells you in real time:**

| Sound | Meaning | Action |
|-------|---------|--------|
| **Tink** | `SCAN_REQ` seen — device found, `CONNECT_IND` imminent | Stay still |
| **Ping** | `CONNECT_IND` caught — sniffer locked | Proceed with the action in the app / on the remote |
| Tink, then silence | `CONNECT_IND` missed | Close app / wait for disconnect; see below |

**What you can still observe even after a miss:**

- `ADV_IND` packets resume once the toilet is advertising again (visible in Wireshark)
- `ADV_DIRECT_IND` packets from the toilet to the remote's MAC — these appear during the
  advertising phase and confirm whether the toilet is still trying to reach the remote
- The remote's own advertising (if it re-advertises after losing its connection)

These advertising-layer observations can answer the displacement question even without catching
a single ATT frame.

**Improving hit rate:**

- Place the dongle **directly on the toilet housing** via a USB extension cable (confirmed
  working at 2 m — see physical setup above).  Proximity is the single biggest factor.
- Each attempt has roughly a 50–70 % success rate in "Following device" mode; expect 2–3 tries.
- Do not stop and restart Wireshark between attempts — let all attempts accumulate in one file.
  Each miss appears as a Tink with no subsequent ATT frames; each hit appears as Tink → Ping →
  ATT frames.  The full timeline in one file is more useful for analysis than separate files.

#### The four capture scenarios

Run all four in a single Wireshark session if possible.  Save once at the end.

**Scenario 1 — Remote baseline (remote sends a command, no app involved)**

1. Sniffer running, Mera MAC selected
2. Press a button on the physical remote
3. Hear Tink → Ping (or retry if miss)
4. Remote sends its command and disconnects (~1 s)
5. Continue to Scenario 2 without stopping

**Scenario 2 — App connects while remote is idle → app disconnects → does remote reconnect?**

1. Open Geberit Home app on iPhone/Android
2. Tink → Ping (or retry)
3. Let the app complete its init sequence (~10 s)
4. Close the app — watch for `ADV_IND` to resume in Wireshark
5. Wait 30 s and observe: does the remote reconnect on its own?  Does the toilet send
   `ADV_DIRECT_IND` to the remote's MAC?  Does a remote Tink → Ping appear?

**Scenario 3 — App connects while remote is already connected → app disconnects**

1. First trigger the remote to connect (press a button) — Tink → Ping
2. While the remote is still connected (within ~1 s), open the app
3. Observe: does the app get a connection?  Does the remote get a disconnect?
4. Close the app — observe recovery

**Scenario 4 — Bridge poll instead of app (the actual issue scenario)**

1. Trigger a bridge poll (enable the HACS integration or use `POST /data/state`)
2. Tink → Ping (or retry)
3. Bridge connects, polls, disconnects
4. Observe: does the remote reconnect cleanly afterwards?

#### What to look for in the capture

| Observation | Interpretation |
|-------------|----------------|
| `ADV_DIRECT_IND` from Mera to remote MAC after app/bridge disconnects | Toilet remembers the remote bond and is actively inviting it back — displacement is temporary |
| No `ADV_DIRECT_IND`, remote has to initiate reconnect | Toilet does not track the remote; remote must poll |
| Remote ATT frames look identical before and after app session | No displacement; bridge can coexist |
| Remote gets a disconnect event mid-session when app connects | Device enforces single-client limit at firmware level — same root cause as Alba |

Analyse the result with:

```bash
/Users/jens/venv/bin/python tools/nrf-ble-analyze.py displacement.pcapng --markdown --output displacement-analysis.md
```

---

### Step 7 — Automated analysis with `nrf-ble-analyze.py`

`tools/nrf-ble-analyze.py` decodes nRF52840 `.pcapng` files directly.  It calls
`tshark` internally to extract ATT frames, auto-detects the device type (Mera
Comfort or Alba), and decodes the Geberit application layer.

**Requires:** `tshark` installed (`brew install wireshark` on macOS, or the
`wireshark` package on Linux — the Wireshark GUI is not required).

#### Default output — compact procedure table

```bash
/Users/jens/venv/bin/python tools/nrf-ble-analyze.py capture.pcapng
```

Auto-detects the toilet MAC and device type, prints a compact table:

```
[+] Detected: 38:AB:41:2A:0D:67  type=mera  addr_field=btle.peripheral_bd_addr
[+] 1,393 ATT frames, 1,333 matching events

========================================================================
File   : capture.pcapng  [nRF52840 pcapng, 1,393 ATT frames]
Device : 38:AB:41:2A:0D:67  (Geberit AquaClean Mera Comfort)
========================================================================

  Time          Proc  Name                                Args
  ------------  ----  ----------------------------------  -----------------------------------
  t=82.8s       0x82  GetDeviceIdentification             Reading device model and SAP number
  t=83.0s       0x05  GetNodeList                         Reading node list
  t=83.3s       0x81  GetSOCApplicationVersions           Reading SOC firmware version (RS/TS)
  t=83.5s       0x0e  GetFirmwareVersionList              Querying 12 firmware component IDs: [...]
  t=83.9s       0x11  SubscribeNotif_0x11                 Subscription handshake
  ...
  t=88.0s       0x0d  GetSystemParameterList              Polling 12 params: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
  t=88.2s       0x59  GetFilterStatus                     Checking 12 filter record IDs: [...]
  t=89.2s       0x55  UnknownProc_0x55                    Unknown — args=(none)
  t=93.7s       0x09  SetCommand                          **Triggering 0x03**
  t=98.5s       0x09  SetCommand                          **Triggering ToggleLidPosition**
```

#### Annotated markdown — full session grouped by phase

```bash
/Users/jens/venv/bin/python tools/nrf-ble-analyze.py capture.pcapng \
  --markdown --output session-analysis.md
```

Produces the same annotated markdown format as `android-ble-analyze.py` and
`ble-decode.py`: phases (Init, Identification, Common Settings, State Poll, …)
with request annotations and decoded responses.

#### Options

| Option | Description |
|--------|-------------|
| `--mac AA:BB:CC:DD:EE:FF` | Filter to one device MAC (auto-detected if omitted) |
| `--markdown` | Full annotated markdown grouped by logical phase |
| `--output FILE` | Write markdown to FILE instead of stdout |
| `--raw` | Print raw ATT bytes without decoding |

#### Device auto-detection

The tool determines Mera vs. Alba without any `--mac` flag:

1. **BLE advertising local name** — Alba advertises `"AcAlba"` as a Complete Local Name
   EIR/AD record; this is read from advertising frames before the connection.
2. **ATT write handle** — if no local name is found (Mera Comfort uses manufacturer-specific
   advertising only, no local name): handle `0x0003` → Mera, handle `0x001E` → Alba.

#### Wireshark version compatibility

`btle.slave_bd_addr` was renamed to `btle.peripheral_bd_addr` in Wireshark 4.0.
The tool probes which name is populated in the file and uses the correct one automatically.
No manual adjustment needed.

---

### Step 7b — Finding the remote control's BLE MAC address

The physical remote is a BLE central — it never advertises, so scanner apps and normal
BLE scans are blind to it.  The only way to find its MAC is to capture the `CONNECT_IND`
frame it sends when connecting to the toilet.

Use `tools/find-geberit-remote.py`:

```bash
# After capturing a pcapng with Wireshark (All advertising devices mode, press remote):
/Users/anne/venv/bin/python tools/find-geberit-remote.py capture.pcapng
```

The script finds all `CONNECT_IND` frames in the file and identifies the initiator address
(the remote).  Texas Instruments OUI addresses are tagged as likely Geberit hardware.

If the toilet's MAC is already known, pass it to get a highlighted result:

```bash
/Users/anne/venv/bin/python tools/find-geberit-remote.py capture.pcapng --toilet 38:AB:41:2A:0D:67
```

#### Confirmed MACs

| Device | Toilet MAC | Remote MAC | Notes |
|--------|-----------|------------|-------|
| jens62 Mera Comfort | `38:AB:41:2A:0D:67` | `B0:10:A0:68:5C:8B` | Texas Instruments OUI; confirmed from `local-assets/Bluetooth-Logs/nRF52840/jens62/get-ble-address-of-geberit-remote-control.pcapng` |

#### `--live` mode and its limitation

`find-geberit-remote.py --live` opens the sniffer serial port directly (no Wireshark).
It works for receiving advertising packets but **cannot capture `CONNECT_IND`**:

- The sniffer requires a `REQ_FOLLOW` command to lock onto a device's advertising channel
  and catch the `CONNECT_IND` on that same channel
- `REQ_FOLLOW` via raw serial is incompatible with nrfutil v4.x firmware (PID `1915:522A`)
- The extcap shim inside Wireshark handles `sendFollow()` timing correctly — that is why
  Wireshark works and direct serial does not
- This was investigated and confirmed as a dead end; see `tools/archive/sniff.py`

**Reliable workflow:** Wireshark (extcap) → capture pcapng → `find-geberit-remote.py capture.pcapng`

---

### Step 8 — Manual KE Request analysis (Alba only)

#### Find the KE Request in Wireshark

Filter for ATT writes from the client to the toilet:
```
btatt.opcode == 0x52
```
(ATT_WRITE_CMD — `response=False` writes, used by current bridge versions)

or
```
btatt.opcode == 0x12
```
(ATT_WRITE_REQ — `response=True` writes, used by some older versions and the app)

Look for packets from client to server (toilet). The **KE Request** is the longest
client→device frame in the handshake (~55 raw bytes after COBS encoding), sent
immediately after the EP Response (nonces) frame arrives from the device.

The GATT handle for Arendi writes on the Alba is `0x001e`; notifications arrive on
`0x0020`. Filter: `btatt.handle == 0x001e`.

#### Extract with tshark

```bash
tshark -r bridge-session.pcapng \
  -Y "btatt.handle == 0x001e || btatt.handle == 0x0020" \
  -T fields \
  -e frame.number \
  -e frame.time_relative \
  -e btatt.opcode \
  -e btatt.handle \
  -e btatt.value \
  2>/dev/null
```

If the handle differs from `0x001e` (different GATT enumeration on first connect),
run without the handle filter and look for long ATT_WRITE frames.

#### Decode a KE Request manually

Copy the `btatt.value` hex from a KE Request frame and run:

```bash
/path/to/python - <<'EOF'
def cobs_decode(data):
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]; i += 1
        for _ in range(code - 1):
            out.append(data[i]); i += 1
        if code < 0xFF and i < len(data):
            out.append(0)
    return bytes(out)

raw = bytes.fromhex("PASTE_HEX_HERE")
frames = []
i = 0
while i < len(raw):
    if raw[i] == 0:
        try: end = raw.index(0, i + 1)
        except ValueError: break
        if raw[i+1:end]:
            frames.append(cobs_decode(raw[i+1:end]))
        i = end + 1
    else:
        i += 1

for f in frames:
    ctrl = f[0]
    payload = f[1:-2]
    ns = (ctrl >> 1) & 7; nr = (ctrl >> 5) & 7
    print(f"ctrl=0x{ctrl:02x}  N(S)={ns} N(R)={nr}  payload={payload.hex()}")
    if len(payload) >= 1:
        t = payload[0]
        print(f"  type=0x{t:02x}", end="")
        if t == 0x12 and len(payload) == 50:
            keyset = payload[49]
            print(f" = KE_REQUEST")
            print(f"  pubkey   = {payload[1:33].hex()}")
            print(f"  cmac     = {payload[33:49].hex()}")
            print(f"  keyset_id= 0x{keyset:02x}  ({'app/bridge key' if keyset==0 else 'remote key' if keyset==1 else 'UNKNOWN'})")
        else:
            print()
EOF
```

#### Key comparisons across captures

| Frame | App | Bridge | Remote | Significance |
|-------|-----|--------|--------|--------------|
| `keyset_id` | `0x00` (confirmed) | `0x00` (confirmed) | expected `0x01` | Confirm remote uses keyset 1 |
| KE Request total size | 50 B payload | 50 B payload | ? | Any extra fields? |
| Version Request payload | ? | logged | ? | Protocol version difference? |
| EP Request payload | ? | logged | ? | Capability flag difference? |
| Capture D: toilet→remote after bridge KE | — | — | any notification? | Displacement trigger timing |

### Successful Mera Comfort capture — reference screenshot

The screenshot below shows a confirmed successful OTA capture of the Geberit AquaClean
Mera Comfort (device HB2304EU298413, firmware RS146.21), made 2026-06-01 with the nRF52840
dongle placed on the toilet housing via a 2 m USB extension cable.

**Verifying a successful capture — look for ATT Protocol entries:**
In the Protocol column, a successful capture shows entries labelled **`ATT`** (Bluetooth
Attribute Protocol) with descriptions such as `Sent Write Command, Handle: 0x0003` and
`Rcvd Handle Value Notification, Handle: 0x000f`.  If you see only `LE LL` entries
(Empty PDU, Link Layer frames), `CONNECT_IND` was missed and no application data was
captured — close the app, wait for the toilet to resume advertising, and try again.

**Frame 10059 (highlighted):** ATT Handle Value Notification from the toilet (Peripheral)
to the iPhone (Central) on handle `0x000f` — the first notification fragment of a
GetSystemParameterList response arriving at t=88.049 s.  The surrounding frames show the
characteristic write/notify rhythm: frames 10054 and 10056 are `Sent Write Command` to
handle `0x0003` (the SPL request), frame 10059 is the first `Rcvd Handle Value Notification`
on handle `0x000f` (the response).  The multi-handle notification pattern on `0x000f`,
`0x0013`, `0x0017`, `0x001b` carries identification data and procedure responses split
across 20-byte ATT frames.

![Wireshark showing successful Mera Comfort OTA BLE capture. Frame 10059 (highlighted) is an ATT Handle Value Notification on handle 0x000f — the first response fragment from the toilet to the iPhone after a GetSystemParameterList write. The capture contains 1393 ATT frames including identification data, GetStoredCommonSetting responses, and the full polling loop.](images/ble-traffic-capture/nRF52840-dongle/Wireshark%20-%20Mera%20Comfort%20sniff%20-%20ATT.png)

---

## What to Include When Sharing a Capture

When submitting a capture file for analysis (e.g. as a GitHub issue attachment):

1. **iPhone:** attach the `.txt` file from File → Export → Raw Data.
2. **Android:** attach the `.pcapng` file saved from Wireshark.
3. **nRF52840:** attach the `.pcapng` file saved from Wireshark. Include the Alba's MAC address so the correct connection can be identified.
4. Note what action you performed and at what approximate time (used to find the relevant window in the file).
5. Include the device MAC address and model if known.
