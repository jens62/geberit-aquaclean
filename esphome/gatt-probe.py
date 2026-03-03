#!/usr/bin/env python3
"""
Geberit AquaClean GATT Probe via ESPHome Bluetooth Proxy.

Isolates ESP32-C3 GATT notification failures (GitHub issue #11).

Runs four diagnostic steps, each building on the previous:

  Step 1  ESP32 API connect + device info
  Step 2  BLE advertisement scan → confirm Geberit MAC is visible
  Step 3  BLE connect → MTU negotiation + GATT service discovery
  Step 4  Subscribe READ notifications + send GetSystemParameterList
          → confirms the full GATT write + notify pipeline works

ESP32 DEBUG log is streamed throughout so both the Python side and the
ESP32 firmware side are visible simultaneously.

Wire format verified against SILLY log in AquaCleanBaseClient.send_request():
  Write  1104FF00116EE101010D0D080001020304050609  → BULK_WRITE_0
  Notify (CONTROL frame then 4 data frames) ← BULK_READ_0..3

Usage:
  python gatt-probe.py <proxy_host> <ble_mac>
  python gatt-probe.py 192.168.0.160 38:AB:XX:XX:ZZ:67
  python gatt-probe.py 192.168.0.160 38:AB:XX:XX:ZZ:67 --noise-psk "base64key=="

Requires:  pip install aioesphomeapi
"""

import asyncio
import argparse
import re
import sys
import time
from aioesphomeapi import APIClient, LogLevel


# ── Geberit AquaClean GATT characteristic UUIDs ──────────────────────────────
# Source: BluetoothLeConnector.py
SERVICE_UUID = "3334429d-90f3-4c41-a02d-5cb3a03e0000"

# Python writes 20-byte frames to BULK_WRITE_0 (commands to toilet)
WRITE_UUID = "3334429d-90f3-4c41-a02d-5cb3a13e0000"  # BULK_WRITE_0

# Toilet sends 20-byte frame responses as notifications on BULK_READ_0..3
READ_UUIDS = [
    "3334429d-90f3-4c41-a02d-5cb3a53e0000",  # BULK_READ_0  (frame 0 + CONTROL)
    "3334429d-90f3-4c41-a02d-5cb3a63e0000",  # BULK_READ_1  (frame 1)
    "3334429d-90f3-4c41-a02d-5cb3a73e0000",  # BULK_READ_2  (frame 2)
    "3334429d-90f3-4c41-a02d-5cb3a83e0000",  # BULK_READ_3  (frame 3)
]

CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

# Frame type labels: (header_byte >> 5) & 7
_FRAME_TYPES = {0: "SINGLE", 1: "FIRST", 2: "CONS", 3: "CONTROL", 4: "INFO"}


# ── Wire frame builder ────────────────────────────────────────────────────────
# Reconstructed from AquaCleanBaseClient.build_payload() + MessageService.build_message()
# + FrameFactory.BuildSingleFrame(). Verified against SILLY log in AquaCleanBaseClient.py.
#
# Observed wire bytes for GetSystemParameterList([0,1,2,3,4,5,6,9]):
#   1104FF00116EE101010D0D080001020304050609
# This function produces the same format for any parameter list.

def _crc16(data: bytes) -> int:
    """CrcMessage.crc16_calculation() — initial value 4660 (0x1234)."""
    crc = 4660
    for b in data:
        crc = (((crc << 8) & 0xFF00) | ((crc >> 8) & 0x00FF)) ^ (b & 0xFF)
        crc = (crc ^ ((crc & 0xFF) >> 4)) & 0xFFFF
        crc = (crc ^ ((crc << 8) << 4)) & 0xFFFF
        crc = (crc ^ (((crc & 0xFF) << 4) << 1)) & 0xFFFF
    return crc


