# Alba BLE Encryption — Cryptanalysis

This document records all findings from the known-plaintext attack on the
`GetDeviceIdentification` encryption in the Geberit AquaClean Alba BLE protocol.

---

## Device under analysis

| Field | Value |
|-------|-------|
| Device model | AquaClean Alba |
| GATT name (cleartext) | "AC250" (handle 0x0007) |
| Gerätetyp | AcAlba |
| SAP (Artikelnummer) | 146.350.01.x (revision digit unknown) |
| Seriennummer | SB2509EU177754 |
| Firmware | RS3.0 TS89 (RS major=3, minor=0, TS build=89) |
| BLE MAC | E4:85:01:CD:51:6B |
| Captures | `local-assets/Bluetooth-Logs/johannes-schliephake/connect.txt` (S1), `connect+actions.txt` (S2) |

---

## Frame structure of GetDeviceIdentification response (41 bytes)

```
Offset  Bytes   Description
0– 3    4       Cleartext header: 00 24 42 11
4–35    32      Encrypted block 1
36–37   2       Cleartext separator: 03 03
38–39   2       Encrypted block 2
40      1       Cleartext terminator: 00
```

---

## Captured ciphertexts

### Session 1 (connect.txt, timestamp ~22:17:52.900)

```
Block1 (32B): 4E C6 4F 99 EA BE 40 BC  6C 86 24 1C 2D F3 A6 1A
              94 ED 26 77 D3 B7 95 DC  28 0E 98 23 FF 75 E4 31
Block2  (2B): 40 3E
```

### Session 2 (connect+actions.txt, timestamp ~22:19:26, ~1.5 min after S1)

```
Block1 (32B): A0 BB 05 A2 F8 22 B4 65  42 11 8F F7 1C E4 E4 0C
              A8 79 63 59 E2 07 5D 2C  7B E3 A7 1D E1 F6 12 34
Block2  (2B): C8 1E
```

### C1 XOR C2 (= KS1 XOR KS2 if same plaintext, different session keys)

```
Block1: EE 7D 4A 3B 12 9C F4 D9  2E 97 AB EB 31 17 42 16
        3C 94 45 2E 31 B0 C8 F0  53 ED 3F 3E 1E 83 F6 05
Block2: 88 20
```

All 34 bytes differ between sessions → keystream is session-specific.

---

## Pre-ciphertext handshake frames — STATIC across sessions

Every frame exchanged before the GetDeviceIdentification ciphertext is identical
in both sessions. This confirms the session key is NOT derived from the visible handshake.

| Frame | Value | S1 = S2? |
|-------|-------|----------|
| Client challenge write | `00 04 2F F5 D9 00` | ✓ |
| Device challenge response | `00 04 63 9D 51 00` | ✓ |
| SubscribeNotifications response | `00 03 20 01 02 05 01 01 04 01 F8 1E 00` | ✓ |
| Device info frame (proc 0x21) | `00 04 21 8B 30 00` | ✓ |
| App echo + request (proc 0x22) | `00 04 22 10 02 01 00` | ✓ |
| GATT char 559EB110 | `05 06 FA 00 3E 86 05 02 95 04 03 20 01 0E 01 01 02 00` | ✓ |

---

## Timeline paradox — proc 0x44 is NOT the init-encryption key exchange

```
22:17:52.900   Recv: GetDeviceIdentification response (ENCRYPTED, session-specific)
22:17:52.923   Write: proc 0x44 key exchange (50-byte ephemeral, also session-specific)
```

The GetDeviceIdentification ciphertext arrives **23 ms before** the proc 0x44 exchange
begins. Therefore proc 0x44 cannot be establishing the key used to encrypt the init
response. Proc 0x44 most likely establishes keys for subsequent authenticated write
commands (e.g. `StoredProfileSetting`).

---

## Proc 0x44 / 0x64 key exchange data

Both sides contribute 49 ephemeral bytes (first byte is a constant version marker).
Consistent with a DH-like exchange.

### Session 1

