---
name: Alba BLE Encryption Analysis
description: Confirmed pre-shared key, corrected misidentification of Encrypt Param Response as ciphertext, full Arendi Security.cs DH protocol understood
type: project
---

## Confirmed pre-shared key

`D1 21 8A 89 F6 0A C2 94 2D 44 20 79 74 50 97 BE`

Obtained from the Geberit.ComLib.Bluetooth assembly (class `am`, field `t`).
Verified computationally against Johannes S1 capture:
- auth_key = HKDF-SHA256(pre-shared key, nonce1, [], 16) = `00 8C 88 95 78 81 37 C0 27 03 3D F9 0B 33 0C A6`
- AES-CMAC(auth_key, client_public) = `AE F9 0A 14 0E 12 3C 1D 4B 50 08 80 CB 9E 70 54` → **exact match** to captured frame 0x12

Source: ComLib.Bluetooth (ArendiSecurity implementation).

## Critical correction — prior analysis was wrong

The 5-session known-plaintext attack and "AES-CTR session counter model" were based on a misidentified frame:

- **Frame at `22:17:52.900` in `connect.txt`** — previously called "encrypted GetDeviceIdentification response"
- **Actually:** Encrypt Parameter Response (Security frame type `0x11`) — contains nonce1 (16B) + nonce2 (16B) sent in **CLEARTEXT**
- The "high entropy bytes that changed per session" were just the random nonces by design
- No session counter exists; the per-session key variance is from the Curve25519 ephemeral DH, not a counter

## True protocol: Arendi Security.cs DH handshake

Full documentation: `docs/developer/alba-ble-encryption.md`

Sequence (inside Geberit outer framing `[ctrl|len|payload|CRC16|0x00]`):
1. App→Device: Security(0x00) — Version Request
2. Device→App: Security(0x01, fw_version) — Version Response
3. App→Device: Security(0x10) — Encrypt Param Request
4. **Device→App: Security(0x11, nonce1[16], nonce2[16], keyset_bitmask[2])** — Encrypt Param Response ← was misidentified
5. App computes: `auth_key = HKDF(pre-shared key, nonce1, [], 16)`, `client_CMAC = AES-CMAC(auth_key, client_public)`
6. App→Device: Security(0x12, client_public[32], client_CMAC[16], keyset_id[1]) — Key Exchange Request
7. Device verifies CMAC (same HKDF with device's pre-shared key copy)
8. Device→App: Security(0x13, server_public[32], server_CMAC[16]) — Key Exchange Response
9. Both: `shared_secret = Curve25519-DH(my_private, peer_public)`
   `key_material = HKDF(shared_secret, nonce1, [], 32)`
   `rx_key = key_material[0:16]`, `tx_key = key_material[16:32]`
   ciphers = AES-CTR(nonce=nonce2, key=rx/tx_key)
10. All subsequent procs wrapped in Security(0x20, AES-CTR(plaintext))

## What this enables

With the pre-shared key, the bridge can:
- Complete the full DH handshake with any Alba device
- Establish its own ephemeral session keys
- Send/receive encrypted procs (GetDeviceIdentification, GetSystemParameterList, etc.)

## What this does NOT enable

- Passive decryption of existing captures (session keys are ephemeral, private key not transmitted)
- Johannes/kstr session logs remain undecryptable without the app's private key

## Johannes S1 handshake data (connect.txt)

| Field | Value |
|-------|-------|
| nonce1 | `4E C6 4F 99 EA BE 40 BC 6C 86 24 1C 2D F3 A6 1A` |
| nonce2 | `94 ED 26 77 D3 B7 95 DC 28 0E 98 23 FF 75 E4 31` |
| client_public | `70 3A AA 56 ... 56 01 19 3F` (32 bytes) |
| client_CMAC | `AE F9 0A 14 0E 12 3C 1D 4B 50 08 80 CB 9E 70 54` |
| server_public | `35 E5 F3 47 ... 9A 05 F7 3D` (32 bytes) |

## Next step: implement AriendiSecurity Python class

```python
# cryptography library components needed:
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.cmac import CMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
```

Wire into `ESPHomeAPIClient`/`BluetoothLeConnector` for the Alba GATT path (559EB001 write, 559EB002 notify).

**Why:** The pre-shared key is now confirmed. All crypto primitives are standard and available in Python `cryptography`. The GATT channel for Alba is already identified (Variant A: 559EB001/559EB002). Alba proc codes are not yet confirmed but expected to match Mera Comfort.
