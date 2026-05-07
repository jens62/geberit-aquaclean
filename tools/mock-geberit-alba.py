#!/usr/bin/env python3
"""
Mock BLE peripheral using bluez_peripheral on Linux (BlueZ).

Two modes:
  --mode unsupported  (default)
      Advertises the Alba GATT profile but never responds to any frame.
      Use this to test the unsupported-device detection in the HACS config flow.

  --mode handshake
      Implements the full server-side Arendi Security handshake + one encrypted
      frame exchange.  Use this to test that AriendiSecurity.py (bridge side)
      can complete the handshake against a live BLE peer and exchange encrypted
      Geberit frames.

Requirements:
  - Linux with BlueZ (Experimental=true may be required)
  - Python packages: dbus-next, bluez_peripheral
  - Sufficient D-Bus privileges (run as root or with appropriate group membership)
  - BlueZ experimental features may be required (`Experimental=true` in
    /etc/bluetooth/main.conf)

Quick start:
  sudo /home/jens/venv/bin/python ./mock-geberit-alba.py --mode handshake
"""

import argparse
import asyncio
import inspect
import os
import pathlib
import sys

from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant
from bluez_peripheral.gatt.service import Service
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.util import Adapter

# --- Import Arendi Security crypto from the bridge package -------------------
# Adds the repo root to sys.path so we can import from aquaclean_console_app.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from aquaclean_console_app.bluetooth_le.LE.AriendiSecurity import (
    _crc16_kermit, _cobs_encode, _cobs_decode,
    _hkdf, _aes_cmac, _AesCtrState,
    aquacleanBridgeId,
    _SEC_VERSION_REQ, _SEC_VERSION_RESP,
    _SEC_EP_REQ,      _SEC_EP_RESP,
    _SEC_KE_REQ,      _SEC_KE_RESP,
    _SEC_ENCRYPTED,
    _HDLC_SABM_TYPE,  _HDLC_UA_TYPE,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey,
)


# ---------------------------------------------------------------------------
# Device-side Arendi Security implementation
# ---------------------------------------------------------------------------

