# Alba BLE Encryption — Protocol Analysis

This document records all findings on the Geberit AquaClean Alba BLE encryption protocol,
including a correction of an earlier misidentification and the confirmed authKey.

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

## Correction of earlier misidentification (2026-05-06)

### What was previously believed

Prior analysis (before APK analysis) identified the 34-byte payload at timestamp
`22:17:52.900` as an *encrypted GetDeviceIdentification response*.  A 5-session
known-plaintext attack was conducted against that payload.  The attack failed to find
a repeating keystream, leading to the hypothesis that the encryption uses a
*device-internal session counter* to derive a new key per BLE session.

### What is actually happening

After extracting the Geberit Bluetooth library from the Android app
(`local-assets/geberit-home/sources/ComLib.Bluetooth/`), the correct interpretation is:

**The frame at `22:17:52.900` is the Encrypt Parameter Response (Security frame type
`0x11`) — it contains `nonce1 (16 bytes) + nonce2 (16 bytes)` sent in CLEARTEXT.**
This is step 2 of the DH key exchange handshake, not an encrypted response.

The full 41-byte ATT notification is:

```
Byte  Value    Meaning
 0    00       ctrl byte (Geberit outer framing)
 1    24       len = 36 bytes follow (including CRC)
 2    42       ctx byte (Arendi BLE channel ID)
 3    11       Security frame type: Encrypt Parameter Response
 4–19 <rand>   nonce1 (16 bytes, random per session)
20–35 <rand>   nonce2 (16 bytes, random per session)
36–37 03 03    keyset_bitmask (LE16, indicates supported key sets)
38–39 40 3E    CRC-16
40    00       terminator
```

**The 32 bytes previously labeled "ciphertext Block1" are nonce1 + nonce2 — sent
in plaintext by the device as part of the handshake.** Because they are random per
session, they appeared "session-specific" and "high-entropy" — which is correct,
but not because they are AES output.

The "AES-CTR session counter model" was wrong.  The variance in those bytes is simply
the expected randomness of the nonce generation, not a counter-keyed cipher.

### S4 "header byte anomaly" re-interpreted

Session S4 (file `2.txt`) showed `len = 0x1D = 29` instead of the normal `36`.
With the corrected understanding:
- `len = 29` = 27 bytes data + 2 bytes CRC
- `27 = 1(ctx) + 1(frame_type) + 16(nonce1) + ?`
  — nonce2 is truncated (8 bytes) in this session, or the frame format differs.
  The prior failed connection attempt for this session may have triggered a
  protocol fallback that changed the frame length.

---

## True protocol: Arendi Security.cs DH-based session encryption

The Alba uses the full Arendi BLE security protocol implemented in
`Geberit.ComLib.Bluetooth.Crypto.Security` and `SecurityServer`.

### Session handshake sequence

All frames are wrapped in `[ctrl(1) | len(1) | data(len-2) | CRC16(2) | 0x00]` at the
Geberit outer layer; the Arendi Security layer sits inside.

```
1. App → Device:  [ctrl:00 len:01] + Security(type=0x00)          Version Request
2. Device → App:  [ctrl:00 len:07] + Security(type=0x01, fw_version, protocol_v)
                                                                    Version Response

3. App → Device:  [ctrl:00 len:03] + Security(type=0x10)          Encrypt Param Request
4. Device → App:  [ctrl:00 len:24] + Security(type=0x11,
                    nonce1[16], nonce2[16], keyset_bitmask[2])      Encrypt Param Response
                  ← The frame previously misidentified as "ciphertext"

5. App computes:
     auth_key = HKDF-SHA256(IKM=authKey, salt=nonce1, info=b"", length=16)
     client_CMAC = AES-CMAC(key=auth_key, message=client_public)

6. App → Device:  [ctrl:00 len:33] + Security(type=0x12,
                    client_public[32], client_CMAC[16], keyset_id[1])
                                                                    Key Exchange Request
7. Device verifies CMAC (same HKDF computation with device's authKey)
8. Device computes:
     server_CMAC = AES-CMAC(key=auth_key, message=server_public)
9. Device → App:  [ctrl:00 len:31] + Security(type=0x13,
                    server_public[32], server_CMAC[16])             Key Exchange Response

10. Both sides:
     shared_secret = Curve25519_DH(my_private, peer_public)
     key_material  = HKDF-SHA256(IKM=shared_secret, salt=nonce1, info=b"", length=32)
     rx_key  = key_material[0:16]
     tx_key  = key_material[16:32]
     rx_cipher = AES-CTR(nonce=nonce2, key=rx_key)   # decrypt incoming
     tx_cipher = AES-CTR(nonce=nonce2, key=tx_key)   # encrypt outgoing

11. All subsequent procs (GetDeviceIdentification, etc.) are encrypted:
    Send:     [ctrl:00 len:N+3] + Security(type=0x20, AES-CTR-encrypt(proc_data))
    Receive:  Security(type=0x20, AES-CTR-decrypt(ciphertext)) → plaintext proc data
```