def build_get_system_params_frame(params: list) -> bytes:
    """
    Build the 20-byte SingleFrame for GetSystemParameterList.

    ApiCallAttribute: context=0x01, procedure=0x0D, node=0x01
    params: list of up to 12 parameter IDs (e.g. [0,1,2,3,4,5,7,9])
    """
    # 1. GetSystemParameterList payload (13 bytes)
    arg_count = min(len(params), 12)
    api_payload = bytearray(13)
    api_payload[0] = arg_count
    for i, p in enumerate(params[:arg_count]):
        api_payload[i + 1] = p

    # 2. build_payload(): node + context + procedure + payload_len + payload (17 bytes)
    body = bytearray(4 + len(api_payload))
    body[0] = 0x01  # node
    body[1] = 0x01  # context
    body[2] = 0x0D  # procedure
    body[3] = len(api_payload)
    body[4:] = api_payload

    # 3. CrcMessage: id=4, segment=0xFF (= build_message_segment_of_type(4, data, 0x00, 0x01))
    #    segment = is_zero-1 + (is_one-1)*16 = -1 + 0 = -1 → 256-1 = 255 = 0xFF
    crc = _crc16(bytes(body))
    crc_msg = bytearray(262)   # matches CrcMessage.serialize() output length
    crc_msg[0] = 4             # message_id
    crc_msg[1] = 0xFF          # message_segment
    crc_msg[2] = len(body) >> 8        # len_hi
    crc_msg[3] = len(body) & 0xFF      # len_lo = 17 = 0x11
    crc_msg[4] = crc >> 8              # crc16_hi
    crc_msg[5] = crc & 0xFF            # crc16_lo
    crc_msg[6:6 + len(body)] = body

    # 4. BuildSingleFrame: header 0x11 + first 19 bytes of crc_msg
    #    SINGLE(0) | HasMessageTypeByte_b4(0x10) | IsSubFrameCount(0x01) = 0x11
    frame = bytearray(20)
    frame[0] = 0x11
    frame[1:20] = crc_msg[:19]
    return bytes(frame)


# ── Helpers ───────────────────────────────────────────────────────────────────

def mac_int_to_str(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))


def make_esp32_log_handler():
    """Return a callback that pretty-prints ESP32 log lines."""
    ansi = re.compile(r'(?:\x1b|\033)\[[0-9;]*m')

    def on_log(entry) -> None:
        try:
            raw = entry.message if hasattr(entry, "message") else str(entry)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            clean = ansi.sub("", raw).rstrip()
            m = re.match(r'^\[([DEWIVT])\]\[([^\]]+?)(?::\d+)?\]:\s*(.+)$', clean)
            if m:
                level_char, component, message = m.groups()
                print(f"  [ESP32:{component}] {level_char}: {message}")
            else:
                print(f"  [ESP32] {clean}")
        except Exception as exc:
            print(f"  [ESP32:?] parse error: {exc}")

    return on_log


def _frame_type_label(header_byte: int) -> str:
    return _FRAME_TYPES.get((header_byte >> 5) & 7, f"type{(header_byte >> 5) & 7}")


# ── Main probe ────────────────────────────────────────────────────────────────