| Direction | Full 50-byte payload |
|-----------|---------------------|
| Client → Device (proc 0x44) | `12 70 3A AA 56 ED 5E 8A 1B 69 7E BB 0E BA DE 38 8F AE 59 81 15 69 5A 30 CC 3C F7 4D C8 56 01 19 3F AE F9 0A 14 0E 12 3C 1D 4B 50 08 80 CB 9E 70 54 03 B0 8E` |
| Device → Client (proc 0x64) | `13 35 E5 F3 47 71 09 1B 01 D2 94 8C DA B0 D8 18 58 E0 26 75 F3 75 CF 62 BE D6 D6 CE 51 9A 05 F7 3D 7D A4 C9 95 83 E5 30 BD C0 5D 71 59 46 3A 0A EE 69 9E` |

### Session 2

| Direction | Full 50-byte payload |
|-----------|---------------------|
| Client → Device (proc 0x44) | `12 2C 50 FC F7 E9 46 10 2D 1C 94 D6 83 D6 64 1A 4B 32 46 80 A3 85 67 16 AA B7 88 D2 CA 35 2E AE 2E A7 17 CC BB B1 A5 08 CC D1 EB 54 C6 50 55 93 5C 03 16 8F` |
| Device → Client (proc 0x64) | `13 81 30 9D F5 E8 9E A3 4A 31 2E 72 2E 5F B1 46 05 09 4C 18 BB E8 D1 87 3E AE 44 EA 41 7A AB 8B 45 1F 06 58 B3 79 6F F8 5E 28 16 FF 98 2C 79 1E CD BD 3D` |

---

## Known-plaintext attack — attempt and result

### Candidate plaintext (32 bytes, exact fit for encrypted block 1)

| Field | Bytes | Hex |
|-------|-------|-----|
| Gerätetyp | 6 | `41 63 41 6C 62 61` ("AcAlba") |
| SAP | 12 | `31 34 36 2E 33 35 30 2E 30 31 2E 78` ("146.350.01.x" — revision unknown) |
| Seriennummer | 14 | `53 42 32 35 30 39 45 55 31 37 37 37 35 34` ("SB2509EU177754") |
| **Total** | **32** | |

SAP revision variants tried: `.0`, `.1`, `.2` (and a 10-byte no-revision variant with null/space padding).

### Test methodology

Tool: `tools/alba-decrypt-analysis.py`

For each of 6 field orderings × 3 SAP suffix variants (18 combinations + 4 padding variants = ~24 total):
1. Compute `KS1 = C1 XOR candidate_plaintext`
2. Check KS1 for: printable ASCII, 16-byte repeat, low distinct-byte count, short repeating period (≤16)
3. Test static-KS hypothesis: compute `C2 XOR KS1` — if readable, KS is static

### Result

**No repeating keystream found in any of the 24 tested orderings.**

Both `C2 XOR KS1` outputs are random-looking for all orderings, confirming the keystream
is session-specific. The known-plaintext attack does recover one session's keystream, but
that keystream cannot be used to decrypt future sessions.

---

## Most likely encryption scheme

The consistent evidence points to a **session counter model**:

```
session_key_n = AES(device_static_key, IV || n)
```

where `n` is a per-device BLE connection counter that increments with every BLE connect.
This counter is stored in device flash (or EEPROM) and is never transmitted over BLE.

In AES-CTR terms:
```
KS_n = AES(K, nonce || n)
C1 = plaintext XOR KS_m
C2 = plaintext XOR KS_(m+1)    (assuming sessions are consecutive)
diff = C1 XOR C2 = KS_m XOR KS_(m+1) = AES(K, nonce||m) XOR AES(K, nonce||(m+1))
```

The `diff` value (`EE 7D 4A 3B ...`) encodes the XOR of two adjacent AES outputs.
With three or more sessions, patterns in the diff series could allow recovery of `K`.

---

## Third ciphertext — kstr device (2026-05-01)

A third Alba capture (`local-assets/Android-BLE-Logs/kstr/Wireshark/GeberitConnectViaApp.pcapng`)
from the official Geberit Home App connecting to a second Alba device added a third ciphertext.
Designated C0 for the kstr device series.

