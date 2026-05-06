# Next Steps: Adding Alba Support to the Bridge

This document describes what is needed to integrate the AquaClean Alba into the
bridge as a first-class supported device, beyond the current state of:

- GATT profile detection (Variant A → unsupported-device screen in HACS config flow)
- Arendi Security handshake working (confirmed by `tests/test_arendi_security.py`
  and `tests/test_pcapng_handshake.py`)
- `tools/alba-ble20-probe.py` for manual DpId exploration

---

## Current state

| Layer | Status |
|-------|--------|
| BLE connection + GATT discovery | ✅ working — Variant A profile detected |
| Arendi Security handshake | ✅ implemented (`AriendiSecurity.py`) |
| AES-CTR encrypted frame exchange | ✅ implemented |
| Ble20 application protocol (DpId Read/Write/Inventory/Notify) | ❌ not yet |
| Bridge polling loop for Alba | ❌ not yet |
| HACS coordinator for Alba | ❌ not yet |

---

## Step 1 — Ble20 client (`Ble20Client.py`)

Create `aquaclean_console_app/bluetooth_le/LE/Ble20Client.py` implementing the
application protocol that runs inside the encrypted channel.

### Protocol primitives needed

| Command | Code | Direction | Description |
|---------|------|-----------|-------------|
| `DataPointInventory` | `0x00` | C→D | Request list of all supported DpId addresses |
| `InventoryResponse` | `0x01` | D→C | One frame per DpId: `[0x01, lo, hi, instance, datatype]` |
| `Read` | `0x02` | C→D | Read one DpId: `[0x02, lo, hi, instance]` |
| `ReadResponse` | `0x03` | D→C | `[0x03, lo, hi, instance, ...data...]` |
| `Write` | `0x04` | C→D | Write one DpId: `[0x04, lo, hi, instance, ...data...]` |
| `WriteResponse` | `0x05` | D→C | `[0x05, lo, hi, instance, status]` |
| `EnableNotification` | `0x07` | C→D | Subscribe to unsolicited updates for a DpId |
| `DisableNotification` | `0x08` | C→D | Unsubscribe |
| `NotifyData` | `0x34` | D→C | Unsolicited push when a subscribed DpId changes |

### Key DpIds for bridge polling

| DpId (decimal) | Name | Notes |
|----------------|------|-------|
| 60 | `USER_PRESENT` | `AC_STATUS_USER_PRESENT = 65596` truncates to `ushort` 60 |
| 563 | anal shower state | from `BLE_COMMAND_REFERENCE.md` |
| 1009 | `TRIGGER_LID_LIFTING` | ToggleLid command: write `[0x01]` |

Run `tools/alba-ble20-probe.py --device <MAC>` to retrieve the full inventory from
the real device before hard-coding the DpId list.

### Suggested interface

```python
class Ble20Client:
    async def inventory(self) -> dict[int, dict]:
        """Send DataPointInventory; return {dpid: {instance, datatype}} for all supported DpIds."""

    async def read(self, dp_id: int, instance: int = 0) -> bytes:
        """Read one DpId; return raw bytes."""

    async def write(self, dp_id: int, value: bytes, instance: int = 0) -> bool:
        """Write one DpId; return True on success."""

    async def enable_notification(self, dp_ids: list[int]) -> None:
        """Subscribe to unsolicited updates."""

    async def poll_state(self) -> dict:
        """Read the set of DpIds that map to bridge device_state fields."""
```

`Ble20Client` receives decrypted bytes via a callback registered with
`AriendiSecurity.data_received_handlers` — identical to how `FrameService`
receives bytes in the Mera Comfort path.  No frame reassembly is needed at this
layer: `AriendiSecurity._process_rx_buf()` already delivers one complete Ble20
message per callback invocation.

---

## Step 2 — Mock upgrade (`--mode ble20`)

Add `--mode ble20` to `tools/mock-geberit-alba.py` alongside the existing
`--mode handshake`.  The mock runs the full Arendi Security handshake and then
dispatches the decrypted Ble20 messages from the application layer.

