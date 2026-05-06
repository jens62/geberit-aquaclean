#!/usr/bin/env python3
"""
Offline handshake CMAC verification against real Alba pcapng captures.

For each BLE session in the captures, extracts:
  - nonce1 from the device's EP_RESP (type 0x11)  — sent in cleartext
  - client_pubkey + client_cmac from KE_REQ (type 0x12) — sent in cleartext

Then verifies:
    auth_key      = HKDF(aquacleanBridgeId, nonce1, length=16)
    expected_cmac = AES-CMAC(auth_key, client_pubkey)
    assert expected_cmac == client_cmac

A passing CMAC check proves that aquacleanBridgeId is the preshared key the
real Alba device at E4:85:01:CD:6B:04 (kstr) uses.  No DH private key is
needed — only the cleartext handshake fields.

Encrypted data frames (type 0x20) cannot be decrypted because the session
keys derive from an ephemeral X25519 private key that was never recorded.

Requirements
------------
  tshark — Wireshark CLI, checked at TSHARK_PATH below
  pcapng files at PCAPNG_DIR (not committed to the repo — local-assets only)

Run
---
  /Users/jens/venv/bin/python tests/test_pcapng_handshake.py
  /Users/jens/venv/bin/python -m pytest tests/test_pcapng_handshake.py -v
"""