### AES-CTR details (aj.cs)

The AES-CTR implementation in `aj.a` (inner class):
- Uses AES-ECB to generate keystream blocks
- Counter = last 4 bytes of the 16-byte nonce (big-endian), incremented per block
- Keystream XOR'd with plaintext/ciphertext

### HKDF-SHA256 details (al.cs)

`al.a(IKM, salt, info, length)` = standard RFC 5869 HKDF-SHA256.
- Extract: `PRK = HMAC-SHA256(key=salt, data=IKM)`
- Expand: `T(i) = HMAC-SHA256(key=PRK, data=T(i-1) || info || [i])`

---

## Confirmed authKey

### Extraction method

The Geberit Bluetooth library was extracted from the Android app
(native assembly store, LZ4-compressed) and analyzed with `ilspycmd`.

The authKey is stored in class `am`, field `t`, as a FieldRVA byte array literal
in the IL:

```il
// from /tmp/security_il.txt (lines 123904–123907)
.field assembly static initonly valuetype am/f t at I_0002ED10
.data cil I_0002ED10 = bytearray (
    d1 21 8a 89 f6 0a c2 94  2d 44 20 79 74 50 97 be
)
```

The `Security` static constructor initializes `Security::v` (the authKey) from
this FieldRVA via `RuntimeHelpers.InitializeArray`.

### Verification

**Verified computationally against the Johannes S1 capture (connect.txt).**

| Parameter | Value |
|-----------|-------|
| authKey | `D1 21 8A 89 F6 0A C2 94 2D 44 20 79 74 50 97 BE` |
| nonce1 (from frame 0x11) | `4E C6 4F 99 EA BE 40 BC 6C 86 24 1C 2D F3 A6 1A` |
| client_public (from frame 0x12) | `70 3A AA 56 ED 5E 8A 1B 69 7E BB 0E BA DE 38 8F` `AE 59 81 15 69 5A 30 CC 3C F7 4D C8 56 01 19 3F` |
| auth_key = HKDF(authKey, nonce1, [], 16) | `00 8C 88 95 78 81 37 C0 27 03 3D F9 0B 33 0C A6` |
| computed CMAC = AES-CMAC(auth_key, client_public) | `AE F9 0A 14 0E 12 3C 1D 4B 50 08 80 CB 9E 70 54` |
| expected CMAC (from captured frame 0x12) | `AE F9 0A 14 0E 12 3C 1D 4B 50 08 80 CB 9E 70 54` |

**MATCH confirmed.** The device accepted the key exchange request (replied with 0x13),
which independently confirms the CMAC was valid.

Verification script: `/tmp/verify_auth_key.py`

### What the authKey enables

The authKey is used **only for mutual authentication** during the key exchange
(steps 5–7 above).  It does not directly encrypt session data.

With the authKey, the bridge can:
1. Complete the Security.cs handshake and authenticate with the Alba device
2. Establish its own DH session keys (bridge generates its own keypair)
3. Send and receive encrypted procs (GetDeviceIdentification, GetSystemParameterList, etc.)

Without the authKey, the handshake CMAC check fails and the device closes the connection.

### What the authKey alone does NOT enable

