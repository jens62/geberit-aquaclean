#!/usr/bin/env python3
"""
In-process unit test for AriendiSecurity — no BLE hardware required.

Tests:
  1. Handshake completes: both client and server reach handshake_done = True
  2. Client → Server encryption: server decrypts client payload correctly
  3. Server → Client encryption: client decrypts server payload correctly
  4. CRC: tampered frame is dropped (not delivered as a valid frame)
  5. CMAC: wrong aquacleanBridgeId causes KE_REQ CMAC verification to fail

Run:
  /Users/jens/venv/bin/python tests/test_arendi_security.py
  # or with pytest:
  /Users/jens/venv/bin/python -m pytest tests/test_arendi_security.py -v
"""

import asyncio
import os
import sys
import pathlib

# Add repo root so imports work from any working directory.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

from aquaclean_console_app.bluetooth_le.LE.AriendiSecurity import (
    AriendiSecurity,
    _crc16_kermit, _cobs_encode, _cobs_decode,
    _hkdf, _aes_cmac, _AesCtrState,
    aquacleanBridgeId,
    _SEC_VERSION_REQ, _SEC_VERSION_RESP,
    _SEC_EP_REQ,      _SEC_EP_RESP,
    _SEC_KE_REQ,      _SEC_KE_RESP,
    _SEC_ENCRYPTED,
    _HDLC_SABM_TYPE,  _HDLC_UA_TYPE,
)


# ---------------------------------------------------------------------------
# Server-side implementation (mirrors mock-geberit-alba.py _AriendiServerSide)
# ---------------------------------------------------------------------------