### In-memory DpId store (equivalent to `MockBaseProduct.DataMemory` in C#)

```python
dp_store: dict[int, bytes] = {
    60:   b'\x00',    # USER_PRESENT = false
    563:  b'\x00',    # anal shower = off
    1009: b'\x00',    # lid trigger = 0
}
notify_subscribed: set[int] = set()
```

### Dispatch table (after decrypting each incoming Ble20 frame)

| Incoming `cmd` | Action |
|----------------|--------|
| `0x00` — Inventory | Respond with one `0x01` frame per DpId in `dp_store` |
| `0x02` — Read | Look up DpId in `dp_store`, respond with `0x03` |
| `0x04` — Write | Update `dp_store[dpid] = value`; respond `0x05 OK`; if dpid in `notify_subscribed` push `0x34` |
| `0x07` — EnableNotification | Add dpid to `notify_subscribed` |
| `0x08` — DisableNotification | Remove dpid from `notify_subscribed` |

This maps directly to `MockBaseProduct.Read()`, `Write()`,
`EnableParameterNotification()`, and the `m()` notification-trigger helper — the
C# data model is the same, just expressed as a Python dict.

### When to implement

Implement `--mode ble20` in the same session as `Ble20Client.py`.  Both sides
(client and mock) can then be tested in-process first — the same pattern as
`tests/test_arendi_security.py` tests the handshake — before any BLE hardware
is involved.

---

## Step 3 — Bridge wiring

### `_post_connect()` in `BluetoothLeConnector.py`

After `perform_handshake()` succeeds (i.e. `arendi_handshake_done == True`):

1. Instantiate `Ble20Client(arendi_security)`.
2. Call `await client.inventory()` to build the DpId map.
3. Store the client as `connector.ble20_client`.

### `AquaCleanClient` (or a new `AlbaClient`)

Two options:

**Option A — new `AlbaClient`** (recommended): a separate class parallel to
`AquaCleanClient` that calls `Ble20Client.poll_state()` instead of
`GetSystemParameterList`.  The coordinator / `ServiceMode` selects the client
class based on `connector.is_variant_a`.

**Option B — extend `AquaCleanClient`**: add an Alba branch inside
`get_state()` that switches on `self.connector.ble20_client is not None`.  Less
clean but fewer files.

### `device_state` fields

Map the polled DpId values to the same `device_state` keys already used for the
Mera Comfort (`user_sitting`, `anal_shower_running`, etc.) where semantics match.
Alba-only fields (if any) get new keys following the existing naming convention.

---

## Step 4 — HACS coordinator

The HACS coordinator already has the Alba detection gate:

```python
if self.connector.is_variant_a and not self.connector.arendi_handshake_done:
    raise UpdateFailed("Alba not yet supported")
```

Once step 3 is done, the coordinator drops through this gate and polls normally.
No structural coordinator changes are needed — the same `_do_poll()` flow applies.

---

## Testing sequence

1. `tests/test_arendi_security.py` — passes already; must stay green
2. New `tests/test_ble20_client.py` — in-process test: `Ble20Client` ↔ `_MockBle20Server` via asyncio queues (same pattern as `test_arendi_security.py`)
3. `tools/mock-geberit-alba.py --mode ble20` on Raspberry Pi ↔ bridge on Mac — live BLE test without real Alba hardware
4. Bridge against real Alba device (E4:85:01:CD:6B:04 or E4:85:01:CD:B0:08)

---

## Open questions

- Which DpIds does the real Alba inventory return? Run `alba-ble20-probe.py` once before step 1.
- Does the bridge need to send `EnableNotification` for USER_PRESENT, or is polling sufficient?  
  Polling is simpler; notifications are more responsive.  The probe's `--watch` flag can test notification delivery on real hardware.
- Does the Alba use the same `AriendiSecurity` `aquacleanBridgeId` as the kstr device?  
  Confirmed for kstr (E4:85:01:CD:6B:04) via `tests/test_pcapng_handshake.py`.  
  The B0:08 device has not been tested — run the pcapng test once a capture is available.