- Passive decryption of previously captured sessions (session keys are ephemeral DH)
- Decryption of captures from `connect.txt`, `connect+actions.txt`, kstr captures, etc.

---

## Captured handshake data (Johannes S1, connect.txt)

### Encrypt Param Response (device → app, 22:17:52.900)

| Field | Value |
|-------|-------|
| nonce1 | `4E C6 4F 99 EA BE 40 BC 6C 86 24 1C 2D F3 A6 1A` |
| nonce2 | `94 ED 26 77 D3 B7 95 DC 28 0E 98 23 FF 75 E4 31` |
| keyset_bitmask | `0x0303` |
| CRC | `40 3E` |

### Key Exchange Request (app → device, 22:17:52.923)

| Field | Value |
|-------|-------|
| client_public | `70 3A AA 56 ED 5E 8A 1B 69 7E BB 0E BA DE 38 8F AE 59 81 15 69 5A 30 CC 3C F7 4D C8 56 01 19 3F` |
| client_CMAC | `AE F9 0A 14 0E 12 3C 1D 4B 50 08 80 CB 9E 70 54` |
| keyset_id | `03` |
| CRC | `B0 8E` |

### Key Exchange Response (device → app, 22:17:52.977)

| Field | Value |
|-------|-------|
| server_public | `35 E5 F3 47 71 09 1B 01 D2 94 8C DA B0 D8 18 58 E0 26 75 F3 75 CF 62 BE D6 D6 CE 51 9A 05 F7 3D` |
| server_CMAC | `7D A4 C9 95 83 E5 30 BD C0 5D 71 59 46 3A 0A EE` |

---

## Geberit outer framing (BLE GATT level)

All data written/notified over the 559EB001/559EB002 GATT characteristics uses:

```
[ctrl(1)] [len(1)] [payload(len-2 bytes)] [CRC16(2)] [0x00 terminator]
```

- `ctrl` is always `00` in observed captures.
- `len` = payload bytes + 2 (CRC counts toward length).
- Long messages split across multiple BLE notifications; reassemble using total `len`.
- `0x00` terminator is sent as a separate BLE notification for longer messages.

Short session-init frames (challenge `2F F5 D9`, etc.) appear to use a simpler format
without CRC; exact boundary not determined.

---

## Static pre-handshake frames

These frames are byte-identical across all captured sessions (Johannes S1–S5, kstr):

| Frame | Value | Meaning |
|-------|-------|---------|
| App challenge write | `00 04 2F F5 D9 00` | Session start challenge |
| Device response | `00 04 63 9D 51 00` | Challenge response |
| Device→app frame | `00 03 20 01 02 05 01 01 04 01 F8 1E 00` | Likely version/capability |
| Device→app frame | `00 04 21 8B 30 00` | Some proto frame |

The static nature of these frames is expected: they are fixed protocol constants that
precede the random-nonce phase.

---

## BLE link-layer security model — confirmed findings (2026-05-03)

### Geberit does NOT use BLE Secure Connections

Standard BLE Secure Connections (LESC) would mean:
- ECDH P-256 key exchange during pairing
- PIN as passkey for MITM protection
- AES-128-CCM encrypts the link layer
- Long Term Key (LTK) stored for future sessions

**Geberit's actual implementation is different.** Zero SMP (Security Manager Protocol)
frames appear in any capture — including `GeberitFirstconnection.pcapng`, which was taken
during a fresh app install when the PIN was entered for the first time. LESC pairing
_requires_ SMP PDUs on L2CAP CID 0x0006; their complete absence rules out standard
BLE pairing.

Verified by counting L2CAP CIDs across the fresh-install pcapng:

```
ATT  (CID 0x0004):  683 frames
SIG  (CID 0x0005):   12 frames
SMP  (CID 0x0006):    0 frames  ← no pairing/LESC
```

### What Geberit actually does

```
BLE link layer:       unencrypted (no BLE Secure Connections pairing)
Application layer:    Arendi Security.cs DH + AES-CTR with authKey authentication
PIN role:             used only at Geberit app layer (proc 0x44 key exchange)
                      — the BLE Security Manager never sees the PIN
```

