"""In-process tests for Ble20Client.

No BLE hardware required.  _FakeConnector routes plaintext frames between
Ble20Client and _MockBle20Server via asyncio queues — no Arendi Security
layer, no BlueZ.

Pattern mirrors test_arendi_security.py.
"""

import asyncio
import logging
import os
import struct
import sys
import traceback

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Register SILLY/TRACE log levels before any bridge import (myEvent uses logger.trace).
def _add_level(name: str, value: int) -> None:
    logging.addLevelName(value, name)
    setattr(logging, name, value)
    setattr(logging.Logger, name.lower(),
            lambda self, msg, *a, **kw: self.log(value, msg, *a, **kw))

import logging as _logging_module
_add_level('SILLY', 4)
_add_level('TRACE', 5)
logging.basicConfig(level=logging.WARNING)

from aquaclean_console_app.bluetooth_le.LE.Ble20Client import (
    Ble20Client, encode_address, decode_address,
)
from aquaclean_console_app.bluetooth_le.LE.command_id import CommandId
from aquaclean_console_app.bluetooth_le.LE.dp_type import DpType
from aquaclean_console_app.myEvent.myEvent import EventHandler


# ---------------------------------------------------------------------------
# _FakeConnector — minimal connector stub for Ble20Client
# ---------------------------------------------------------------------------

class _FakeConnector:
    """Routes frames between Ble20Client (consumer) and _MockBle20Server (producer)."""

    def __init__(self):
        self.data_received_handlers = EventHandler()
        self._to_server: asyncio.Queue[bytes] = asyncio.Queue()

    async def send_message(self, data: bytes) -> None:
        await self._to_server.put(data)

    async def deliver_to_client(self, data: bytes) -> None:
        await self.data_received_handlers.invoke_async(data)


# ---------------------------------------------------------------------------
# _MockBle20Server — in-process Ble20 server
# ---------------------------------------------------------------------------