class _ServerSide:
    """
    Device-role Arendi Security implementation for in-process testing.

    Identical logic to _AriendiServerSide in mock-geberit-alba.py, without
    any BLE / D-Bus dependency.
    """

    def __init__(self):
        self._rx_buf   = bytearray()
        self._rx_queue: asyncio.Queue = asyncio.Queue()
        self._tx_seq   = 0
        self._rx_ack   = 0
        self._tx_cipher: _AesCtrState | None = None
        self._rx_cipher: _AesCtrState | None = None
        self.handshake_done = False

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
            if crc_recv != _crc16_kermit(decoded[:-2]):
                continue
            ctrl    = decoded[0]
            payload = decoded[1:-2]
            if (ctrl & 0x01) == 0:
                peer_ns = (ctrl >> 1) & 0x07
                self._rx_ack = (peer_ns + 1) % 8
                self._rx_queue.put_nowait(('I', ctrl, payload))
            elif (ctrl & 0x03) == 0x03:
                self._rx_queue.put_nowait(('U', ctrl, payload))

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

    async def _await_u(self, expected_ctrl: int, timeout: float = 2.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Server: timeout U ctrl=0x{expected_ctrl:02X}")
            ft, ctrl, _ = await asyncio.wait_for(self._rx_queue.get(), timeout=remaining)
            if ft == 'U' and ctrl == expected_ctrl:
                return

    async def _await_i(self, expected_type: int, timeout: float = 2.0) -> bytes:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Server: timeout I type=0x{expected_type:02X}")
            ft, ctrl, payload = await asyncio.wait_for(self._rx_queue.get(), timeout=remaining)
            if ft == 'I' and payload and payload[0] == expected_type:
                return payload

    async def run_handshake(self, send_fn, nonce1: bytes | None = None,
                             nonce2: bytes | None = None) -> None:
        if nonce1 is None:
            nonce1 = os.urandom(16)
        if nonce2 is None:
            nonce2 = os.urandom(16)

        await self._await_u(self._u_ctrl(_HDLC_SABM_TYPE))
        await send_fn(self._att_u(_HDLC_UA_TYPE))

        await self._await_i(_SEC_VERSION_REQ)
        await send_fn(self._att_i(bytes([_SEC_VERSION_RESP, 0, 0, 0, 0, 0, 1])))

        await self._await_i(_SEC_EP_REQ)
        await send_fn(self._att_i(bytes([_SEC_EP_RESP]) + nonce1 + nonce2 + bytes([0x01])))

        ke = await self._await_i(_SEC_KE_REQ)
        client_public_bytes = ke[1:33]
        client_cmac_bytes   = ke[33:49]

        auth_key = _hkdf(ikm=aquacleanBridgeId, salt=nonce1, length=16)
        expected_cmac = _aes_cmac(auth_key, client_public_bytes)
        if client_cmac_bytes != expected_cmac:
            raise ValueError("Server: client CMAC verification FAILED")

        server_priv         = X25519PrivateKey.generate()
        server_public_bytes = server_priv.public_key().public_bytes_raw()
        server_cmac         = _aes_cmac(auth_key, server_public_bytes)

        client_pub_key = X25519PublicKey.from_public_bytes(client_public_bytes)
        shared_secret  = server_priv.exchange(client_pub_key)
        key_material   = _hkdf(ikm=shared_secret, salt=nonce1, length=32)

        self._tx_cipher = _AesCtrState(key_material[0:16],  nonce2)
        self._rx_cipher = _AesCtrState(key_material[16:32], nonce2)

        await send_fn(self._att_i(bytes([_SEC_KE_RESP]) + server_public_bytes + server_cmac))
        self.handshake_done = True

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._att_i(bytes([_SEC_ENCRYPTED]) + self._tx_cipher.process(plaintext))

    def decrypt_next(self, att_bytes: bytes) -> bytes | None:
        """Feed att_bytes; return decrypted plaintext if an encrypted I-frame was received."""
        self.feed(att_bytes)
        while not self._rx_queue.empty():
            ft, ctrl, payload = self._rx_queue.get_nowait()
            if ft == 'I' and payload and payload[0] == _SEC_ENCRYPTED:
                return self._rx_cipher.process(payload[1:])
        return None


# ---------------------------------------------------------------------------
# Pipe helpers
# ---------------------------------------------------------------------------

def _make_pipe(client: AriendiSecurity, server: _ServerSide):
    """
    Returns (client_send, server_send) async callables.

    client_send(att) — deliver ATT bytes from client to server
    server_send(att) — deliver ATT bytes from server to client (via feed_att_bytes)
    """
    async def client_send(att: bytes) -> None:
        server.feed(att)

    async def server_send(att: bytes) -> None:
        client.feed_att_bytes(att)

    return client_send, server_send


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_handshake_completes():
    """Both sides report handshake_done = True after the exchange."""
    client = AriendiSecurity()
    server = _ServerSide()
    c_send, s_send = _make_pipe(client, server)

    server_task = asyncio.create_task(server.run_handshake(s_send))
    await client.perform_handshake(c_send)
    await server_task

    assert client.handshake_done, "client.handshake_done should be True"
    assert server.handshake_done, "server.handshake_done should be True"
    print("PASS test_handshake_completes")


async def test_client_to_server_encryption():
    """Client encrypts a payload; server decrypts and gets original plaintext."""
    client = AriendiSecurity()
    server = _ServerSide()
    c_send, s_send = _make_pipe(client, server)

    server_task = asyncio.create_task(server.run_handshake(s_send))
    await client.perform_handshake(c_send)
    await server_task

    plaintext = b'\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0A\x0B\x0C\x0D\x0E\x0F\x10'

    encrypted_att = client.wrap_for_send(plaintext)
    decrypted = server.decrypt_next(encrypted_att)

    assert decrypted is not None, "server received no encrypted I-frame"
    assert decrypted == plaintext, f"decrypt mismatch: {decrypted.hex()} != {plaintext.hex()}"
    print("PASS test_client_to_server_encryption")


async def test_server_to_client_encryption():
    """Server encrypts a payload; client decrypts and gets original plaintext."""
    client = AriendiSecurity()
    server = _ServerSide()
    c_send, s_send = _make_pipe(client, server)

    server_task = asyncio.create_task(server.run_handshake(s_send))
    await client.perform_handshake(c_send)
    await server_task

    plaintext = b'\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE\x11\x22\x33\x44\x55\x66\x77\x88'

    server_att = server.encrypt(plaintext)
    decrypted_list = client.feed_att_bytes(server_att)

    assert len(decrypted_list) == 1, f"expected 1 decrypted frame, got {len(decrypted_list)}"
    assert decrypted_list[0] == plaintext, \
        f"decrypt mismatch: {decrypted_list[0].hex()} != {plaintext.hex()}"
    print("PASS test_server_to_client_encryption")


async def test_round_trip_multiple_frames():
    """Multiple frames in each direction are all decrypted correctly."""
    client = AriendiSecurity()
    server = _ServerSide()
    c_send, s_send = _make_pipe(client, server)

    server_task = asyncio.create_task(server.run_handshake(s_send))
    await client.perform_handshake(c_send)
    await server_task

    payloads = [os.urandom(20) for _ in range(5)]

    for payload in payloads:
        encrypted_att = client.wrap_for_send(payload)
        decrypted = server.decrypt_next(encrypted_att)
        assert decrypted == payload, f"C→S mismatch frame: {decrypted!r} != {payload!r}"

    for payload in payloads:
        server_att = server.encrypt(payload)
        result = client.feed_att_bytes(server_att)
        assert len(result) == 1 and result[0] == payload, \
            f"S→C mismatch frame: {result!r} != {payload!r}"

    print("PASS test_round_trip_multiple_frames")


async def test_tampered_frame_dropped():
    """A frame with a corrupted CRC byte is silently dropped, not delivered."""
    client = AriendiSecurity()
    server = _ServerSide()
    c_send, s_send = _make_pipe(client, server)

    server_task = asyncio.create_task(server.run_handshake(s_send))
    await client.perform_handshake(c_send)
    await server_task

    plaintext = b'\xAA\xBB\xCC\xDD'
    good_att = client.wrap_for_send(plaintext)

    # Flip a byte in the middle of the frame (inside the COBS payload).
    # The CRC check must reject it.
    tampered = bytearray(good_att)
    mid = len(tampered) // 2
    tampered[mid] ^= 0xFF
    tampered = bytes(tampered)

    decrypted = server.decrypt_next(tampered)
    assert decrypted is None, "tampered frame should have been dropped"
    print("PASS test_tampered_frame_dropped")


async def test_wrong_auth_key_fails_cmac():
    """
    If the server uses a different key for CMAC verification, KE_REQ fails.
    This confirms the CMAC check actually gates on the correct key.
    """
    client = AriendiSecurity()

    # Build a server that uses a WRONG key for CMAC verification
    class _WrongKeyServer(_ServerSide):
        async def run_handshake(self, send_fn, nonce1=None, nonce2=None):
            if nonce1 is None:
                nonce1 = os.urandom(16)
            if nonce2 is None:
                nonce2 = os.urandom(16)

            await self._await_u(self._u_ctrl(_HDLC_SABM_TYPE))
            await send_fn(self._att_u(_HDLC_UA_TYPE))
            await self._await_i(_SEC_VERSION_REQ)
            await send_fn(self._att_i(bytes([_SEC_VERSION_RESP, 0, 0, 0, 0, 0, 1])))
            await self._await_i(_SEC_EP_REQ)
            await send_fn(self._att_i(bytes([_SEC_EP_RESP]) + nonce1 + nonce2 + bytes([0x01])))

            ke = await self._await_i(_SEC_KE_REQ)
            client_public_bytes = ke[1:33]
            client_cmac_bytes   = ke[33:49]

            wrong_key = bytes(16)  # all-zero key
            auth_key  = _hkdf(ikm=wrong_key, salt=nonce1, length=16)
            expected_cmac = _aes_cmac(auth_key, client_public_bytes)
            if client_cmac_bytes != expected_cmac:
                raise ValueError("Server: client CMAC FAILED (expected with wrong key)")
            # Should never reach here
            self.handshake_done = True

    server = _WrongKeyServer()
    c_send, s_send = _make_pipe(client, server)

    server_task = asyncio.create_task(server.run_handshake(s_send))
    try:
        await client.perform_handshake(c_send)
        # Client waits for KE_RESP, which server never sends (it raises instead)
        assert False, "Expected handshake to fail (server CMAC check)"
    except (TimeoutError, asyncio.TimeoutError, ValueError):
        pass  # expected — server rejected the client's CMAC

    # Give the server task a moment to settle
    try:
        await asyncio.wait_for(server_task, timeout=0.5)
    except (asyncio.TimeoutError, Exception):
        pass

    exc = server_task.exception() if server_task.done() else None
    assert exc is not None and "CMAC FAILED" in str(exc), \
        f"Expected server CMAC failure, got: {exc}"
    print("PASS test_wrong_auth_key_fails_cmac")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run_all():
    tests = [
        test_handshake_completes,
        test_client_to_server_encryption,
        test_server_to_client_encryption,
        test_round_trip_multiple_frames,
        test_tampered_frame_dropped,
        test_wrong_auth_key_fails_cmac,
    ]
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as exc:
            print(f"FAIL {test_fn.__name__}: {exc}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


def test_all_handshake():
    """pytest entry point — runs all async tests synchronously."""
    assert asyncio.run(_run_all()) == 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_all()))
