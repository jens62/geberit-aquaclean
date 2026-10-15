import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
    BluetoothLeConnector,
    ESPHomeConnectionError,
)
from aquaclean_console_app.setup.discovery import async_scan_ble_via_esphome


class _UnreachableAPIClient:
    created: list[dict] = []

    def __init__(self, **kwargs):
        self.created.append(kwargs)

    async def connect(self, login=False):
        raise ConnectionError("unreachable")


@pytest.fixture
def api_clients(monkeypatch):
    _UnreachableAPIClient.created = []
    fake = types.ModuleType("aioesphomeapi")
    fake.APIClient = _UnreachableAPIClient
    fake.ButtonInfo = object
    monkeypatch.setitem(sys.modules, "aioesphomeapi", fake)
    return _UnreachableAPIClient.created


async def test_connector_shares_zeroconf_instance_with_api_client(api_clients):
    shared = object()
    connector = BluetoothLeConnector("proxy.local", 6053, None, zeroconf_instance=shared)

    with pytest.raises(ESPHomeConnectionError):
        await connector._ensure_esphome_api_connected()
    with pytest.raises(ESPHomeConnectionError):
        await connector.restart_esp32_async()

    assert [c["zeroconf_instance"] for c in api_clients] == [shared, shared]


async def test_ble_scan_shares_zeroconf_instance_with_api_client(api_clients):
    shared = object()

    assert await async_scan_ble_via_esphome("proxy.local", 6053, zeroconf_instance=shared) == []
    assert api_clients[0]["zeroconf_instance"] is shared
