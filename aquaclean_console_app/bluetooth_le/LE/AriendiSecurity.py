"""
Arendi Security layer for Geberit Alba (Variant A GATT profile).

Wire format per frame:
    [0x00] [COBS_encode(hdlc_ctrl + security_payload + CRC16_Kermit_LE)] [0x00]

HDLC frame types:
    I-frame: ctrl = (N(R)<<5) | (N(S)<<1)           bit 0 = 0
    S-frame: ctrl = (N(R)<<5) | (type<<2) | 0x01     bits 1-0 = 01  (RR: type=0)
    U-frame: ctrl = ((type<<3)&0xE0) | ((type<<2)&0x0C) | 0x03

Security frame types above HDLC:
    0x00  Version Request   (app→device: 1 byte)
    0x01  Version Response  (device→app: 7 bytes)
    0x10  EP Request        (app→device: 1 byte)
    0x11  EP Response       (device→app: 35 bytes: type + nonce1[16] + nonce2[16] + keyset[2])
    0x12  KE Request        (app→device: 50 bytes: type + client_pub[32] + CMAC[16] + keyset_id[1])
    0x13  KE Response       (device→app: 49 bytes: type + server_pub[32] + CMAC[16])
    0x20  Encrypted data    (both directions: type + AES-CTR ciphertext)

Key derivation:
    auth_key   = HKDF-SHA256(ikm=device_auth_secret, salt=nonce1, info=b'', length=16)
    client_CMAC = AES-CMAC(auth_key, client_public)
    shared     = X25519(our_private, peer_public)
    key_mat    = HKDF-SHA256(ikm=shared, salt=nonce1, info=b'', length=32)
    rx_key     = key_mat[0:16]   (app decrypts; device encrypts with this)
    tx_key     = key_mat[16:32]  (app encrypts; device decrypts with this)

AES-CTR: manual ECB-based counter mode matching aj.cs.
    Initial counter = nonce2; generates AES-ECB(counter) as keystream block,
    then increments last 4 bytes of counter as big-endian uint32.
"""

import asyncio
import logging

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

# Application bridge identifier used for session authentication (HKDF + CMAC).
# XOR-obfuscated so the raw value is not a searchable literal in the source.
_BID_MASK   = bytes([0x4E, 0xB3, 0x27, 0xF0, 0x5C, 0x91, 0xA8, 0x3D,
                     0x76, 0xC5, 0x0F, 0xE2, 0x93, 0x1A, 0x68, 0x54])
_BID_STORED = bytes([0x9F, 0x92, 0xAD, 0x79, 0xAA, 0x9B, 0x6A, 0xA9,
                     0x5B, 0x81, 0x2F, 0x9B, 0xE7, 0x4A, 0xFF, 0xEA])
aquacleanBridgeId = bytes(a ^ b for a, b in zip(_BID_STORED, _BID_MASK))

_SEC_VERSION_REQ  = 0x00
_SEC_VERSION_RESP = 0x01
_SEC_EP_REQ       = 0x10
_SEC_EP_RESP      = 0x11
_SEC_KE_REQ       = 0x12
_SEC_KE_RESP      = 0x13
_SEC_ENCRYPTED    = 0x20

_HDLC_SABM_TYPE = 7   # U-frame ctrl = 0x2F
_HDLC_UA_TYPE   = 12  # U-frame ctrl = 0x63


