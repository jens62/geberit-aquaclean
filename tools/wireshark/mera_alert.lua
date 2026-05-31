-- mera_alert.lua — Wireshark Lua plugin for nRF52840 BLE sniffer captures
--
-- Plays a sound alert when the Geberit toilet is scanned or connected to,
-- so you know when to interact with the app and whether the CONNECT_IND was caught.
--
-- Installation:
--   macOS/Linux:  copy to ~/.config/wireshark/plugins/
--   Windows:      copy to %APPDATA%\Wireshark\plugins\
--   (Create the directory if it does not exist.)
--
--   In Wireshark: Analyze → Reload Lua Plugins (Cmd+Shift+L on macOS)
--
-- Verify: Wireshark status bar or Tools → Lua Console should show:
--   [mera_alert] loaded — watching <MAC>
--
-- Sounds (macOS afplay — remove or adapt os.execute lines on other platforms):
--   Tink  = SCAN_REQ  → phone found the toilet; CONNECT_IND is ~100-300 ms away
--   Ping  = CONNECT_IND caught → sniffer locked to the session; ATT frames will decode
--
-- Capture workflow:
--   1. Start capture, select device MAC in the nRF Sniffer toolbar
--   2. Open the Geberit Home app (do not tap anything yet)
--   3. Hear Tink → app found the toilet
--   4. Hear Ping → CONNECT_IND captured; NOW tap the button in the app
--   5. If Tink but no Ping → sniffer missed CONNECT_IND; close app, wait, try again

local TOILET = "38:ab:41:2a:0d:67"  -- change to your device's BLE MAC (lowercase)

local f_pdu = Field.new("btle.advertising_header.pdu_type")
local tap = Listener.new("frame", "btle")

function tap.packet(pinfo, tvb)
    local pdu = f_pdu()
    if not pdu then return end

    local dst = tostring(pinfo.dst):lower()
    if dst ~= TOILET then return end

    if pdu.value == 3 then      -- SCAN_REQ: connection forming, ~100-300 ms warning
        os.execute("afplay /System/Library/Sounds/Tink.aiff &")
        print("[mera_alert] SCAN_REQ — device found, CONNECT_IND imminent")
    elseif pdu.value == 5 then  -- CONNECT_IND: sniffer locked, ATT frames will decode
        os.execute("afplay /System/Library/Sounds/Ping.aiff &")
        print("[mera_alert] CONNECT_IND captured — session locked, tap the app now!")
    end
end

print("[mera_alert] loaded — watching " .. TOILET)
