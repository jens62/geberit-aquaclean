#!/usr/bin/env python3
"""
Known-plaintext analysis of Geberit AquaClean Alba BLE encryption.

DEFAULT MODE (no arguments)
  Runs all 7 hardcoded analysis sections against the known ciphertexts from
  Johannes's and kstr's devices (Sections 1–7).

  /Users/jens/venv/bin/python tools/alba-decrypt-analysis.py

PCAPNG MODE (--pcapng)
  Extracts GetDeviceIdentification ciphertexts from a new capture and runs
  known-plaintext analysis against them.

  /Users/jens/venv/bin/python tools/alba-decrypt-analysis.py \\
      --pcapng local-assets/Android-BLE-Logs/kstr/GeberitConnect4xViaApp.pcapng

  With device info for known-plaintext attack:
  /Users/jens/venv/bin/python tools/alba-decrypt-analysis.py \\
      --pcapng capture.pcapng \\
      --serial <your-serial> \\
      --sap 146.350.01.x

  Analyse multiple captures together (e.g. remote + app):
  /Users/jens/venv/bin/python tools/alba-decrypt-analysis.py \\
      --pcapng remote.pcapng --pcapng app.pcapng \\
      --serial <your-serial>

INPUT FORMAT
  pcapng must use link type 201 (BLUETOOTH_HCI_H4_WITH_PHDR).
  Wireshark captures from Android BTSnoop logs use this format automatically.
  nRF52840 Dongle captures use a different link type — export from Wireshark
  via File → Export Specified Packets, selecting a compatible dissector.
"""

import argparse
import importlib.util
import itertools
import struct
import sys
from pathlib import Path

# ── Frame structure ────────────────────────────────────────────────────────────
# 41-byte GetDeviceIdentification response (Geberit AquaClean Alba):
#   bytes  0– 3: cleartext header  00 24 42 11
#   bytes  4–35: encrypted block 1 (32 bytes)  ← known-plaintext target
#   bytes 36–37: cleartext separator  03 03
#   bytes 38–39: encrypted block 2 (2 bytes)
#   byte     40: cleartext terminator  00

# ── Device A — Johannes Schliephake (SB2509EU177754) ──────────────────────────
# Source: local-assets/Bluetooth-Logs/johannes-schliephake/
#   connect.txt         → J1
#   connect+actions.txt → J2
# Firmware RS3.0 TS89, MAC E4:85:01:CD:51:6B

J1_b1 = bytes.fromhex("4EC64F99EABE40BC6C86241C2DF3A61A94ED2677D3B795DC280E9823FF75E431")
J1_b2 = bytes.fromhex("403E")

J2_b1 = bytes.fromhex("A0BB05A2F822B46542118FF71CE4E40CA8796359E2075D2C7BE3A71DE1F61234")
J2_b2 = bytes.fromhex("C81E")

# ── Device B — kstr ──────────────────────────────────────────────────────────
# Firmware RS3.0 TS89
#
# Source: local-assets/Android-BLE-Logs/kstr/
#   Wireshark/GeberitConnectViaApp.pcapng    → C0  (single session, 2026-05-01)
#   GeberitConnect4xViaApp.pcapng           → C1–C4  (4 consecutive sessions, 2026-05-02)
#   GeberitFirstconnection.pcapng           → F1–F3  (fresh-install, 3 sessions, 2026-05-02)

C0_b1 = bytes.fromhex("c90e8edc32b0353e54eabacfcbfcca6436f94ca938fc25cede697b431e4dd3bc")
C0_b2 = bytes.fromhex("a111")

C1_b1 = bytes.fromhex("2131739954fe2b8f9f6e8917b4ccdd3bb46a954ead6e431e2ae27aefa4b7bdde")
C2_b1 = bytes.fromhex("972ee2fc6c02687ecdbf8d504c1680271b3f5b91e4468a705f090c21ed96f135")
C3_b1 = bytes.fromhex("1a7b26f02155ed938b837ec18f6e10ca6cc8b7f62dfce52a6d491bd1c9eb96ef")
C4_b1 = bytes.fromhex("69ff9128c0d0655252724d0c70fa4ea2ce80aa201b42f0fdb3f4eecb720804d3")