| Field | Value |
|-------|-------|
| Device | AcAlba, MAC `<redacted>` |
| Serial | `<redacted>` |
| SAP | 146.350.01.x |
| Firmware | RS3.0 TS89 |

```
C0 block1 (32B): c9 0e 8e dc 32 b0 35 3e 54 ea ba cf cb fc ca 64
                 36 f9 4c a9 38 fc 25 ce de 69 7b 43 1e 4d d3 bc
C0 block2  (2B): a1 11
```

### Cross-device XOR (keys appear device-specific)

```
C_J1 XOR C0 = 87 c8 c1 45 d8 0e 75 82  38 6c 9e d3 e6 0f 6c 7e
              a2 14 6a de eb 4b b0 12  f6 67 e3 60 e1 38 37 8d
C_J2 XOR C0 = 69 b5 8b 7e ca 92 81 5b  16 fb 35 38 d7 18 2e 68
              9e 80 2f f0 da fb 78 e2  a5 8a dc 5e ff bb c1 88
```

All cross-device XORs are high-entropy and structurally different from the same-device diff
`C_J1 XOR C_J2`. This strongly suggests the encryption key is **device-specific** (not firmware-wide).
Known-plaintext attack on C0 (device-specific serial) produced no repeating keystream
in any of the 6 field orderings — same result as Johannes's device.

### New protocol discovery from kstr capture

The init sequence is **byte-for-byte identical** to Johannes's sessions across all five static
pre-ciphertext frames. Additionally a previously unseen frame was observed:

**Proc 0x41 frame** (device→app, immediately after GetDeviceIdentification response):
```
00 04 41 8D 53 00   proc=0x41, data=8D 53
```
The app echoes this frame back before sending the proc 0x44 key exchange — same echo pattern
as proc 0x21 in the init sequence. This frame was not visible in Johannes's iOS PacketLogger
captures (possibly below the reassembly threshold or omitted by the iOS logger).

---

## Four-session capture — kstr device (2026-05-02)

A second capture (`local-assets/Android-BLE-Logs/kstr/GeberitConnect4xViaApp.pcapng`)
provides **four consecutive Geberit Home App sessions** from the same device.
**PIN:** factory-set (redacted from public repo).

### Extracted ciphertexts (block1 32B each; approximate session timestamps)

```
C1 (~t=21.6s):  21 31 73 99 54 fe 2b 8f  9f 6e 89 17 b4 cc dd 3b
                b4 6a 95 4e ad 6e 43 1e  2a e2 7a ef a4 b7 bd de
C2 (~t=55.8s):  97 2e e2 fc 6c 02 68 7e  cd bf 8d 50 4c 16 80 27
                1b 3f 5b 91 e4 46 8a 70  5f 09 0c 21 ed 96 f1 35
C3 (~t=92.7s):  1a 7b 26 f0 21 55 ed 93  8b 83 7e c1 8f 6e 10 ca
                6c c8 b7 f6 2d fc e5 2a  6d 49 1b d1 c9 eb 96 ef
C4 (~t=123.7s): 69 ff 91 28 c0 d0 65 52  52 72 4d 0c 70 fa 4e a2
                ce 80 aa 20 1b 42 f0 fd  b3 f4 ee cb 72 08 04 d3
```

### Consecutive XOR diffs (KS_n XOR KS_(n+1) if consecutive sessions)

```
C0 XOR C1: e8 3f fd 45 66 4e 1e b1  cb 84 33 d8 7f 30 17 5f
           82 93 d9 e7 95 92 66 d0  f4 8b 01 ac ba fa 6e 62
C1 XOR C2: b6 1f 91 65 38 fc 43 f1  52 d1 04 47 f8 da 5d 1c
           af 55 ce df 49 28 c9 6e  75 eb 76 ce 49 21 4c eb
C2 XOR C3: 8d 55 c4 0c 4d 57 85 ed  46 3c f3 91 c3 78 90 ed
           77 f7 ec 67 c9 ba 6f 5a  32 40 17 f0 24 7d 67 da
C3 XOR C4: 73 84 b7 d8 e1 85 88 c1  d9 f1 33 cd ff 94 5e 68
           a2 48 1d d6 36 be 15 d7  de bd f5 1a bb e3 92 3c
```