The device accepts BLE connections without any link-layer pairing ceremony. The
session encryption is entirely at the Geberit/Arendi application layer.

### Security comparison

| Property | Standard BLE (LESC) | Geberit Alba |
|----------|--------------------|----|
| Link-layer encryption | AES-128-CCM | None |
| OTA sniffability | Opaque without LTK | Fully readable |
| Key exchange | ECDH P-256 (standard) | Arendi Curve25519 DH + authKey |
| PIN scope | MITM protection for pairing | App-layer only (proc 0x44) |
| Session key | ECDH shared secret | Curve25519 DH shared secret + HKDF |

### Caveat — definitive proof requires OTA sniff

All existing captures are from the HCI layer (Android BTSnoop, iPhone PacketLogger).
The Bluetooth controller decrypts link-layer frames before passing them to the host,
so HCI captures are always readable regardless of whether link-layer encryption is
active. The zero SMP frame count is strong evidence but not a mathematical proof.

**An nRF52840 Dongle OTA sniff is the only definitive test.**

### BLE pairing — confirmed Just Works (Linux, 2026-05-03)

**Confirmed:** `bluetoothctl pair <alba-mac>` on a Raspberry Pi completed with
`Bonded: yes` and no PIN prompt. The device's IO capability is `NoInputNoOutput` →
BT spec §3.5.3.2 selects "Just Works" automatically.

| Platform | Result |
|----------|--------|
| **Linux (Raspberry Pi)** | **Just Works — no PIN, Bonded: yes** ✅ confirmed 2026-05-03 |
| Android | not yet tested |
| Windows | not yet tested |
| iOS | not testable (CoreBluetooth hides raw pairing) |

**What this means:**

- The 4-digit PIN is **only** at the Geberit application layer (proc 0x44). The
  BLE Security Manager never sees it — confirmed at both the HCI level (zero SMP frames)
  and by the pairing test (no PIN prompt).
- Just Works pairing is trivially MITMable — any BLE adapter acting as a peripheral
  toward the iPhone can accept the exchange and relay traffic in plaintext.

---

## Firmware binary analysis (2026-05-02)

**Series 250 = Alba** — confirmed from Geberit firmware cloud API.

**File:** `local-assets/alba-fw/AqCS_FA_00_RS_03_TS_89.sfb` (175 KB)

**Format:** Nordic Semiconductor SFUM (Secure Firmware Update Manager) for nRF52 series.
Payload is AES-128-CCM encrypted using the device's Device Root Key (DRK) stored in
the nRF52 UICR. The DRK is provisioned at manufacturing; not accessible without hardware.

**Conclusion: dead end without JTAG/SWD access to the nRF52.**

---

## Path forward: implementing Alba support in the bridge

With the authKey confirmed, all cryptographic components are available:

| Component | Status |
|-----------|--------|
| Curve25519 keypair generation | Python: `cryptography` lib, `X25519PrivateKey.generate()` |
| HKDF-SHA256 | Python: `cryptography.hazmat.primitives.hashes.HKDF` |
| AES-CMAC | Python: `cryptography.hazmat.primitives.cmac.CMAC` |
| AES-CTR | Python: `cryptography.hazmat.primitives.ciphers.Cipher` |
| authKey | `D1 21 8A 89 F6 0A C2 94 2D 44 20 79 74 50 97 BE` |

**Implementation plan:**
1. Implement `AriendiSecurity` class in Python mirroring `Security.cs`:
   - `initialize_data_exchange()` performs the 4-step handshake
   - `encrypt(data)` wraps outgoing data in Security frame 0x20
   - `decrypt(data)` unwraps incoming Security frame 0x20
2. Wire into `ESPHomeAPIClient` / `BluetoothLeConnector` for the Alba GATT path
   (559EB001 write, 559EB002 notify)
3. Determine if Alba uses the same proc codes as Mera Comfort (0x0D, 0x82, etc.)
   — likely yes, as the Security layer is transport-transparent
4. Test with live Alba device using `tools/geberit-ble-probe.py` extended for
   the Arendi Security handshake