async def run_probe(proxy_host: str, ble_mac: str, noise_psk: str | None) -> None:
    mac_str = ble_mac.upper()
    mac_int = int(ble_mac.replace(":", ""), 16)

    # Track resources for cleanup
    api = None
    log_api = None
    unsub_logs = None
    unsub_adv = None
    cancel_conn = None
    notify_subs = []   # list of (stop_fn, remove_fn)

    print("=" * 70)
    print("Geberit AquaClean GATT Probe")
    print(f"Proxy: {proxy_host}    MAC: {mac_str}")
    print("=" * 70)
    print()

    try:
        # ── Step 1: ESP32 API connect + log streaming ─────────────────────────
        print("STEP 1  ESP32 API connect + log streaming")
        t0 = time.perf_counter()

        log_api = APIClient(address=proxy_host, port=6053, password="", noise_psk=noise_psk)
        await log_api.connect(login=True)
        info = await log_api.device_info()
        feature_flags = getattr(info, "bluetooth_proxy_feature_flags", 0)
        api_ms = int((time.perf_counter() - t0) * 1000)

        print(f"  OK  ({api_ms} ms)")
        print(f"  Device:   {info.name}")
        print(f"  ESPHome:  {info.esphome_version}")
        print(f"  Model:    {info.model}")
        print(f"  BT proxy feature_flags: {feature_flags:#010b}  ({feature_flags:#010x})")
        print()

        unsub_logs = log_api.subscribe_logs(
            make_esp32_log_handler(), log_level=LogLevel.LOG_LEVEL_VERBOSE
        )
        print("  ESP32 VERBOSE log streaming active")
        print()

        # ── Step 2: BLE advertisement scan ───────────────────────────────────
        print("STEP 2  BLE advertisement scan (up to 30 s)")
        print(f"  Looking for {mac_str} …")

        api = APIClient(address=proxy_host, port=6053, password="", noise_psk=noise_psk)
        await api.connect(login=True)

        found_evt = asyncio.Event()
        address_type = 0
        adv_rssi: int | None = None

        def on_adv(resp) -> None:
            nonlocal address_type, adv_rssi
            for adv in resp.advertisements:
                if mac_int_to_str(adv.address) == mac_str:
                    address_type = getattr(adv, "address_type", 0)
                    adv_rssi = getattr(adv, "rssi", None)
                    found_evt.set()

        unsub_adv = api.subscribe_bluetooth_le_raw_advertisements(on_adv)

        try:
            await asyncio.wait_for(found_evt.wait(), timeout=30.0)
            addr_label = "PUBLIC" if address_type == 0 else "RANDOM"
            print(f"  OK  Device advertising  address_type={address_type} ({addr_label})  RSSI={adv_rssi} dBm")
        except asyncio.TimeoutError:
            print(f"  FAIL  Device not seen in 30 s")
            print("        Is the toilet powered on and in BLE range of the ESP32?")
            return
        print()

        # ── Step 3: BLE connect + GATT service discovery ──────────────────────
        print("STEP 3  BLE connect + GATT service discovery")
        t_ble = time.perf_counter()
        connected_fut: asyncio.Future = asyncio.get_running_loop().create_future()
        negotiated_mtu = 0

        def on_ble_state(connected: bool, mtu: int, error: int) -> None:
            nonlocal negotiated_mtu
            if connected_fut.done():
                return
            if error:
                connected_fut.set_exception(Exception(f"BLE error code {error}"))
            elif connected:
                negotiated_mtu = mtu
                connected_fut.set_result(mtu)
            else:
                connected_fut.set_exception(Exception("BLE disconnected during connect"))

        try:
            cancel_conn = await api.bluetooth_device_connect(
                mac_int, on_ble_state,
                address_type=address_type,
                feature_flags=feature_flags,
                has_cache=False,
                disconnect_timeout=10.0,
                timeout=20.0,
            )
            await asyncio.wait_for(connected_fut, timeout=20.0)
            ble_ms = int((time.perf_counter() - t_ble) * 1000)
            print(f"  OK  BLE connected  MTU={negotiated_mtu}  ({ble_ms} ms)")
        except asyncio.TimeoutError:
            print("  FAIL  BLE connect timed out after 20 s")
            return
        except Exception as exc:
            print(f"  FAIL  {exc}")
            return

        # GATT service discovery
        print("  Fetching GATT services …")
        t_svc = time.perf_counter()
        try:
            svc_resp = await asyncio.wait_for(
                api.bluetooth_gatt_get_services(mac_int), timeout=15.0
            )
        except asyncio.TimeoutError:
            print("  FAIL  GATT service discovery timed out after 15 s")
            print("        This is the same symptom as the HACS poll failure.")
            print("        → The GATT pipeline is already broken at service discovery.")
            return
        except Exception as exc:
            print(f"  FAIL  GATT service discovery: {exc}")
            return

        svc_ms = int((time.perf_counter() - t_svc) * 1000)
        uuid_to_handle: dict[str, int] = {}
        cccd_for_handle: dict[int, int] = {}
        found_geberit_service = False

        for svc in svc_resp.services:
            svc_uuid = svc.uuid.lower()
            is_geberit = svc_uuid == SERVICE_UUID
            if is_geberit:
                found_geberit_service = True
            marker = "  ★" if is_geberit else "   "
            print(f"{marker} Service  {svc_uuid}")
            for char in svc.characteristics:
                char_uuid = char.uuid.lower()
                uuid_to_handle[char_uuid] = char.handle
                role = ""
                if char_uuid == WRITE_UUID:
                    role = "  ← WRITE (commands)"
                elif char_uuid in READ_UUIDS:
                    idx = READ_UUIDS.index(char_uuid)
                    role = f"  ← READ_{idx} (notify responses)"
                prop_str = f"props=0x{char.properties:02x}"
                print(f"     Char  0x{char.handle:04x}  {char_uuid}  {prop_str}{role}")
                for desc in char.descriptors:
                    if desc.uuid.lower() == CCCD_UUID:
                        cccd_for_handle[char.handle] = desc.handle
                        print(f"       CCCD  0x{desc.handle:04x}")

        print(f"  Service discovery OK  ({svc_ms} ms)  {len(svc_resp.services)} service(s)")
        if not found_geberit_service:
            print(f"  WARNING  Geberit service {SERVICE_UUID} not found!")
            print("           Service layout unexpected — proceeding anyway")
        print()

        # ── Step 4: Subscribe notifications + send GetSystemParameterList ─────
        print("STEP 4  Subscribe GATT notifications + send GetSystemParameterList")

        # Check required handles
        write_handle = uuid_to_handle.get(WRITE_UUID)
        if write_handle is None:
            print(f"  FAIL  WRITE characteristic not found in service table")
            return

        notifications: list[tuple[float, str, bytes]] = []  # (elapsed_s, uuid, data)
        notify_evt = asyncio.Event()
        t_write_ref = [0.0]   # mutable container so inner function can read after assignment

        # Subscribe to all 4 READ characteristics
        for read_uuid in READ_UUIDS:
            handle = uuid_to_handle.get(read_uuid)
            if handle is None:
                print(f"  SKIP  {read_uuid[-12:]} — not in service table")
                continue

            # Closure: capture read_uuid and handle by value
            def make_notify_handler(uuid: str):
                def on_notify(h: int, data: bytes) -> None:
                    elapsed = time.perf_counter() - t_write_ref[0]
                    ft = _frame_type_label(data[0])
                    print(f"  +{elapsed*1000:6.0f} ms  NOTIFY  {uuid[-12:]}  {data.hex().upper()}  [{ft}]")
                    notifications.append((elapsed, uuid, data))
                    notify_evt.set()
                return on_notify

            try:
                stop_fn, remove_fn = await api.bluetooth_gatt_start_notify(
                    mac_int, handle, make_notify_handler(read_uuid)
                )
                notify_subs.append((stop_fn, remove_fn))

                # Write CCCD to enable notifications (required for BLE proxy V3 connections)
                cccd_handle = cccd_for_handle.get(handle)
                if cccd_handle is not None:
                    await api.bluetooth_gatt_write_descriptor(mac_int, cccd_handle, b"\x01\x00")
                    print(f"  Subscribed  0x{handle:04x}  BULK_READ_{READ_UUIDS.index(read_uuid)}  CCCD=0x{cccd_handle:04x}")
                else:
                    print(f"  Subscribed  0x{handle:04x}  BULK_READ_{READ_UUIDS.index(read_uuid)}  (no CCCD found)")
            except Exception as exc:
                print(f"  WARN  subscribe {read_uuid[-12:]}: {exc}")

        if not notify_subs:
            print("  FAIL  Could not subscribe to any READ characteristic")
            return

        print()
        print("  Watching for spontaneous INFO frames (device sends these on connect) …")
        print("  (waiting 3 s before sending command)")
        await asyncio.sleep(3.0)

        if notifications:
            print(f"  Got {len(notifications)} spontaneous notification(s) before command — GATT notify path is alive")
        else:
            print("  No spontaneous notifications in 3 s (normal if device is idle)")
        print()

        # Build and send GetSystemParameterList([0,1,2,3,4,5,7,9])
        params = [0, 1, 2, 3, 4, 5, 7, 9]
        cmd = build_get_system_params_frame(params)
        print(f"  Sending GetSystemParameterList({params})")
        print(f"  → {cmd.hex().upper()}")
        print(f"  → BULK_WRITE_0 (handle 0x{write_handle:04x})")
        print()

        t_write_ref[0] = time.perf_counter()
        pre_write_count = len(notifications)

        try:
            await asyncio.wait_for(
                api.bluetooth_gatt_write(mac_int, write_handle, cmd, response=True),
                timeout=10.0
            )
            write_ack_ms = int((time.perf_counter() - t_write_ref[0]) * 1000)
            print(f"  Write acknowledged ({write_ack_ms} ms)")
        except asyncio.TimeoutError:
            print("  FAIL  GATT write timed out (no write-response from device in 10 s)")
            print("        The ESP32 accepted the TCP frame but the BLE write never completed.")
            print("        → BLE connection parameter or coexistence scheduler issue on C3.")
            return
        except Exception as exc:
            print(f"  FAIL  GATT write: {exc}")
            return

        # Wait up to 10 s for response notifications
        print(f"  Waiting up to 10 s for response notifications …")
        deadline = time.perf_counter() + 10.0
        while time.perf_counter() < deadline:
            notify_evt.clear()
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(notify_evt.wait(), timeout=min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass

        post_write_notifications = [n for n in notifications if n[0] >= 0]

        # ── Summary ───────────────────────────────────────────────────────────
        print()
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"  Step 1  API connect:            {api_ms} ms  OK")
        print(f"  Step 2  BLE advertisement:       RSSI={adv_rssi} dBm  OK")
        print(f"  Step 3  BLE connect + svc disc:  MTU={negotiated_mtu}  {ble_ms} ms + {svc_ms} ms  OK")

        new_notifs = [n for n in notifications[pre_write_count:] if True]
        if new_notifs:
            first_ms = int(new_notifs[0][0] * 1000)
            print(f"  Step 4  GATT notify:             {len(new_notifs)} notification(s) received")
            print(f"          First notification:       {first_ms} ms after write")
            print()
            for elapsed, uuid, data in new_notifs:
                ft = _frame_type_label(data[0])
                print(f"    +{elapsed*1000:5.0f} ms  {uuid[-12:]}  {data.hex().upper()}  [{ft}]")
            print()
            print("  RESULT  OK — Full GATT write + notify pipeline is working.")
            print("          The ESP32 and Geberit device exchanged data successfully.")
            print("          If HACS still fails, the issue is at a higher protocol layer")
            print("          (frame assembly, CRC, or timeout in the coordinator).")
        else:
            print(f"  Step 4  GATT notify:             FAIL — 0 notifications in 10 s after write")
            print()
            print("  RESULT  FAIL — GATT notify pipeline is broken.")
            print()
            print("  Likely causes (check ESP32 DEBUG log above for confirmation):")
            print("  1. ESP32-C3 BLE/WiFi coexistence: single-core scheduler not giving")
            print("     enough radio time for GATT notifications after write completes.")
            print("     Fix: try sdkconfig CONFIG_SW_COEXIST_ENABLE or reduce scan window.")
            print("  2. Geberit device not responding: check if 'Write' log appears on ESP32")
            print("     side but no subsequent 'Notify' arrives from the peripheral.")
            print("  3. CCCD not accepted: device ignores CCCD write (rare, check ESP32 log).")
        print()

    finally:
        # ── Cleanup ───────────────────────────────────────────────────────────
        # Order: remove notify handlers → BLE disconnect → unsub adv → API disconnect
        for _stop_fn, remove_fn in notify_subs:
            try:
                remove_fn()
            except Exception:
                pass

        if api is not None:
            try:
                await api.bluetooth_device_disconnect(mac_int)
            except Exception:
                pass
            if cancel_conn is not None:
                try:
                    cancel_conn()
                except Exception:
                    pass
            if unsub_adv is not None:
                try:
                    unsub_adv()
                except Exception:
                    pass
            try:
                await api.disconnect()
            except Exception:
                pass

        if log_api is not None:
            if unsub_logs is not None:
                try:
                    unsub_logs()
                except Exception:
                    pass
            try:
                await log_api.disconnect()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("proxy_host", help="ESP32 IP or hostname (e.g. 192.168.0.160)")
    parser.add_argument("ble_mac",    help="Geberit BLE MAC  (e.g. 38:AB:XX:XX:ZZ:67)")
    parser.add_argument(
        "--noise-psk", default=None, dest="noise_psk",
        help="base64 encryption key (if api encryption is enabled in ESPHome YAML)",
    )
    args = parser.parse_args()
    asyncio.run(run_probe(args.proxy_host, args.ble_mac.upper(), args.noise_psk))


if __name__ == "__main__":
    main()