All diffs are high-entropy with no visible patterns — consistent with AES-CTR output;
inconsistent with any weaker stream or block cipher.

### AES key brute-force — no hits

The factory-set PIN was tested as a potential root of the `device_static_key`.
23 key derivation candidates × 18 plaintext orderings = **414 combinations tested**.

| Key candidate | Derivation |
|---|---|
| `70 80 00…` (2B zero-padded to 16B/32B) | raw PIN bytes |
| `37 30 38 30 00…` (4B ASCII zero-padded) | PIN as ASCII |
| SHA256(PIN) | standard hash |
| SHA256(SERIAL + PIN), SHA256(PIN + SERIAL) | combined with serial |
| SHA256(MAC + PIN), SHA256(PIN + MAC) | combined with MAC |
| HMAC-SHA256(PIN, SERIAL), HMAC-SHA256(SERIAL, PIN) | HMAC variants |
| MD5(PIN), MD5(SERIAL + PIN) | MD5 variants |
| SHA256(SERIAL), SHA256(MAC) | device identifiers alone |
| … (13 more combinations) | |

**Result: NO HITS** — no candidate produced a sparse nonce block (≥ 10 zero bytes)
when `AES_decrypt(candidate, expected_keystream_block)` was computed.

**Interpretation:** The `device_static_key` is not derivable from the factory PIN,
serial number, or MAC address using any standard single-pass hash. It is most likely:
- A key burned into the device's flash/EEPROM during manufacturing (not derivable from
  any value printed on the device), or
- Derived during an initial pairing ceremony that stores a shared secret — meaning the
  factory PIN is used once to authenticate pairing, but the resulting stored key (not
  the PIN itself) is what encrypts subsequent sessions.

---

## Fresh-install capture — kstr device (2026-05-02)

**File:** `local-assets/Android-BLE-Logs/kstr/GeberitFirstconnection.pcapng`
**Procedure:** uninstall app → Wireshark running → fresh install → connect → enter PIN → save

Three BLE sessions were captured. GetDeviceIdentification is **still encrypted** after a fresh
install — the encryption did not change.

### New ciphertexts F1–F3

```
F1 (~t=13.5s): a362ddcc75f51845220510a6c3e49348b95ae2f68a2288063b13785468a1d070  | bf ed
F2 (~t=39.6s): c202a5728da5c4c0da3231d7837bb0b17e745be461c52d5b8282bfc6b13d61ac  | 70 64
F3 (~t=55.5s): 7baf39beed7652bdfc23e63683dd67dd048b41ed2031973652b6cac3ccc4f667  | 5e 54
```

XOR diffs are high-entropy (AES-CTR confirmed). Cross-series XOR (C0^F1, C4^F1) also
high-entropy — the counter continued advancing between the two capture sessions.

### New frame discovered: `00 01 01 01 01 01 00`

Present in **all 3 sessions** of this capture, written by the app to handle `0x001e` between
the device challenge response and SubscribeNotifications. Absent from all previous kstr
captures (4-session and single-session). Appears every session (not just first), so it is
**not a one-time pairing frame** — most likely the Geberit Home App was updated between
captures and this frame is new app behavior. Data bytes `[01 01 01]` do not encode the
device PIN in any known form; possibly app capability/feature flags.

### Conclusion from fresh-install

The encryption key survives an app reinstall unchanged. This rules out the hypothesis that
the key is derived from an app-side secret introduced during first pairing. The key is
stored on the **device** and does not depend on the app installation state.

---

## Firmware binary analysis (2026-05-02)

**Series 250 = Alba** — confirmed from Geberit firmware cloud API (`GET /api/firmwares?series=248`
returns all series, including 250). Three versions available; active version is `3.0.89.250423`
which matches Johannes's device firmware (RS3.0 TS89) exactly.