class _MockBle20Server:
    """Handles Ble20 frames from a _FakeConnector and delivers responses back."""

    _DEFAULT_STORE = [
        # (dp_id, inst, version, datatype, min_s, max_s, behavior, init_bytes)
        (60,   None, 1, DpType.OffOn,  0, 1,   1, b'\x00'),
        (563,  None, 1, DpType.OffOn,  0, 1,   2, b'\x00'),
        (564,  None, 1, DpType.Enum,   0, 5,   1, b'\x01'),   # at-rest value = 1
        (1008, None, 1, DpType.Signed, 0, 100, 1, b'\x00\x00\x00\x00'),
        (1009, None, 1, DpType.OffOn,  0, 1,   2, b'\x00'),
    ]

    def __init__(self, connector: _FakeConnector):
        self._connector = connector
        self._store: dict = {}
        self._notify_subs: set = set()
        for dp_id, inst, ver, dt, mn, mx, beh, val in self._DEFAULT_STORE:
            self._store[(dp_id, inst)] = {
                'version': ver, 'datatype': int(dt),
                'min_s': mn,    'max_s': mx,
                'behavior': beh,
                'value': bytearray(val),
            }

    async def run_once(self, timeout: float = 5.0) -> None:
        """Consume one client frame, deliver all responses."""
        frame = await asyncio.wait_for(self._connector._to_server.get(), timeout=timeout)
        for resp in self._dispatch(frame):
            await self._connector.deliver_to_client(resp)

    async def push_notify(self, dp_id: int, value: bytes) -> None:
        """Push an unsolicited NotifyData to the client."""
        frame = bytes([CommandId.NotifyData]) + encode_address(dp_id) + value
        await self._connector.deliver_to_client(frame)

    def _dispatch(self, frame: bytes) -> list[bytes]:
        if not frame:
            return []
        cmd = frame[0]
        if cmd == CommandId.Inventory:
            return self._inventory()
        if cmd == CommandId.ReadCmd:
            return self._read(frame)
        if cmd == CommandId.WriteCmd:
            return self._write(frame)
        if cmd == CommandId.NotifyEnable:
            return self._notify_enable(frame)
        if cmd == CommandId.NotifyDisable:
            return self._notify_disable(frame)
        return []

    def _inventory(self) -> list[bytes]:
        count = len(self._store)
        frames: list[bytes] = [struct.pack('<BH', CommandId.InventoryCount, count)]
        for (dp_id, inst), e in sorted(self._store.items()):
            addr = encode_address(dp_id, inst)
            payload = (bytes([e['version'], e['datatype']]) +
                       struct.pack('<ii', e['min_s'], e['max_s']) +
                       bytes([e['behavior'] & 0x7F]))
            frames.append(bytes([CommandId.InventoryData]) + addr + payload)
        return frames

    def _read(self, frame: bytes) -> list[bytes]:
        dp_id, inst, _ = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        entry = self._store.get((dp_id, inst)) or self._store.get((dp_id, None))
        if entry is None:
            return [bytes([CommandId.ReadError]) + addr + bytes([0x01])]
        return [bytes([CommandId.ReadAns]) + addr + bytes(entry['value'])]

    def _write(self, frame: bytes) -> list[bytes]:
        dp_id, inst, off = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        value = frame[off:]
        entry = self._store.get((dp_id, inst)) or self._store.get((dp_id, None))
        if entry is None:
            return [bytes([CommandId.WriteError]) + addr + bytes([0x01])]
        entry['value'] = bytearray(value)
        responses: list[bytes] = [bytes([CommandId.WriteAck]) + addr]
        if dp_id in self._notify_subs:
            responses.append(bytes([CommandId.NotifyData]) + encode_address(dp_id) + value)
        return responses

    def _notify_enable(self, frame: bytes) -> list[bytes]:
        dp_id, inst, _ = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        if (dp_id, inst) not in self._store and (dp_id, None) not in self._store:
            return [bytes([CommandId.NotifyError]) + addr + bytes([0x01])]
        self._notify_subs.add(dp_id)
        return [bytes([CommandId.NotifyAck]) + addr]

    def _notify_disable(self, frame: bytes) -> list[bytes]:
        dp_id, inst, _ = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        self._notify_subs.discard(dp_id)
        return [bytes([CommandId.NotifyAck]) + addr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(connector=None):
    """Return (connector, client, server)."""
    c = connector or _FakeConnector()
    return c, Ble20Client(c), _MockBle20Server(c)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_inventory():
    c, client, server = _make()
    n_expected = len(server._store)

    inv_task = asyncio.create_task(client.inventory())
    srv_task = asyncio.create_task(server.run_once())
    inv = (await asyncio.wait_for(asyncio.gather(inv_task, srv_task), timeout=5.0))[0]

    assert len(inv) == n_expected, f"expected {n_expected} DpIds, got {len(inv)}"
    assert 60 in inv
    assert 563 in inv
    assert 564 in inv
    assert 1008 in inv
    assert 1009 in inv
    assert inv[564]['datatype'] == int(DpType.Enum)
    assert inv[1008]['datatype'] == int(DpType.Signed)
    print(f"  test_inventory: PASS ({len(inv)} DpIds)")


async def test_read():
    c, client, server = _make()

    read_task = asyncio.create_task(client.read(564))
    srv_task = asyncio.create_task(server.run_once())
    raw = (await asyncio.wait_for(asyncio.gather(read_task, srv_task), timeout=5.0))[0]

    assert raw == b'\x01', f"expected b'\\x01', got {raw.hex()}"
    print("  test_read: PASS")


async def test_read_unknown_dpid():
    c, client, server = _make()

    read_task = asyncio.create_task(client.read(9999))
    srv_task = asyncio.create_task(server.run_once())
    try:
        await asyncio.wait_for(asyncio.gather(read_task, srv_task), timeout=5.0)
        assert False, "expected IOError"
    except IOError as e:
        assert "dp_id=9999" in str(e), f"unexpected message: {e}"
        print(f"  test_read_unknown_dpid: PASS")


async def test_write():
    c, client, server = _make()

    write_task = asyncio.create_task(client.write(1009, b'\x01'))
    srv_task = asyncio.create_task(server.run_once())
    await asyncio.wait_for(asyncio.gather(write_task, srv_task), timeout=5.0)

    assert bytes(server._store[(1009, None)]['value']) == b'\x01'
    print("  test_write: PASS")


async def test_write_unknown_dpid():
    c, client, server = _make()

    write_task = asyncio.create_task(client.write(9999, b'\x01'))
    srv_task = asyncio.create_task(server.run_once())
    try:
        await asyncio.wait_for(asyncio.gather(write_task, srv_task), timeout=5.0)
        assert False, "expected IOError"
    except IOError as e:
        assert "dp_id=9999" in str(e), f"unexpected message: {e}"
        print("  test_write_unknown_dpid: PASS")


async def test_enable_notification():
    c, client, server = _make()

    notify_task = asyncio.create_task(client.enable_notification([60]))
    srv_task = asyncio.create_task(server.run_once())
    await asyncio.wait_for(asyncio.gather(notify_task, srv_task), timeout=5.0)

    assert 60 in server._notify_subs
    assert 60 in client._notify_queues
    print("  test_enable_notification: PASS")


async def test_get_notification():
    c, client, server = _make()

    # Subscribe
    notify_task = asyncio.create_task(client.enable_notification([60]))
    srv_task = asyncio.create_task(server.run_once())
    await asyncio.gather(notify_task, srv_task)

    # Server pushes unsolicited NotifyData
    get_task = asyncio.create_task(client.get_notification(60, timeout=5.0))
    push_task = asyncio.create_task(server.push_notify(60, b'\x01'))
    results = await asyncio.wait_for(asyncio.gather(get_task, push_task), timeout=5.0)

    assert results[0] == b'\x01', f"expected b'\\x01', got {results[0].hex()}"
    print("  test_get_notification: PASS")


async def test_write_triggers_notify():
    """Write to a subscribed DpId — server returns WriteAck + NotifyData in one shot."""
    c, client, server = _make()

    # Subscribe DpId 60
    notify_task = asyncio.create_task(client.enable_notification([60]))
    srv_task = asyncio.create_task(server.run_once())
    await asyncio.gather(notify_task, srv_task)

    # Write + get_notification run concurrently; server delivers both frames in run_once
    write_task = asyncio.create_task(client.write(60, b'\x01'))
    get_task = asyncio.create_task(client.get_notification(60, timeout=5.0))
    srv_task = asyncio.create_task(server.run_once())

    results = await asyncio.wait_for(asyncio.gather(write_task, get_task, srv_task), timeout=5.0)
    assert results[1] == b'\x01', f"expected b'\\x01', got {results[1].hex()}"
    print("  test_write_triggers_notify: PASS")


async def test_disable_notification():
    c, client, server = _make()

    # Subscribe, then disable
    enable_task = asyncio.create_task(client.enable_notification([60]))
    srv_task = asyncio.create_task(server.run_once())
    await asyncio.gather(enable_task, srv_task)
    assert 60 in server._notify_subs

    disable_task = asyncio.create_task(_disable_and_check(client, 60))
    srv_task = asyncio.create_task(server.run_once())
    await asyncio.wait_for(asyncio.gather(disable_task, srv_task), timeout=5.0)

    assert 60 not in server._notify_subs
    print("  test_disable_notification: PASS")


async def _disable_and_check(client: Ble20Client, dp_id: int) -> None:
    from aquaclean_console_app.bluetooth_le.LE.command_id import CommandId as _CId
    addr = encode_address(dp_id)
    await client._send(bytes([_CId.NotifyDisable]) + addr)
    frame = await client._recv(timeout=5.0)
    assert frame[0] == _CId.NotifyAck, f"expected NotifyAck, got 0x{frame[0]:02X}"


async def test_poll_state():
    """poll_state() reads the four standard bridge DpIds."""
    c, client, server = _make()

    async def _serve_four():
        for _ in range(4):
            await server.run_once()

    poll_task = asyncio.create_task(client.poll_state())
    srv_task = asyncio.create_task(_serve_four())
    results = await asyncio.wait_for(asyncio.gather(poll_task, srv_task), timeout=10.0)
    state = results[0]

    assert 60 in state
    assert 564 in state
    assert state[564] == b'\x01'
    print(f"  test_poll_state: PASS (state keys: {sorted(state.keys())})")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run_all() -> bool:
    tests = [
        test_inventory,
        test_read,
        test_read_unknown_dpid,
        test_write,
        test_write_unknown_dpid,
        test_enable_notification,
        test_get_notification,
        test_write_triggers_notify,
        test_disable_notification,
        test_poll_state,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            await t()
            passed += 1
        except Exception as e:
            print(f"  {t.__name__}: FAIL — {e}")
            traceback.print_exc()
            failed += 1
    total = passed + failed
    print(f"\n{'OK' if failed == 0 else 'FAILED'}: {passed}/{total} tests passed")
    return failed == 0


def test_all_ble20_client():
    """pytest entry point."""
    assert asyncio.run(_run_all())


if __name__ == '__main__':
    sys.exit(0 if asyncio.run(_run_all()) else 1)