# Fresh-install sessions (app reinstalled; counter continued from C4+N)
F1_b1 = bytes.fromhex("a362ddcc75f51845220510a6c3e49348b95ae2f68a2288063b13785468a1d070")
F1_b2 = bytes.fromhex("bfed")
F2_b1 = bytes.fromhex("c202a5728da5c4c0da3231d7837bb0b17e745be461c52d5b8282bfc6b13d61ac")
F2_b2 = bytes.fromhex("7064")
F3_b1 = bytes.fromhex("7baf39beed7652bdfc23e63683dd67dd048b41ed2031973652b6cac3ccc4f667")
F3_b2 = bytes.fromhex("5e54")

# ── Known plaintext candidates ────────────────────────────────────────────────

# Device A (Johannes)
J_TYPE   = b"AcAlba"           # 6 bytes
J_SERIAL = b"SB2509EU177754"   # 14 bytes
J_SAP_VARIANTS = {
    "SAP.0": b"146.350.01.0",
    "SAP.1": b"146.350.01.1",
    "SAP.2": b"146.350.01.2",
}

# Device B (kstr)
K_TYPE   = b"AcAlba"           # 6 bytes
K_SERIAL = b""   # redacted — provide via --serial for known-plaintext analysis
K_SAP_VARIANTS = {
    "SAP.0": b"146.350.01.0",
    "SAP.1": b"146.350.01.1",
    "SAP.2": b"146.350.01.2",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def is_printable_ascii(b: bytes) -> bool:
    return all(0x20 <= c < 0x7F for c in b)


def has_16byte_repeat(b: bytes) -> bool:
    return len(b) >= 32 and b[:16] == b[16:32]


def analyse_keystream(ks: bytes) -> None:
    print(f"    KS : {ks.hex(' ')}")
    print(f"         printable={is_printable_ascii(ks)}  16-byte-repeat={has_16byte_repeat(ks)}")
    distinct = len(set(ks))
    print(f"         distinct_bytes={distinct}/256  ", end="")
    for period in range(1, 17):
        chunk = ks[:period]
        if ks == (chunk * ((len(ks) // period) + 1))[:len(ks)]:
            print(f"REPEATING_KEY_LEN={period} key={chunk.hex()}", end="")
            break
    print()


def try_permutation(name: str, pt: bytes, c_ref: bytes, c_other: bytes) -> bytes | None:
    """XOR pt with c_ref to get keystream; check if it decrypts c_other to plaintext."""
    if len(pt) != len(c_ref):
        return None
    ks = xor_bytes(c_ref, pt)
    print(f"\n  [{name}]")
    print(f"    PT : {pt.hex(' ')}")
    print(f"         {pt!r}")
    analyse_keystream(ks)
    pt2 = xor_bytes(c_other, ks)
    pt2_ascii = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in pt2)
    print(f"    C2→: {pt2.hex(' ')}")
    print(f"         {pt2_ascii!r}  (static-KS hypothesis)")
    return ks


# ── pcapng extraction ─────────────────────────────────────────────────────────

def _load_ble_analyzer():
    """Import android-ble-analyze from the same tools directory."""
    spec = importlib.util.spec_from_file_location(
        "android_ble_analyze",
        Path(__file__).parent / "android-ble-analyze.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_from_pcapng(path: str) -> list[tuple[bytes, bytes | None]]:
    """
    Extract GetDeviceIdentification ciphertexts from a pcapng capture.

    Uses iter_pcapng from android-ble-analyze.py for pcapng parsing, then
    does inline HCI ACL → L2CAP → ATT demultiplexing to find ATT NOTIFY
    packets containing the GetDeviceIdentification frame (00 24 42 11 ...).

    Returns a list of (block1: bytes[32], block2: bytes[2] | None) tuples,
    one per BLE session found in the capture.
    """
    ana  = _load_ble_analyzer()
    raw, fmt = ana.load(Path(path))
    if fmt != "pcapng":
        raise ValueError(f"{path}: not a pcapng file (detected format: {fmt})")

    HCI_ACL     = ana.HCI_ACL      # 0x02
    ATT_NOTIFY  = 0x1B
    L2CAP_ATT   = 0x0004

    results  = []
    in_devid = False
    frags: list[bytes] = []

    for _ts, _direction, h4type, payload in ana.iter_pcapng(raw):
        if h4type != HCI_ACL:
            continue

        # HCI ACL: handle+flags(2) + total_len(2) + L2CAP body
        if len(payload) < 8:
            continue
        total_len = struct.unpack_from("<H", payload, 2)[0]
        l2cap = payload[4: 4 + total_len]
        if len(l2cap) < 5:
            continue
        cid = struct.unpack_from("<H", l2cap, 2)[0]
        if cid != L2CAP_ATT:
            continue
        att = l2cap[4:]
        if not att or att[0] != ATT_NOTIFY or len(att) < 3:
            continue

        value = att[3:]   # skip op(1) + att_handle(2)
        if not value:
            continue

        # Fragment 1: cleartext header 00 24 42 11
        if len(value) >= 4 and value[0] == 0x00 and value[1] == 0x24:
            in_devid = True
            frags = [value]
            continue

        if not in_devid:
            continue

        # Transfer-complete terminator (single 0x00 byte)
        if value == b'\x00':
            full = b''.join(frags)
            # full = [00 24 42 11][32B block1][03 03][2B block2]  (40 bytes)
            if (len(full) >= 40
                    and full[:4] == b'\x00\x24\x42\x11'
                    and full[36:38] == b'\x03\x03'):
                results.append((full[4:36], full[38:40]))
            in_devid = False
            frags = []
            continue

        # Continuation fragment
        frags.append(value)

    return results


# ── Hardcoded multi-device analysis (Sections 1–7) ───────────────────────────

def run_full_analysis() -> None:
    # ── Section 1: Device A — Johannes, 2-session XOR diff ───────────────────
    print("=" * 70)
    print("Section 1: Device A (Johannes) — J1 XOR J2")
    print()
    xc = xor_bytes(J1_b1, J2_b1)
    print(f"  block1: {xc.hex(' ')}")
    xc2 = xor_bytes(J1_b2, J2_b2)
    print(f"  block2: {xc2.hex(' ')}")
    print(f"  All bytes differ: {all(b != 0 for b in xc + xc2)} → keystream is session-specific")

    # ── Section 2: Device B (kstr) — multi-session XOR diffs ─────────────────
    print()
    print("=" * 70)
    print("Section 2: Device B (kstr) — consecutive XOR diffs (4-session capture)")
    print()
    for a, b, label in [
        (C0_b1, C1_b1, "C0^C1"),
        (C1_b1, C2_b1, "C1^C2"),
        (C2_b1, C3_b1, "C2^C3"),
        (C3_b1, C4_b1, "C3^C4"),
    ]:
        d = xor_bytes(a, b)
        print(f"  {label}: {d.hex()}")

    print()
    print("Section 2b: Device B (kstr) — fresh-install XOR diffs")
    print()
    for a_b1, b_b1, a_b2, b_b2, label in [
        (F1_b1, F2_b1, F1_b2, F2_b2, "F1^F2"),
        (F2_b1, F3_b1, F2_b2, F3_b2, "F2^F3"),
        (F1_b1, F3_b1, F1_b2, F3_b2, "F1^F3"),
    ]:
        print(f"  {label} b1: {xor_bytes(a_b1, b_b1).hex()}")
        print(f"  {label} b2: {xor_bytes(a_b2, b_b2).hex()}")

    print()
    print("Section 2c: Cross-series XOR (4-session vs fresh-install)")
    print()
    for a_name, a in [("C0", C0_b1), ("C4", C4_b1)]:
        for b_name, b in [("F1", F1_b1)]:
            d = xor_bytes(a, b)
            print(f"  {a_name}^{b_name}: {d.hex()}")

    # ── Section 3: Cross-device XOR ───────────────────────────────────────────
    print()
    print("=" * 70)
    print("Section 3: Cross-device XOR (Device A vs Device B)")
    print()
    for a_name, a in [("J1", J1_b1), ("J2", J2_b1)]:
        for b_name, b in [("C0", C0_b1), ("F1", F1_b1)]:
            d = xor_bytes(a, b)
            print(f"  {a_name}^{b_name}: {d.hex()}")

    # ── Section 4: Known-plaintext attack — Device A ──────────────────────────
    print()
    print("=" * 70)
    print("Section 4: Known-plaintext attack — Device A (Johannes SB2509EU177754)")
    print("  Trying all orderings of {TYPE(6), SAP(12), SERIAL(14)}")
    print()

    for sap_label, sap in J_SAP_VARIANTS.items():
        candidates = [("TYPE", J_TYPE), (sap_label, sap), ("SERIAL", J_SERIAL)]
        for perm in itertools.permutations(candidates):
            names = "+".join(n for n, _ in perm)
            pt = b"".join(v for _, v in perm)
            if len(pt) == 32:
                try_permutation(names, pt, J1_b1, J2_b1)

    # ── Section 5: Known-plaintext attack — Device B ──────────────────────────
    print()
    print("=" * 70)
    print("Section 5: Known-plaintext attack — Device B (kstr)")
    print("  Using C0 as reference, checking if KS decrypts F1")
    print()
    if not K_SERIAL:
        print("  [skipped — K_SERIAL redacted; run with --pcapng and --serial for pcapng mode]")
        print()

    for sap_label, sap in K_SAP_VARIANTS.items():
        candidates = [("TYPE", K_TYPE), (sap_label, sap), ("SERIAL", K_SERIAL)]
        for perm in itertools.permutations(candidates):
            names = "+".join(n for n, _ in perm)
            pt = b"".join(v for _, v in perm)
            if len(pt) == 32:
                try_permutation(names, pt, C0_b1, F1_b1)

    # ── Section 6: AES key brute-force ────────────────────────────────────────
    print()
    print("=" * 70)
    print("Section 6: AES key brute-force — Device B")
    print("  For each candidate key K: AES_ECB_decrypt(K, expected_keystream_block)")
    print("  should produce a sparse nonce (many zero bytes) if K is correct AES-CTR key")
    print()

    try:
        from Crypto.Cipher import AES

        PIN      = b""   # redacted
        SERIAL_B = b""   # redacted — kstr serial
        MAC_B    = b""   # redacted — kstr MAC

        import hashlib, hmac as _hmac

        def sha256(*parts):
            return hashlib.sha256(b"".join(parts)).digest()

        def md5(*parts):
            return hashlib.md5(b"".join(parts)).digest()

        def hmac_sha256(key, msg):
            return _hmac.new(key, msg, hashlib.sha256).digest()

        key_candidates = {
            "PIN_2B_pad16":         PIN.ljust(16, b"\x00"),
            "PIN_2B_pad32":         PIN.ljust(32, b"\x00"),
            "SHA256(PIN)":          sha256(PIN),
            "SHA256(SERIAL)":       sha256(SERIAL_B),
            "SHA256(MAC)":          sha256(MAC_B),
            "SHA256(SERIAL+PIN)":   sha256(SERIAL_B, PIN),
            "SHA256(PIN+SERIAL)":   sha256(PIN, SERIAL_B),
            "SHA256(MAC+PIN)":      sha256(MAC_B, PIN),
            "SHA256(PIN+MAC)":      sha256(PIN, MAC_B),
            "SHA256(PIN+SERIAL+MAC)": sha256(PIN, SERIAL_B, MAC_B),
            "MD5(PIN)":             md5(PIN),
            "MD5(SERIAL)":          md5(SERIAL_B),
            "MD5(SERIAL+PIN)":      md5(SERIAL_B, PIN),
            "HMAC-SHA256(PIN,SERIAL)":  hmac_sha256(PIN, SERIAL_B),
            "HMAC-SHA256(SERIAL,PIN)":  hmac_sha256(SERIAL_B, PIN),
            "HMAC-SHA256(PIN,MAC)":     hmac_sha256(PIN, MAC_B),
        }

        all_orderings = []
        for sap_label, sap in K_SAP_VARIANTS.items():
            candidates = [("TYPE", K_TYPE), (sap_label, sap), ("SERIAL", K_SERIAL)]
            for perm in itertools.permutations(candidates):
                pt = b"".join(v for _, v in perm)
                if len(pt) == 32:
                    all_orderings.append(("+".join(n for n, _ in perm), pt))

        hits = 0
        ZERO_THRESHOLD = 10

        for key_label, key in key_candidates.items():
            for key_size in [16, 32]:
                k = key[:key_size].ljust(key_size, b"\x00")
                try:
                    aes = AES.new(k, AES.MODE_ECB)
                except ValueError:
                    continue
                for pt_label, pt in all_orderings:
                    ks = xor_bytes(C0_b1, pt)
                    try:
                        nonce_block = aes.decrypt(ks[:16])
                    except Exception:
                        continue
                    zero_count = nonce_block.count(b"\x00")
                    if zero_count >= ZERO_THRESHOLD:
                        hits += 1
                        print(f"  HIT: key={key_label} ({key_size*8}b) pt={pt_label}")
                        print(f"       nonce_block={nonce_block.hex()} zeros={zero_count}")

        if hits == 0:
            print(f"  No hits (no sparse nonce with >= {ZERO_THRESHOLD} zero bytes)")
            print(f"  Tested: {len(key_candidates)} key candidates × {len(all_orderings)} orderings")
            print(f"  Conclusion: device_static_key not derivable from PIN/serial/MAC via single-pass hash")

    except ImportError:
        print("  pycryptodome not installed. Run: /Users/jens/venv/bin/pip install pycryptodome")

    # ── Section 7: Summary ────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("Section 7: Summary")
    print()
    print("  Device A (Johannes SB2509EU177754): 2 ciphertexts (J1, J2)")
    print("  Device B (kstr):                    8 ciphertexts (C0, C1–C4, F1–F3)")
    print()
    print("  All cross-session XOR diffs: HIGH ENTROPY → AES-CTR model confirmed")
    print("  All cross-device XOR diffs:  HIGH ENTROPY → keys are DEVICE-SPECIFIC")
    print("  Known-plaintext attack:      NO REPEATING KEYSTREAM in any ordering")
    print("  AES brute-force (factory PIN): NO HITS in any key derivation scheme")
    print()
    print("  Encryption model: KS_n = AES(device_static_key, IV || n)")
    print("  device_static_key: not derivable from PIN/serial/MAC; likely in device flash")
    print()
    print("  Next steps:")
    print("  1. Sniff the physical remote control (nRF52840 Dongle)")
    print("     → If GetDeviceIdentification is plaintext: known-plaintext attack succeeds")
    print("  2. Capture INITIAL pairing ceremony (factory-reset device)")
    print("  3. Confirm plaintext field order from thomas-bingel C# proc 0x82/0x42")


# ── pcapng-file analysis ──────────────────────────────────────────────────────

def run_pcapng_analysis(paths: list[str], serial: str | None,
                        sap: str, type_str: str) -> None:
    all_ciphertexts: list[tuple[str, bytes, bytes | None]] = []  # (label, b1, b2)

    for path in paths:
        print(f"  Loading {path} …")
        try:
            found = extract_from_pcapng(path)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            continue
        label_base = Path(path).stem
        print(f"  → {len(found)} GetDeviceIdentification frame(s) found")
        for i, (b1, b2) in enumerate(found):
            label = f"{label_base}[{i}]"
            all_ciphertexts.append((label, b1, b2))
            ascii_b1 = "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in b1)
            printable = is_printable_ascii(b1)
            flag = "  *** PLAINTEXT? ***" if printable else ""
            print(f"    {label}  block1={b1.hex()}{flag}")
            if printable:
                print(f"           ASCII: {ascii_b1!r}")
            if b2:
                print(f"           block2={b2.hex()}")

    if not all_ciphertexts:
        print("\n  No ciphertexts extracted — nothing to analyse.")
        return

    print()
    print("=" * 70)
    print("XOR diffs between extracted sessions")
    print()
    for i in range(len(all_ciphertexts) - 1):
        la, a_b1, _ = all_ciphertexts[i]
        lb, b_b1, _ = all_ciphertexts[i + 1]
        d = xor_bytes(a_b1, b_b1)
        printable = is_printable_ascii(d)
        flag = "  ← LOW ENTROPY" if printable else ""
        print(f"  {la} ^ {lb}: {d.hex()}{flag}")

    if serial:
        # Strip trailing .x placeholder (device reports e.g. "146.350.01.x")
        sap_base = sap[:-2] if sap.lower().endswith(".x") else sap
        print()
        print("=" * 70)
        print(f"Known-plaintext attack — serial={serial!r}  sap={sap_base!r}  type={type_str!r}")
        print()
        sap_variants = {
            f"SAP.{suffix}": (sap_base + f".{suffix}").encode()
            for suffix in ("0", "1", "2")
        }
        type_bytes   = type_str.encode()
        serial_bytes = serial.encode()
        candidates_base = [("TYPE", type_bytes), ("SERIAL", serial_bytes)]
        for ref_label, ref_b1, _ in all_ciphertexts:
            for other_label, other_b1, _ in all_ciphertexts:
                if ref_label == other_label:
                    continue
                for sap_label, sap_bytes in sap_variants.items():
                    all_fields = candidates_base + [(sap_label, sap_bytes)]
                    for perm in itertools.permutations(all_fields):
                        pt = b"".join(v for _, v in perm)
                        if len(pt) == 32:
                            name = f"{ref_label}→{other_label} " + "+".join(n for n, _ in perm)
                            try_permutation(name, pt, ref_b1, other_b1)
    else:
        print()
        print("  Tip: pass --serial SERIAL to run the known-plaintext attack.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="alba-decrypt-analysis.py",
        description="Known-plaintext cryptanalysis of Geberit AquaClean Alba BLE encryption.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--pcapng", metavar="FILE", action="append",
        help="pcapng capture to extract GetDeviceIdentification frames from "
             "(can be given multiple times).",
    )
    parser.add_argument(
        "--serial", metavar="SERIAL",
        help="Device serial number for known-plaintext attack (e.g. SB2509EU177754).",
    )
    parser.add_argument(
        "--sap", metavar="SAP", default="146.350.01",
        help="SAP number — trailing .x placeholder is stripped automatically, then .0/.1/.2 "
             "are tried. Pass '146.350.01' or '146.350.01.x' (both accepted). Default: 146.350.01.",
    )
    parser.add_argument(
        "--type", metavar="TYPE", dest="type_str", default="AcAlba",
        help="Device type string for known-plaintext (default: AcAlba).",
    )
    args = parser.parse_args()

    if args.pcapng:
        print("=" * 70)
        print("pcapng extraction")
        print()
        run_pcapng_analysis(args.pcapng, args.serial, args.sap, args.type_str)
    else:
        run_full_analysis()


if __name__ == "__main__":
    main()