import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from aquaclean_console_app.bluetooth_le.LE.AriendiSecurity import (
    _crc16_kermit,
    _cobs_decode,
    _hkdf,
    _aes_cmac,
    aquacleanBridgeId,
    _SEC_EP_RESP,
    _SEC_KE_REQ,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TSHARK_PATH = "/Applications/Wireshark.app/Contents/MacOS/tshark"

PCAPNG_DIR = pathlib.Path(__file__).parent.parent / "local-assets/Android-BLE-Logs/kstr"

PCAPNG_FILES = [
    PCAPNG_DIR / "GeberitConnect4xViaApp.pcapng",
    PCAPNG_DIR / "GeberitFirstconnection.pcapng",
]

# Skip all tests in this module when tshark or pcapng files are unavailable.
tshark_missing  = not os.path.exists(TSHARK_PATH)
pcapngs_missing = not any(p.exists() for p in PCAPNG_FILES)

pytestmark = pytest.mark.skipif(
    tshark_missing or pcapngs_missing,
    reason="tshark or pcapng capture files not available",
)


# ---------------------------------------------------------------------------
# ATT frame extraction via tshark
# ---------------------------------------------------------------------------

def _extract_att_frames(pcapng: pathlib.Path) -> list[dict]:
    """
    Return all ATT WRITE_CMD (client→device, handle 0x001e) and
    HANDLE_VALUE_NOTIF (device→client, handle 0x0020) frames as:
        {"direction": "CD"|"DC", "value": bytes}
    in packet order.
    """
    result = subprocess.run(
        [
            TSHARK_PATH, "-r", str(pcapng),
            "-Y", "btatt.opcode == 0x52 or btatt.opcode == 0x1b",
            "-T", "fields",
            "-e", "btatt.opcode",
            "-e", "btatt.handle",
            "-e", "btatt.value",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    frames = []
    for line in result.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3:
            continue
        opcode_str, handle_str, value_str = parts[0], parts[1], parts[2]
        try:
            opcode = int(opcode_str, 16)
            handle = int(handle_str.split(",")[0], 16)
        except ValueError:
            continue
        if opcode == 0x52 and handle == 0x001E:
            direction = "CD"
        elif opcode == 0x1B and handle == 0x0020:
            direction = "DC"
        else:
            continue
        try:
            value = bytes.fromhex(value_str) if value_str else b""
        except ValueError:
            continue
        frames.append({"direction": direction, "value": value})
    return frames


# ---------------------------------------------------------------------------
# COBS frame reassembly
# ---------------------------------------------------------------------------

def _cobs_frames_from_stream(stream: bytes) -> list[bytes]:
    """
    Split a raw byte stream on 0x00 delimiters and return the non-empty
    COBS payloads (bytes between each pair of 0x00 bytes).

    ATT packets are concatenated into one stream per direction, so
    multi-ATT-fragment COBS frames are automatically reassembled.
    """
    frames: list[bytes] = []
    i = 0
    while i < len(stream):
        if stream[i] == 0:
            j = stream.find(b"\x00", i + 1)
            if j == -1:
                break
            payload = stream[i + 1 : j]
            if payload:
                frames.append(bytes(payload))
            i = j
        else:
            i += 1
    return frames


# ---------------------------------------------------------------------------
# HDLC frame parsing
# ---------------------------------------------------------------------------

def _parse_hdlc(cobs_payload: bytes) -> tuple[int, bytes] | None:
    """
    COBS-decode *cobs_payload*, verify the CRC-16/Kermit footer, and return
    (ctrl, application_payload).  Returns None on any decode or CRC error.
    """
    try:
        decoded = _cobs_decode(cobs_payload)
    except Exception:
        return None
    if len(decoded) < 3:
        return None
    crc_recv = decoded[-2] | (decoded[-1] << 8)
    if crc_recv != _crc16_kermit(decoded[:-2]):
        return None
    return decoded[0], decoded[1:-2]


# ---------------------------------------------------------------------------
# Session extraction
# ---------------------------------------------------------------------------

def _extract_sessions(pcapng: pathlib.Path) -> list[dict]:
    """
    Parse *pcapng* and return one dict per complete Arendi Security handshake:
        {
            "nonce1":        bytes(16),
            "nonce2":        bytes(16),
            "client_pubkey": bytes(32),
            "client_cmac":   bytes(16),
        }

    Sessions are matched by index: the N-th EP_RESP is paired with the N-th
    KE_REQ.  Both are sent in cleartext before any session key is established.
    """
    raw_frames = _extract_att_frames(pcapng)
    if not raw_frames:
        return []

    # Build per-direction byte streams (ATT payloads concatenated in order).
    cd_stream = bytearray()
    dc_stream = bytearray()
    for f in raw_frames:
        (cd_stream if f["direction"] == "CD" else dc_stream).extend(f["value"])

    # Extract EP_RESP frames from device→client stream.
    ep_resps: list[dict] = []
    for cobs in _cobs_frames_from_stream(bytes(dc_stream)):
        r = _parse_hdlc(cobs)
        if r and r[1] and r[1][0] == _SEC_EP_RESP and len(r[1]) >= 33:
            ep_resps.append({"nonce1": r[1][1:17], "nonce2": r[1][17:33]})

    # Extract KE_REQ frames from client→device stream.
    ke_reqs: list[dict] = []
    for cobs in _cobs_frames_from_stream(bytes(cd_stream)):
        r = _parse_hdlc(cobs)
        if r and r[1] and r[1][0] == _SEC_KE_REQ and len(r[1]) >= 49:
            ke_reqs.append({"client_pubkey": r[1][1:33], "client_cmac": r[1][33:49]})

    # Pair N-th EP_RESP with N-th KE_REQ.
    return [{**ep, **ke} for ep, ke in zip(ep_resps, ke_reqs)]


# ---------------------------------------------------------------------------
# CMAC verification
# ---------------------------------------------------------------------------

def _verify_cmac(session: dict) -> bool:
    """
    Verify the client's CMAC from a captured KE_REQ against our preshared key.

        auth_key      = HKDF(aquacleanBridgeId, salt=nonce1, length=16)
        expected_cmac = AES-CMAC(auth_key, client_pubkey)
    """
    auth_key      = _hkdf(ikm=aquacleanBridgeId, salt=session["nonce1"], length=16)
    expected_cmac = _aes_cmac(auth_key, session["client_pubkey"])
    return expected_cmac == session["client_cmac"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sessions_found_in_4x_capture():
    """GeberitConnect4xViaApp.pcapng must contain at least 4 handshake sessions."""
    pcapng = PCAPNG_DIR / "GeberitConnect4xViaApp.pcapng"
    if not pcapng.exists():
        pytest.skip("GeberitConnect4xViaApp.pcapng not found")
    sessions = _extract_sessions(pcapng)
    assert len(sessions) >= 4, (
        f"Expected ≥4 sessions in 4x capture, found {len(sessions)}"
    )


def test_sessions_found_in_first_connection_capture():
    """GeberitFirstconnection.pcapng must contain at least 1 handshake session."""
    pcapng = PCAPNG_DIR / "GeberitFirstconnection.pcapng"
    if not pcapng.exists():
        pytest.skip("GeberitFirstconnection.pcapng not found")
    sessions = _extract_sessions(pcapng)
    assert len(sessions) >= 1, (
        f"Expected ≥1 session in first-connection capture, found {len(sessions)}"
    )


def test_cmac_all_sessions_4x_capture():
    """
    Every session in GeberitConnect4xViaApp.pcapng passes the CMAC check.

    A passing CMAC confirms aquacleanBridgeId is the correct preshared key
    used by the kstr Alba device (E4:85:01:CD:6B:04).
    """
    pcapng = PCAPNG_DIR / "GeberitConnect4xViaApp.pcapng"
    if not pcapng.exists():
        pytest.skip("GeberitConnect4xViaApp.pcapng not found")
    sessions = _extract_sessions(pcapng)
    assert sessions, "No sessions found — check tshark and GATT handles"
    for i, s in enumerate(sessions):
        assert _verify_cmac(s), (
            f"Session {i}: CMAC mismatch — "
            f"nonce1={s['nonce1'].hex()} "
            f"pubkey={s['client_pubkey'].hex()} "
            f"captured={s['client_cmac'].hex()}"
        )


def test_cmac_all_sessions_first_connection_capture():
    """
    Every session in GeberitFirstconnection.pcapng passes the CMAC check.
    """
    pcapng = PCAPNG_DIR / "GeberitFirstconnection.pcapng"
    if not pcapng.exists():
        pytest.skip("GeberitFirstconnection.pcapng not found")
    sessions = _extract_sessions(pcapng)
    assert sessions, "No sessions found — check tshark and GATT handles"
    for i, s in enumerate(sessions):
        assert _verify_cmac(s), (
            f"Session {i}: CMAC mismatch — "
            f"nonce1={s['nonce1'].hex()} "
            f"pubkey={s['client_pubkey'].hex()} "
            f"captured={s['client_cmac'].hex()}"
        )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def _run_all():
    total = passed = failed = 0
    for pcapng in PCAPNG_FILES:
        if not pcapng.exists():
            print(f"SKIP {pcapng.name} — file not found")
            continue
        sessions = _extract_sessions(pcapng)
        if not sessions:
            print(f"WARN {pcapng.name} — no handshake sessions found")
            continue
        for i, s in enumerate(sessions):
            total += 1
            ok = _verify_cmac(s)
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1
            print(
                f"{status}  {pcapng.name}  session {i}  "
                f"nonce1={s['nonce1'].hex()[:16]}...  "
                f"cmac={'ok' if ok else 'MISMATCH'}"
            )
    print(f"\n{passed}/{total} sessions passed CMAC verification")
    if failed:
        print(f"NOTE: {failed} session(s) failed — aquacleanBridgeId may be wrong")
    return failed


if __name__ == "__main__":
    if tshark_missing:
        print(f"ERROR: tshark not found at {TSHARK_PATH}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0 if _run_all() == 0 else 1)
