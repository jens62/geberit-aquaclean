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

**Use this method when you need to capture:**
- Physical remote↔toilet (no phone involved)
- Bridge↔toilet (bridge runs on a Raspberry Pi with no HCI log)
- All three clients in the same session to compare frame-by-frame

Alba does not use BLE link-layer encryption (zero SMP frames in all captures), so all
ATT write payloads — including raw Arendi handshake frames (KE Request, EP Response) —
are visible in plaintext to the sniffer.

### Prerequisites

- nRF52840 dongle (PCA10059) — Nordic Semiconductor, ~€10
- macOS, Windows, or Linux
- Wireshark **≥ 3.4.7**

> **Note on the old nrfutil Python package:** `pip install nrfutil` is deprecated
> since 2022 and no longer maintained. Do not use it. The current tool is a standalone
> binary with the same name, described below.

### Step 1 — Install nRF Util

Download the standalone binary from `nordicsemi.com` → Products → nRF Util → Download.
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

Look for a device with the `nordicDfu` trait — that is your dongle in bootloader mode.
Its serial number is 12 alphanumeric characters, e.g. `A1234B5678C9`.

**3c. Program the firmware**

```bash
nrfutil device program \
  --serial-number A1234B5678C9 \
  --firmware /path/to/sniffer_nrf52840dongle_nrf52840_4.4.1.zip
```

Replace the serial number and firmware path with the values from the previous steps.
Use `find ~ -name "sniffer_nrf52840dongle_*.zip" 2>/dev/null` to locate the file if
the path is unclear.

After programming, unplug and replug the dongle. The LED stops pulsating — it is now
running the sniffer firmware.

**Alternative: nRF Connect for Desktop (GUI)**

If you prefer a graphical tool, install **nRF Connect for Desktop** from nordicsemi.com,
open the **Programmer** app, enter bootloader mode on the dongle, select the dongle,
load the `.zip` firmware file, and click **Write**.

### Step 4 — Install the Wireshark plugin (one command)

```bash
nrfutil ble-sniffer bootstrap
```

This copies the extcap shim executable to Wireshark's personal extcap directory
automatically. No manual file copying needed.

Open Wireshark. The interface list should include:
```
nRF Sniffer for Bluetooth LE [nRF52840 Dongle /dev/cu.usbmodemXXX]
```

If it does not appear: Capture → Refresh Interfaces.

To find Wireshark's extcap directory manually: Help → About Wireshark → Folders tab →
look for **Personal Extcap path**.

**Official documentation:**
- Preparing hardware: `docs.nordicsemi.com/bundle/nrfutil/page/nrfutil-ble-sniffer/guides/requirements.html`
- Quick guide: `docs.nordicsemi.com/bundle/nrfutil/page/nrfutil-ble-sniffer/nrfutil-ble-sniffer.html`
- Running the sniffer: `docs.nordicsemi.com/bundle/nrfutil/page/nrfutil-ble-sniffer/guides/running_sniffer.html`

> That site requires JavaScript — open in a browser, not curl.

### Step 3 — Configure for a specific device

Click the gear icon next to the sniffer interface (or go to Capture → Options):

- **Device** dropdown: start with `All advertising devices` for the first capture
  to confirm you can see the toilet advertising. Once you have the MAC, set this
  to the specific MAC for all subsequent captures — the sniffer will lock onto that
  device and follow whichever connection forms.

The toilet's MAC can be found in the HA integration's device entry, in a previous
btsnoop capture, or by scanning via `tools/aquaclean-connection-test.py`.

### Step 4 — Captures to make (Alba remote displacement investigation)

#### Capture A — Official app session (baseline)
1. Start sniffer, device = Alba MAC
2. Open the Geberit Home app → connect to the Alba → let it complete the init sequence
3. Stop after 30 s. Save as `app-session.pcapng`

#### Capture B — Bridge session
1. Start sniffer, device = Alba MAC
2. Trigger a HACS poll (re-enable the integration, or wait for auto-poll)
3. Stop after 30 s. Save as `bridge-session.pcapng`

#### Capture C — Remote control
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

### Step 5 — Analysis

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

---

## What to Include When Sharing a Capture

When submitting a capture file for analysis (e.g. as a GitHub issue attachment):

1. **iPhone:** attach the `.txt` file from File → Export → Raw Data.
2. **Android:** attach the `.pcapng` file saved from Wireshark.
3. **nRF52840:** attach the `.pcapng` file saved from Wireshark. Include the Alba's MAC address so the correct connection can be identified.
4. Note what action you performed and at what approximate time (used to find the relevant window in the file).
5. Include the device MAC address and model if known.