class _AriendiServerSide:
    """
    Server (device) role for the Arendi Security handshake + encrypted data.

    Call flow:
      1. feed(att_bytes)  — call from BLE write handler for every incoming ATT PDU
      2. await run(send_fn) — drives the handshake then loops on encrypted frames
         send_fn: async callable(att_bytes: bytes) — sends a BLE notification
    """

    def __init__(self):
        self._rx_buf  = bytearray()
        self._rx_queue: asyncio.Queue = asyncio.Queue()
        self._tx_seq  = 0
        self._rx_ack  = 0
        self._tx_cipher: _AesCtrState | None = None
        self._rx_cipher: _AesCtrState | None = None
        self.handshake_done = False

    # -----------------------------------------------------------------------
    # Incoming ATT byte feeding — same COBS/CRC/HDLC parser as AriendiSecurity
    # -----------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        self._rx_buf.extend(data)
        self._process_rx_buf()

    def _process_rx_buf(self) -> None:
        while True:
            buf = self._rx_buf
            if not buf or buf[0] != 0:
                idx = buf.find(b'\x00')
                if idx == -1:
                    self._rx_buf = bytearray()
                    return
                self._rx_buf = buf[idx:]
                buf = self._rx_buf
            end = buf.find(b'\x00', 1)
            if end == -1:
                return
            frame_bytes = bytes(buf[1:end])
            self._rx_buf = buf[end:]
            if not frame_bytes:
                continue
            try:
                decoded = _cobs_decode(frame_bytes)
            except ValueError:
                continue
            if len(decoded) < 3:
                continue
            crc_recv = decoded[-2] | (decoded[-1] << 8)
            crc_calc = _crc16_kermit(decoded[:-2])
            if crc_recv != crc_calc:
                print(f"[MockServer] CRC mismatch rx=0x{crc_recv:04X} calc=0x{crc_calc:04X} — frame dropped")
                continue
            ctrl    = decoded[0]
            payload = decoded[1:-2]
            if (ctrl & 0x01) == 0:        # I-frame
                peer_ns = (ctrl >> 1) & 0x07
                self._rx_ack = (peer_ns + 1) % 8
                self._rx_queue.put_nowait(('I', ctrl, payload))
            elif (ctrl & 0x03) == 0x03:   # U-frame
                self._rx_queue.put_nowait(('U', ctrl, payload))
            # S-frames: discard (no state needed for this simple server)

    # -----------------------------------------------------------------------
    # Frame builders — mirrors of AriendiSecurity
    # -----------------------------------------------------------------------

    @staticmethod
    def _u_ctrl(type_code: int) -> int:
        return ((type_code << 3) & 0xE0) | ((type_code << 2) & 0x0C) | 0x03

    def _i_ctrl(self) -> int:
        return ((self._rx_ack << 5) & 0xE0) | ((self._tx_seq << 1) & 0x0E)

    def _build_att(self, ctrl: int, payload: bytes) -> bytes:
        raw = bytes([ctrl]) + payload
        crc = _crc16_kermit(raw)
        return b'\x00' + _cobs_encode(raw + bytes([crc & 0xFF, (crc >> 8) & 0xFF])) + b'\x00'

    def _att_u(self, hdlc_type: int) -> bytes:
        return self._build_att(self._u_ctrl(hdlc_type), b'')

    def _att_i(self, sec_payload: bytes) -> bytes:
        att = self._build_att(self._i_ctrl(), sec_payload)
        self._tx_seq = (self._tx_seq + 1) % 8
        return att

    # -----------------------------------------------------------------------
    # Await helpers
    # -----------------------------------------------------------------------

    async def _await_u(self, expected_ctrl: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"[MockServer] timeout waiting for U ctrl=0x{expected_ctrl:02X}")
            ft, ctrl, _ = await asyncio.wait_for(self._rx_queue.get(), timeout=remaining)
            if ft == 'U' and ctrl == expected_ctrl:
                return

    async def _await_i(self, expected_type: int, timeout: float = 5.0) -> bytes:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"[MockServer] timeout waiting for I type=0x{expected_type:02X}")
            ft, ctrl, payload = await asyncio.wait_for(self._rx_queue.get(), timeout=remaining)
            if ft == 'I' and payload and payload[0] == expected_type:
                return payload

    # -----------------------------------------------------------------------
    # Handshake + encrypted data exchange
    # -----------------------------------------------------------------------

    async def run(self, send_fn) -> None:
        """
        Run the full server-side handshake, then loop on incoming encrypted frames.

        send_fn: async callable(att_bytes: bytes) — BLE notification sender.
        """
        print("[MockServer] waiting for SABM...")

        # 1. SABM → UA
        await self._await_u(self._u_ctrl(_HDLC_SABM_TYPE))
        print("[MockServer] ← SABM")
        await send_fn(self._att_u(_HDLC_UA_TYPE))
        print("[MockServer] → UA")

        # 2. VERSION_REQ → VERSION_RESP
        await self._await_i(_SEC_VERSION_REQ)
        print("[MockServer] ← VERSION_REQ")
        # 7 bytes: [type=0x01][0x00 × 5][proto_ver_minus_1=0x01] → proto v2
        await send_fn(self._att_i(bytes([_SEC_VERSION_RESP, 0, 0, 0, 0, 0, 1])))
        print("[MockServer] → VERSION_RESP (proto v2)")

        # 3. EP_REQ → EP_RESP
        await self._await_i(_SEC_EP_REQ)
        print("[MockServer] ← EP_REQ")
        nonce1 = os.urandom(16)
        nonce2 = os.urandom(16)
        ep_resp = bytes([_SEC_EP_RESP]) + nonce1 + nonce2 + bytes([0x01])
        await send_fn(self._att_i(ep_resp))
        print(f"[MockServer] → EP_RESP  nonce1={nonce1.hex()}  nonce2={nonce2.hex()}")

        # 4. KE_REQ → verify client CMAC, generate server keypair, KE_RESP
        ke = await self._await_i(_SEC_KE_REQ)
        print("[MockServer] ← KE_REQ")
        if len(ke) < 49:
            raise ValueError(f"[MockServer] KE_REQ too short ({len(ke)} bytes)")
        client_public_bytes = ke[1:33]
        client_cmac_bytes   = ke[33:49]

        auth_key = _hkdf(ikm=aquacleanBridgeId, salt=nonce1, length=16)
        expected_cmac = _aes_cmac(auth_key, client_public_bytes)
        if client_cmac_bytes != expected_cmac:
            raise ValueError("[MockServer] client CMAC verification FAILED — wrong aquacleanBridgeId?")
        print("[MockServer] client CMAC verified ✓")

        server_priv        = X25519PrivateKey.generate()
        server_public_bytes = server_priv.public_key().public_bytes_raw()
        server_cmac        = _aes_cmac(auth_key, server_public_bytes)

        client_pub_key = X25519PublicKey.from_public_bytes(client_public_bytes)
        shared_secret  = server_priv.exchange(client_pub_key)
        key_material   = _hkdf(ikm=shared_secret, salt=nonce1, length=32)

        # key_material[0:16]  = client rx key  = server tx key  (server encrypts outgoing)
        # key_material[16:32] = client tx key  = server rx key  (server decrypts incoming)
        self._tx_cipher = _AesCtrState(key_material[0:16],  nonce2)
        self._rx_cipher = _AesCtrState(key_material[16:32], nonce2)

        ke_resp = bytes([_SEC_KE_RESP]) + server_public_bytes + server_cmac
        await send_fn(self._att_i(ke_resp))
        print(f"[MockServer] → KE_RESP  server_pub={server_public_bytes.hex()[:16]}...")

        self.handshake_done = True
        print("[MockServer] *** HANDSHAKE COMPLETE — session keys established ***")

        # 5. Loop on incoming encrypted frames
        # The first item in the queue may be a S-RR ACK (ft='S') or the first data frame.
        while True:
            try:
                ft, ctrl, payload = await asyncio.wait_for(
                    self._rx_queue.get(), timeout=15.0
                )
            except asyncio.TimeoutError:
                print("[MockServer] no more frames after 15 s — exiting frame loop")
                return

            if ft != 'I':
                continue  # S-frame ACK or stray U-frame
            if not payload or payload[0] != _SEC_ENCRYPTED:
                sec_str = f"0x{payload[0]:02X}" if payload else "0x??"
                print(f"[MockServer] unexpected I-frame sec_type={sec_str} — ignored")
                continue

            plaintext = self._rx_cipher.process(payload[1:])
            print(f"[MockServer] ← encrypted frame DECRYPTED: {plaintext.hex()}")

            # Send back a fake Geberit GetDeviceIdentification OK response.
            # Format: SINGLE frame (0x24), counter=0, context=0x00, proc=0x82,
            #         status=OK (0x00), then "AcAlba" ASCII padded to 20 bytes.
            fake_resp = bytes([
                0x24, 0x00, 0x00,           # SINGLE frame header, counter=0
                0x00, 0x82, 0x00,           # ctx=0x00, proc=GetDeviceIdentification, OK
                0x41, 0x63, 0x41, 0x6C,     # "AcAl"
                0x62, 0x61, 0x00, 0x00,     # "ba\x00\x00"
                0x00, 0x00, 0x00, 0x00,     # padding
                0x00, 0x00,                 # padding (total = 20 bytes)
            ])
            encrypted_resp = self._tx_cipher.process(fake_resp)
            await send_fn(self._att_i(bytes([_SEC_ENCRYPTED]) + encrypted_resp))
            print("[MockServer] → fake GetDeviceIdentification response (encrypted)")