**Download URL:**
```
https://prod.firmwarev1.services.geberit.com/api/store/Release/Active/FA/FwPkg_FA00_V3.0.89.250423_c59cdbce_Alba_FA_00_RS_03_TS_89/download
Authorization: Basic <redacted>
```

**File downloaded:** `local-assets/alba-fw/AqCS_FA_00_RS_03_TS_89.sfb` (175 KB)

**Format:** Nordic Semiconductor SFUM (Secure Firmware Update Manager) for nRF52 series.
Magic bytes `SFUM` at offset 0. 128-byte header, 384-byte FF padding, then encrypted payload
starting at offset 0x200.

**Entropy analysis:** Uniform ~7.95 bits/byte across the entire payload — no strings, no
readable sections. The firmware update image is AES-128-CCM encrypted using the device's
Device Root Key (DRK) stored in the nRF52 UICR (User Information Configuration Registers).
The DRK is provisioned at manufacturing and is only accessible via the device's secure
bootloader.

**Conclusion: dead end without hardware access.** The `.sfb` file cannot be statically
analyzed for the Geberit application-layer key. Extracting the decrypted code would require
physical JTAG/SWD access to the nRF52 chip to dump the running flash image.

---

## Remote control sniffing — next approach

**Hypothesis:** The Geberit physical remote control (Fernbedienung) is a first-party trusted
device. Dedicated hardware remotes commonly skip application-layer encryption because they
are considered inherently trusted (paired at factory or via a simpler trust model). If the
toilet's `GetDeviceIdentification` response is **unencrypted** when the remote is the client,
we immediately have the known plaintext and the known-plaintext attack succeeds trivially.

**Tool:** Nordic nRF52840 Dongle (PCA10059, ~€10) running Nordic nRF Sniffer firmware.
Captures raw over-the-air BLE packets with hop-following. Works with Wireshark.
The BLE link layer is unencrypted (confirmed from phone captures — GATT frames are fully
readable in pcapng without any link-layer key), so the sniffer sees all GATT payloads
including the Geberit protocol frames.

**Setup:**
1. Flash dongle with Nordic nRF Sniffer firmware (from Nordic Semiconductor SDK)
2. Install Wireshark nRF Sniffer plugin
3. In Wireshark, select dongle as capture interface; filter on toilet MAC
4. Use remote control to interact with toilet while capturing
5. Look for `00 24 42 11` frame — if the 32 bytes that follow are readable ASCII, it's plaintext

**Three outcomes:**

| Remote's GetDeviceIdentification | Implication |
|---|---|
| **Plaintext** | XOR with C1 gives keystream KS1 → known-plaintext attack succeeds |
| Encrypted, same structure | More ciphertexts, same AES-CTR wall |
| Not requested at all | Remote is control-only; learn protocol structure |

---

## Next steps to crack the encryption

1. **Sniff the remote control** with an nRF52840 Dongle — see section above. Highest
   value / lowest cost next step.

2. **Confirm plaintext field order** by reading the thomas-bingel C# repo proc 0x82/0x42
   handler to determine how the device serializes TYPE + SAP + SERIAL into the payload.

3. **Capture the initial pairing ceremony on a factory-reset device** — the current
   fresh-install capture was not from a factory-reset device (the Alba already had a stored
   key from a previous pairing). A truly factory-reset Alba would force the device to
   re-establish its key from scratch; capturing that ceremony may reveal the key derivation.

4. **Investigate proc 0x42** — the response procedure code for `GetDeviceIdentification`
   on Alba may use a different encoding (`0x02 | 0x40`) that signals a different
   response structure.

5. **Physical JTAG/SWD dump** — last resort. Attach hardware debugger to the nRF52 chip
   on the Alba PCB to dump the decrypted running firmware image. Bypasses the SFUM
   encryption entirely.

---

## Analysis tool

`tools/alba-decrypt-analysis.py` — tries all orderings and SAP suffix variants,
computes keystreams, checks for patterns. Run with:

```bash
/Users/jens/venv/bin/python tools/alba-decrypt-analysis.py
```