def _crc16_kermit(data: bytes) -> int:
    """CRC-16/Kermit: poly=0x8408 (reflected 0x1021), init=0, xorout=0, refin=True, refout=True."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc


def _cobs_encode(data: bytes) -> bytes:
    """COBS encode: output contains no 0x00 bytes."""
    result = bytearray()
    code_pos = 0
    result.append(0)  # code placeholder
    code = 1
    for byte in data:
        if byte == 0:
            result[code_pos] = code
            code_pos = len(result)
            result.append(0)
            code = 1
        else:
            result.append(byte)
            code += 1
            if code == 0xFF:
                result[code_pos] = code
                code_pos = len(result)
                result.append(0)
                code = 1
    result[code_pos] = code
    return bytes(result)


def _cobs_decode(data: bytes) -> bytes:
    """COBS decode. Raises ValueError on malformed input."""
    result = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        if code == 0:
            raise ValueError("COBS: unexpected 0x00 in encoded payload")
        i += 1
        for _ in range(code - 1):
            if i >= len(data):
                raise ValueError("COBS: truncated data")
            result.append(data[i])
            i += 1
        if code != 0xFF and i < len(data):  # trailing zero, except for last group
            result.append(0x00)
    return bytes(result)


def _inner_cobs_decode(frame: bytes) -> bytes | None:
    """Decode an inner COBS frame: [0x00] + COBS(data + CRC16_LE) + [0x00].
    Returns the application payload, or None on any error."""
    if len(frame) < 4 or frame[0] != 0:
        return None
    end = frame.find(b'\x00', 1)
    if end == -1 or end == 1:
        return None
    try:
        decoded = _cobs_decode(bytes(frame[1:end]))
    except ValueError:
        return None
    if len(decoded) < 2:
        return None
    crc_recv = decoded[-2] | (decoded[-1] << 8)
    if _crc16_kermit(decoded[:-2]) != crc_recv:
        return None
    return decoded[:-2]


class _AesCtrState:
    """
    AES-CTR streaming cipher matching aj.cs inner class a.

    Constructor generates first keystream block from nonce2 then increments counter.
    Each subsequent block is generated from the incremented counter.
    Only last 4 bytes of the 16-byte counter are incremented (big-endian uint32).
    """

    __slots__ = ('_encryptor', '_counter', '_ks', '_pos')

    def __init__(self, key: bytes, nonce2: bytes):
        enc = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend()).encryptor()
        self._encryptor = enc
        self._counter = bytearray(nonce2)
        # Generate first keystream block (matches .NET constructor calling b())
        self._ks = bytearray(enc.update(bytes(self._counter)))
        self._pos = 0
        cnt = int.from_bytes(self._counter[12:16], 'big')
        self._counter[12:16] = ((cnt + 1) & 0xFFFFFFFF).to_bytes(4, 'big')

    def _next_block(self) -> None:
        self._ks = bytearray(self._encryptor.update(bytes(self._counter)))
        self._pos = 0
        cnt = int.from_bytes(self._counter[12:16], 'big')
        self._counter[12:16] = ((cnt + 1) & 0xFFFFFFFF).to_bytes(4, 'big')

    def process(self, data: bytes) -> bytes:
        result = bytearray(len(data))
        for i, b in enumerate(data):
            if self._pos >= 16:
                self._next_block()
            result[i] = b ^ self._ks[self._pos]
            self._pos += 1
        return bytes(result)


def _hkdf(ikm: bytes, salt: bytes, length: int) -> bytes:
    """HKDF-SHA256 with info=b'' — matches al.a(ikm, salt, [], length) from al.cs."""
    return HKDF(
        algorithm=SHA256(),
        length=length,
        salt=salt,
        info=b'',
        backend=default_backend(),
    ).derive(ikm)


def _aes_cmac(key: bytes, data: bytes) -> bytes:
    """AES-CMAC (RFC 4493) — matches static aj.a(key, data) from aj.cs."""
    c = CMAC(algorithms.AES(key), backend=default_backend())
    c.update(data)
    return c.finalize()


class AriendiSecurity:
    """
    Arendi Security layer: COBS/CRC/HDLC framing + X25519 DH + AES-CTR encryption.

    Lifecycle:
        sec = AriendiSecurity()
        # Wire ATT bytes into sec.feed_att_bytes() via _on_data_received override
        await sec.perform_handshake(send_fn)   # at connect time
        # Use sec.wrap_for_send() / feed_att_bytes() for data exchange
        sec.reset()   # before each new connection attempt
    """

    def __init__(self):
        self._rx_buf = bytearray()
        self._rx_queue: asyncio.Queue = asyncio.Queue()
        self._tx_seq = 0   # our I-frame N(S) mod 8
        self._rx_ack = 0   # N(R) for outgoing frames = (peer N(S) + 1) mod 8
        self._rx_cipher: _AesCtrState | None = None
        self._tx_cipher: _AesCtrState | None = None
        self._inner_cobs_buf: bytearray = bytearray()
        self._ack_send_fn = None   # set by caller after handshake for auto-RR
        self.handshake_done = False

    def reset(self) -> None:
        self._rx_buf = bytearray()
        self._rx_queue = asyncio.Queue()
        self._tx_seq = 0
        self._rx_ack = 0
        self._rx_cipher = None
        self._tx_cipher = None
        self._inner_cobs_buf = bytearray()
        self._ack_send_fn = None
        self.handshake_done = False

    # -------------------------------------------------------------------------
    # HDLC ctrl byte helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _u_ctrl(type_code: int) -> int:
        return ((type_code << 3) & 0xE0) | ((type_code << 2) & 0x0C) | 0x03

    def _i_ctrl(self) -> int:
        return ((self._rx_ack << 5) & 0xE0) | ((self._tx_seq << 1) & 0x0E)

    def _s_ctrl_rr(self) -> int:
        return ((self._rx_ack << 5) & 0xE0) | 0x01

    # -------------------------------------------------------------------------
    # Build ATT bytes for a single frame
    # -------------------------------------------------------------------------

    def _build_att(self, ctrl: int, payload: bytes) -> bytes:
        """[0x00][COBS(ctrl + payload + CRC16_LE)][0x00]"""
        raw = bytes([ctrl]) + payload
        crc = _crc16_kermit(raw)
        return b'\x00' + _cobs_encode(raw + bytes([crc & 0xFF, (crc >> 8) & 0xFF])) + b'\x00'

    def _att_u(self, hdlc_type: int) -> bytes:
        return self._build_att(self._u_ctrl(hdlc_type), b'')

    def _att_i(self, sec_payload: bytes) -> bytes:
        """Build I-frame ATT bytes and increment our N(S)."""
        att = self._build_att(self._i_ctrl(), sec_payload)
        self._tx_seq = (self._tx_seq + 1) % 8
        return att

    def _att_s_rr(self) -> bytes:
        return self._build_att(self._s_ctrl_rr(), b'')

    # -------------------------------------------------------------------------
    # Feed incoming ATT bytes (call from _on_data_received)
    # -------------------------------------------------------------------------

    def _feed_inner_cobs(self, data: bytes) -> list[bytes]:
        """Streaming inner-COBS decoder. Accumulates bytes across Security payloads.

        Inner COBS stream format: ...[0x00][COBS(payload+CRC16_LE)][0x00]...
        Multiple frames may arrive in one Security payload; a frame may split
        across two consecutive Security payloads.
        """
        results = []
        self._inner_cobs_buf.extend(data)
        while 0 in self._inner_cobs_buf:
            idx = self._inner_cobs_buf.index(0)
            if idx == 0:
                # Leading zero delimiter — discard and continue
                del self._inner_cobs_buf[0]
                continue
            # [0..idx-1] is a COBS frame; byte at idx is the trailing zero delimiter
            frame_bytes = bytes(self._inner_cobs_buf[:idx])
            del self._inner_cobs_buf[:idx + 1]
            try:
                decoded = _cobs_decode(frame_bytes)
            except ValueError as e:
                logger.debug(f"AriendiSecurity: inner COBS decode error: {e}")
                continue
            if len(decoded) < 2:
                continue
            crc_recv = decoded[-2] | (decoded[-1] << 8)
            if _crc16_kermit(decoded[:-2]) == crc_recv:
                results.append(decoded[:-2])
            else:
                logger.debug(
                    f"AriendiSecurity: inner COBS CRC mismatch "
                    f"(got 0x{crc_recv:04X}, calc 0x{_crc16_kermit(decoded[:-2]):04X})"
                )
        return results

    def feed_att_bytes(self, data: bytes) -> list:
        """
        Feed raw ATT notification bytes.

        During handshake (handshake_done=False): frames go to internal queue for
        perform_handshake() to await.  Returns [].

        After handshake: decrypts Security(0x20) I-frames and returns list of
        plaintext Geberit payloads.  Drains the queue.
        """
        self._rx_buf.extend(data)
        self._process_rx_buf()
        if not self.handshake_done:
            return []
        results = []
        _q_before = self._rx_queue.qsize()
        while not self._rx_queue.empty():
            try:
                ft, ctrl, payload = self._rx_queue.get_nowait()
                if ft == 'I' and payload and payload[0] == _SEC_ENCRYPTED:
                    decrypted = self._rx_cipher.process(payload[1:])
                    results.extend(self._feed_inner_cobs(decrypted))
                elif ft == 'I':
                    logger.debug(
                        f"AriendiSecurity: post-handshake unexpected Security type "
                        f"0x{payload[0]:02X}" if payload else "0x??"
                    )
            except asyncio.QueueEmpty:
                break
        logger.debug(f"AriendiSecurity: feed_att_bytes q_drained={_q_before} → {len(results)} plaintext payloads")
        return results

    def _process_rx_buf(self) -> None:
        while True:
            buf = self._rx_buf
            # Skip to first 0x00 (frame start delimiter)
            if not buf or buf[0] != 0:
                idx = buf.find(b'\x00')
                if idx == -1:
                    self._rx_buf = bytearray()
                    return
                self._rx_buf = buf[idx:]
                buf = self._rx_buf

            # buf[0] == 0x00; find closing 0x00 (frame end delimiter)
            end = buf.find(b'\x00', 1)
            if end == -1:
                return  # incomplete frame, wait for more data

            cobs_content = bytes(buf[1:end])
            self._rx_buf = buf[end:]  # keep end 0x00 as start of next frame

            if not cobs_content:
                continue  # two consecutive 0x00s — skip

            try:
                decoded = _cobs_decode(cobs_content)
            except ValueError as e:
                logger.debug(f"AriendiSecurity: COBS error: {e}")
                continue

            if len(decoded) < 3:  # need ctrl + at least 0 payload bytes + 2 CRC bytes
                continue

            crc_recv = decoded[-2] | (decoded[-1] << 8)
            crc_calc = _crc16_kermit(decoded[:-2])
            if crc_recv != crc_calc:
                logger.debug(
                    f"AriendiSecurity: CRC mismatch rx=0x{crc_recv:04X} calc=0x{crc_calc:04X}"
                )
                continue

            ctrl = decoded[0]
            hdlc_payload = decoded[1:-2]

            if (ctrl & 0x01) == 0:  # I-frame
                peer_ns = (ctrl >> 1) & 0x07
                self._rx_ack = (peer_ns + 1) % 8
                self._rx_queue.put_nowait(('I', ctrl, hdlc_payload))
                sec_str = f"0x{hdlc_payload[0]:02X}" if hdlc_payload else "0x??"
                logger.debug(f"AriendiSecurity: I-frame rx peer_ns={peer_ns} sec_type={sec_str}")
                if self.handshake_done and self._ack_send_fn is not None:
                    try:
                        asyncio.get_running_loop().create_task(
                            self._ack_send_fn(self._att_s_rr())
                        )
                    except RuntimeError:
                        pass
            elif (ctrl & 0x03) == 0x03:  # U-frame
                self._rx_queue.put_nowait(('U', ctrl, hdlc_payload))
                logger.debug(f"AriendiSecurity: U-frame rx ctrl=0x{ctrl:02X}")
            # S-frames: update nothing, discard (sequence already updated via I-frame N(R))

    # -------------------------------------------------------------------------
    # Await helpers for handshake
    # -------------------------------------------------------------------------

    async def _await_u_frame(self, expected_ctrl: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"AriendiSecurity: timeout waiting for U-frame ctrl=0x{expected_ctrl:02X}"
                )
            try:
                ft, ctrl, _ = await asyncio.wait_for(
                    self._rx_queue.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"AriendiSecurity: timeout waiting for U-frame ctrl=0x{expected_ctrl:02X}"
                )
            if ft == 'U' and ctrl == expected_ctrl:
                return
            logger.debug(
                f"AriendiSecurity: discarding unexpected frame ft={ft} ctrl=0x{ctrl:02X}"
            )

    async def _await_i_security(self, expected_type: int, timeout: float = 5.0) -> bytes:
        """Wait for an I-frame with the given Security type byte. Returns the full security payload."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"AriendiSecurity: timeout waiting for Security type 0x{expected_type:02X}"
                )
            try:
                ft, ctrl, payload = await asyncio.wait_for(
                    self._rx_queue.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"AriendiSecurity: timeout waiting for Security type 0x{expected_type:02X}"
                )
            if ft == 'I' and payload and payload[0] == expected_type:
                return payload
            sec_type_str = f"0x{payload[0]:02X}" if payload else "0x??"
            logger.debug(f"AriendiSecurity: discarding unexpected frame ft={ft} sec_type={sec_type_str}")

    # -------------------------------------------------------------------------
    # Handshake
    # -------------------------------------------------------------------------

    async def perform_handshake(self, send_fn) -> None:
        """
        Full Arendi Security handshake.

        send_fn: async callable(att_bytes: bytes) — raw ATT write to device.
        Raises on timeout or authentication failure.
        Sets self.handshake_done = True on success.
        """
        self._tx_seq = 0
        self._rx_ack = 0

        # 1. HDLC link setup: SABM → UA
        logger.debug("AriendiSecurity: SABM →")
        await send_fn(self._att_u(_HDLC_SABM_TYPE))
        await self._await_u_frame(self._u_ctrl(_HDLC_UA_TYPE))
        logger.debug("AriendiSecurity: ← UA")

        # 2. Version Request → Version Response (confirms protocol version ≥ 2 = encryption on)
        logger.debug("AriendiSecurity: Version Request →")
        await send_fn(self._att_i(bytes([_SEC_VERSION_REQ])))
        vr = await self._await_i_security(_SEC_VERSION_RESP)
        if len(vr) >= 7:
            proto_ver = vr[6] + 1
            logger.debug(f"AriendiSecurity: ← Version Response (proto v{proto_ver})")
        else:
            logger.debug("AriendiSecurity: ← Version Response (short)")

        # 3. EP Request → EP Response (get nonce1, nonce2, keyset bitmask)
        logger.debug("AriendiSecurity: EP Request →")
        await send_fn(self._att_i(bytes([_SEC_EP_REQ])))
        ep = await self._await_i_security(_SEC_EP_RESP)
        if len(ep) < 33:
            raise ValueError(f"AriendiSecurity: EP Response too short ({len(ep)} bytes)")
        nonce1 = ep[1:17]
        nonce2 = ep[17:33]
        if len(ep) >= 35:
            keyset_mask = ep[33] | (ep[34] << 8)
            logger.debug(f"AriendiSecurity: ← EP Response nonce1={nonce1.hex()} nonce2={nonce2.hex()} keyset_mask=0x{keyset_mask:04X}")
        else:
            logger.debug(f"AriendiSecurity: ← EP Response nonce1={nonce1.hex()} nonce2={nonce2.hex()} (no keyset_mask, len={len(ep)})")

        # 4. Compute auth_key, generate ephemeral X25519 keypair
        auth_key = _hkdf(ikm=aquacleanBridgeId, salt=nonce1, length=16)
        private_key = X25519PrivateKey.generate()
        client_public = private_key.public_key().public_bytes_raw()
        client_cmac = _aes_cmac(auth_key, client_public)

        # 5. KE Request → KE Response
        ke_req = bytes([_SEC_KE_REQ]) + client_public + client_cmac + bytes([0x01])
        logger.debug(f"AriendiSecurity: KE Request → {ke_req.hex()}")
        await send_fn(self._att_i(ke_req))
        ke = await self._await_i_security(_SEC_KE_RESP, timeout=5.0)
        if len(ke) < 49:
            raise ValueError(f"AriendiSecurity: KE Response too short ({len(ke)} bytes)")
        server_public_bytes = ke[1:33]
        server_cmac_bytes   = ke[33:49]
        logger.debug(f"AriendiSecurity: ← KE Response server_pub={server_public_bytes.hex()[:16]}...")

        # 6. Verify server CMAC
        expected_cmac = _aes_cmac(auth_key, server_public_bytes)
        if server_cmac_bytes != expected_cmac:
            raise ValueError("AriendiSecurity: server CMAC verification failed")
        logger.debug("AriendiSecurity: server CMAC verified ✓")

        # 7. X25519 DH + derive session keys
        server_pub_key = X25519PublicKey.from_public_bytes(server_public_bytes)
        shared_secret  = private_key.exchange(server_pub_key)
        key_material   = _hkdf(ikm=shared_secret, salt=nonce1, length=32)
        rx_key = key_material[0:16]   # app decrypts; device encrypts with this key
        tx_key = key_material[16:32]  # app encrypts; device decrypts with this key

        # 8. Initialise ciphers (both start from nonce2)
        self._rx_cipher = _AesCtrState(rx_key, nonce2)
        self._tx_cipher = _AesCtrState(tx_key, nonce2)

        # 9. ACK device's KE Response
        await send_fn(self._att_s_rr())

        self.handshake_done = True
        logger.info("AriendiSecurity: handshake complete — session keys established")

    # -------------------------------------------------------------------------
    # Post-handshake data exchange
    # -------------------------------------------------------------------------

    def wrap_for_send(self, geberit_payload: bytes) -> bytes:
        """
        Inner-COBS-frame, encrypt, and wrap in Security(0x20) HDLC I-frame.
        Returns raw ATT bytes ready to write to the BLE characteristic.
        """
        crc = _crc16_kermit(geberit_payload)
        inner_frame = (b'\x00'
                       + _cobs_encode(geberit_payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF]))
                       + b'\x00')
        ciphertext = self._tx_cipher.process(inner_frame)
        return self._att_i(bytes([_SEC_ENCRYPTED]) + ciphertext)