# ---------------------------------------------------------------------------
# GATT services
# ---------------------------------------------------------------------------

class GeberitServiceA(Service):
    def __init__(self):
        super().__init__("559eb100-2390-11e8-b467-0ed5f89f718b", True)

    @characteristic("559eb101-2390-11e8-b467-0ed5f89f718b", CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_char(self, options):
        pass

    @write_char.setter
    def write_char(self, value, options):
        print(f"Geberit A [Write]: {bytes(value).hex()}")

    @characteristic("559eb110-2390-11e8-b467-0ed5f89f718b", CharFlags.READ)
    async def read_char(self, options):
        return b"Geberit-Mock"


class BtSigDataService(Service):
    def __init__(self, mode: str = "unsupported"):
        super().__init__("0000fd48-0000-1000-8000-00805f9b34fb", True)
        self._mode = mode
        self._notify_value = bytes([0])
        self._arendi = _AriendiServerSide() if mode == "handshake" else None
        # Populated after register() to enable pushing notifications.
        # bluez_peripheral stores characteristic _Characteristic objects in
        # Service._chars in declaration order: [0]=sig_write, [1]=sig_notify.
        # Each _Characteristic IS a dbus-next ServiceInterface; calling
        # emit_properties_changed({'Value': Variant('ay', data)}) on it causes
        # BlueZ to send a BLE ATT Handle Value Notification to subscribed clients.
        self._notify_char_iface = None

    def set_notify_char_iface(self, iface):
        """Called by main() after register() to wire up the notification sender."""
        self._notify_char_iface = iface

    async def send_notify(self, att_bytes: bytes) -> None:
        """Push ATT bytes to the connected BLE client via NOTIFY."""
        self._notify_value = att_bytes
        if self._notify_char_iface is not None:
            if hasattr(self._notify_char_iface, 'changed'):
                self._notify_char_iface.changed(att_bytes)
            else:
                self._notify_char_iface.emit_properties_changed(
                    {'Value': Variant('ay', list(att_bytes))}
                )
        else:
            print("[MockServer] WARNING: notify char interface not set — cannot send notification")

    @characteristic("559eb001-2390-11e8-b467-0ed5f89f718b", CharFlags.WRITE_WITHOUT_RESPONSE)
    def sig_write(self, options):
        pass

    @sig_write.setter
    def sig_write(self, value, options):
        data = bytes(value)
        if self._mode == "handshake" and self._arendi is not None:
            self._arendi.feed(data)
        else:
            print(f"Data Channel [Write]: {data.hex()}")

    @characteristic("559eb002-2390-11e8-b467-0ed5f89f718b", CharFlags.NOTIFY)
    def sig_notify(self, options):
        return self._notify_value


class DeviceInformationService(Service):
    """Standard BLE Device Information Service (0x180a) with real Alba values from kstr's device.

    Source: local-assets/Android-BLE-Logs/kstr/aquaclean2.log (E4:85:01:CD:6B:04)
    Note: model_number is the SAP article number, NOT the Geberit product name ("AcAlba").
    "AcAlba" is only available via proc 0x82 GetDeviceIdentification (application layer).
    """

    def __init__(self):
        super().__init__("0000180a-0000-1000-8000-00805f9b34fb", True)

    @characteristic("00002a29-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def manufacturer_name(self, options):
        return b"Geberit"

    @characteristic("00002a24-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def model_number(self, options):
        return b"828.860.00.A"

    @characteristic("00002a25-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def serial_number(self, options):
        return b"93136"

    @characteristic("00002a26-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def firmware_revision(self, options):
        return b"RS03TS89"

    @characteristic("00002a27-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def hardware_revision(self, options):
        return b"00"

    @characteristic("00002a28-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def software_revision(self, options):
        return b"1.14.1 1.2.0"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def find_first_adapter_path_and_address(bus):
    """Use ObjectManager to find the first Adapter1 path and Address."""
    introspection = await bus.introspect('org.bluez', '/')
    proxy = bus.get_proxy_object('org.bluez', '/', introspection)
    objmgr = proxy.get_interface('org.freedesktop.DBus.ObjectManager')
    managed = await objmgr.call_get_managed_objects()
    for path, ifaces in managed.items():
        if 'org.bluez.Adapter1' in ifaces:
            adapter_props = ifaces['org.bluez.Adapter1']
            addr = adapter_props.get('Address')
            if isinstance(addr, Variant):
                addr = addr.value
            return path, addr, objmgr
    return None, None, objmgr


async def safe_call(obj, method_name, *args, **kwargs):
    fn = getattr(obj, method_name, None)
    if not fn:
        return False
    try:
        if inspect.iscoroutinefunction(fn):
            await fn(*args, **kwargs)
        else:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
        return True
    except Exception as e:
        print(f"Cleanup: calling {method_name} raised: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(mode: str):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    adapter_wrapper = await Adapter.get_first(bus)
    if adapter_wrapper:
        print("Adapter wrapper obtained from bluez_peripheral.")
    else:
        print("No Bluetooth adapter wrapper found via bluez_peripheral.Adapter.get_first()")

    objmgr = None
    try:
        adapter_path, adapter_address, objmgr = await find_first_adapter_path_and_address(bus)
        print("Adapter DBus path:", adapter_path)
        print("Adapter BLE address:", adapter_address)
    except Exception as e:
        print("Could not read adapter path/address via ObjectManager:", e)
        adapter_path = None
        adapter_address = None

    if not adapter_wrapper:
        print("No adapter wrapper available; cannot register GATT services/advertisement.")
        await bus.disconnect()
        return

    if objmgr is not None:
        def on_device_connected(path, interfaces):
            if 'org.bluez.Device1' in interfaces:
                addr = interfaces['org.bluez.Device1'].get('Address')
                if isinstance(addr, Variant):
                    addr = addr.value
                print(f"[Mock] BLE client connected:    {addr or path}")

        def on_device_disconnected(path, interfaces):
            if 'org.bluez.Device1' in interfaces:
                print(f"[Mock] BLE client disconnected: {path}")

        objmgr.on_interfaces_added(on_device_connected)
        objmgr.on_interfaces_removed(on_device_disconnected)

    geb_service = GeberitServiceA()
    sig_service = BtSigDataService(mode=mode)
    dis_service = DeviceInformationService()

    try:
        await geb_service.register(bus, "/org/bluez/example/geberit", adapter_wrapper)
        await sig_service.register(bus, "/org/bluez/example/sigdata", adapter_wrapper)
        await dis_service.register(bus, "/org/bluez/example/dis", adapter_wrapper)
    except Exception as e:
        print("Service registration failed:", e)

    # Wire up the notify characteristic interface so send_notify() can push frames.
    # Search whichever attribute holds characteristics (_characteristics in newer
    # bluez_peripheral, _chars in older versions) and find the NOTIFY char by flags
    # rather than by index — the order differs across library versions.
    notify_char = None
    for attr in ('_characteristics', '_chars'):
        chars = getattr(sig_service, attr, None)
        if chars:
            for c in chars:
                if hasattr(c, 'flags') and CharFlags.NOTIFY in c.flags:
                    notify_char = c
                    break
        if notify_char:
            break
    if notify_char is not None:
        sig_service.set_notify_char_iface(notify_char)
        print("Notify characteristic interface wired.")
    else:
        print("WARNING: could not find notify characteristic — notifications disabled")

    adv = Advertisement(
        "Geberit-Alba-Mock",
        [
            "559eb100-2390-11e8-b467-0ed5f89f718b",
            "0000fd48-0000-1000-8000-00805f9b34fb"
        ],
        appearance=0,
        timeout=0
    )

    adv_registered = False
    try:
        await adv.register(bus, adapter_wrapper)
        adv_registered = True
    except Exception as e:
        print("Advertisement registration failed:", e)

    print(f"--- Mock Device Active (mode={mode}) ---")
    print("Advertising as: Geberit-Alba-Mock")

    server_task = None
    if mode == "handshake":
        print("Waiting for bridge to connect and start handshake...")
        server_task = asyncio.create_task(
            sig_service._arendi.run(sig_service.send_notify)
        )

    stop_event = asyncio.Event()
    try:
        if server_task:
            done, _ = await asyncio.wait(
                [server_task, asyncio.ensure_future(stop_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if server_task in done:
                exc = server_task.exception()
                if exc:
                    print(f"[MockServer] ERROR: {exc}")
                else:
                    print("[MockServer] session complete")
                print("Press Ctrl-C to exit.")
                await stop_event.wait()
        else:
            await stop_event.wait()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down...")
        if server_task and not server_task.done():
            server_task.cancel()
        if adv_registered:
            await safe_call(adv, "unregister", bus, adapter_wrapper)
            await safe_call(adv, "unregister", adapter_wrapper)
            await safe_call(adv, "unregister", bus)
            await safe_call(adv, "unregister")
        await safe_call(geb_service, "unregister")
        await safe_call(sig_service, "unregister")
        await safe_call(dis_service, "unregister")
        try:
            result = bus.disconnect()
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            print("Error disconnecting bus:", e)
        print("Cleanup complete. Exiting.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mock Geberit AquaClean Alba BLE peripheral (Linux/BlueZ).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes
-----
  --mode unsupported  (default)
      Advertises the Alba GATT profile but never responds to any write.
      Use this to test the HACS unsupported-device detection screen
      (HA shows the device UUID details instead of a generic error).

  --mode handshake
      Implements the full server-side Arendi Security handshake:
        SABM / UA / VERSION_REQ-RESP / EP_REQ-RESP / KE_REQ-RESP
      then loops on incoming encrypted frames and sends back a fake
      GetDeviceIdentification response.

      Use this to verify that AriendiSecurity.py (bridge side) can:
        1. Complete the handshake against a live BLE peer
        2. Encrypt outgoing Geberit frames correctly
        3. Decrypt incoming encrypted frames correctly

      Expected output when handshake succeeds:
        [MockServer] ← SABM
        [MockServer] → UA
        [MockServer] ← VERSION_REQ
        [MockServer] → VERSION_RESP (proto v2)
        [MockServer] ← EP_REQ
        [MockServer] → EP_RESP  nonce1=...  nonce2=...
        [MockServer] ← KE_REQ
        [MockServer] client CMAC verified ✓
        [MockServer] → KE_RESP  server_pub=...
        [MockServer] *** HANDSHAKE COMPLETE — session keys established ***
        [MockServer] ← encrypted frame DECRYPTED: <hex of Geberit request>
        [MockServer] → fake GetDeviceIdentification response (encrypted)

Unsupported-device detection test (--mode unsupported)
------------------------------------------------------
Prerequisites (free the ESP32 BLE subscription slot):
  1. In HA: Settings → Integrations → aquaclean-proxy → Disable
  2. In HA: Geberit AquaClean → Configure → set Poll Interval to 300 s

Test sequence:
  3. sudo python mock-geberit-alba.py
     Wait for '--- Mock Device Active ---'
  4. HA: Settings → Integrations → 'Add entry' → Geberit AquaClean
     → MAC: <adapter address from step 3>
     → ESPHome host: <ESP32 IP>
     Expected: unsupported-device abort screen with GATT UUIDs
""",
    )
    parser.add_argument(
        "--mode",
        choices=["unsupported", "handshake"],
        default="unsupported",
        help="unsupported: no responses (HACS detection test); "
             "handshake: full Arendi Security server (decryption test)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.mode))
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")