**Barrier remaining:** The proc codes inside the encrypted sessions are not yet confirmed
for the Alba.  The first successful connection will reveal the proc structure.

---

## Frame reassembly: Alba vs Mera Comfort

The Mera Comfort and Alba use fundamentally different framing strategies.
Understanding this explains why the Alba probe script (`tools/alba-ble20-probe.py`)
needs no frame-assembly code of its own.

### Mera Comfort — application-layer multi-frame assembly

```
BLE notification (20 bytes, fixed)
  → FrameService: accumulate SINGLE / FIRST / CONS frames → complete message
    → AquaCleanClient: parse procedure response
```

Each BLE notification carries exactly one 20-byte Geberit frame.  Long responses
are split by the device into a FIRST frame followed by one or more CONS frames.
`FrameService.py` tracks the expected CONS count and holds the partial message
until all frames arrive, then delivers the assembled payload to `AquaCleanClient`.

### Alba — transport-layer reassembly inside `AriendiSecurity`

```
BLE notification (variable length)
  → AriendiSecurity._rx_buf: accumulate raw bytes until 0x00 delimiter
    → complete COBS frame extracted
      → COBS decode → HDLC strip → CRC-16 verify → AES-CTR decrypt
        → data_received_handlers fires with complete, decrypted Ble20 message
          → application code (probe script / future bridge client)
```

`AriendiSecurity` maintains `_rx_buf` (a `bytearray`) and `feed_att_bytes()`.
Every BLE notification is appended to the buffer.  `_process_rx_buf()` then
scans for `0x00` delimiters (COBS frame boundaries), extracts complete COBS
frames one at a time, and decrypts each one.  Only after decryption does
`data_received_handlers` fire — delivering one complete, decrypted Ble20
application message per call.

This means **by the time the probe script's `_on_data` callback fires, the
message is already fully assembled and decrypted**.  No further reassembly is
needed at the application layer.

### Why individual Ble20 messages stay small

The Ble20 protocol is also designed to avoid large multi-part responses.
`DataPointInventory` sends one `0x02` InventoryData frame **per DpId** rather
than batching all DpIds into a single large response.  Each read/write operation
is similarly one request → one response.  This keeps individual Ble20 messages
short enough that COBS-framed, AES-CTR-encrypted form fits comfortably within
a single or small number of BLE notifications.

### Summary

| Aspect | Mera Comfort | Alba |
|--------|-------------|------|
| Assembly layer | Application (`FrameService.py`) | Transport (`AriendiSecurity._rx_buf`) |
| Frame delimiter | Fixed 20-byte BLE packets + FIRST/CONS type bytes | `0x00` COBS boundary bytes |
| What application code receives | Raw procedure bytes after FIRST+CONS assembly | Decrypted Ble20 message, fully assembled |
| Application-layer multi-frame | Yes (FIRST + N×CONS per response) | No — one message per DpId operation |

---

## Source files

| File | Description |
|------|-------------|
| `local-assets/geberit-home/sources/ComLib.Bluetooth/Geberit.ComLib.Bluetooth.Crypto/Security.cs` | Client-side security protocol |
| `local-assets/geberit-home/sources/ComLib.Bluetooth/Geberit.ComLib.Bluetooth.Crypto/SecurityServer.cs` | Device-side security protocol |
| `local-assets/geberit-home/sources/ComLib.Bluetooth/aj.cs` | AES-CTR cipher + AES-CMAC |
| `local-assets/geberit-home/sources/ComLib.Bluetooth/ak.cs` | Curve25519 ECDH |
| `local-assets/geberit-home/sources/ComLib.Bluetooth/al.cs` | HKDF-SHA256 |
| `local-assets/geberit-home/sources/ComLib.Bluetooth/am.cs` | Contains FieldRVA for authKey |
| `/tmp/security_il.txt` | Full IL of the Geberit Bluetooth library (authKey at line 123904) |
| `tools/alba-decrypt-analysis.py` | Earlier analysis tool (now superseded) |
| `/tmp/verify_auth_key.py` | Python CMAC verification script |
