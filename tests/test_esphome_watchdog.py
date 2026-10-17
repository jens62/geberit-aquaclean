"""Isolated regression tests for ESPHome API verification and watchdog.

These tests extract the target methods via AST/text so the bridge's optional
BLE and MQTT runtime dependencies are not required to execute the tests.

Run: python -m unittest discover -s tests -p test_esphome_watchdog.py
"""
import ast
import asyncio
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class ESPHomeConnectionError(Exception):
    def __init__(self, message, timeout=False):
        super().__init__(message)
        self.timeout = timeout


ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


def source_methods(path, first, next_method):
    source = (ROOT / path).read_text(encoding="utf-8")
    start = source.index("    async def " + first + "(")
    end = source.index("    async def " + next_method + "(", start)
    return source[start:end]


connector_methods = source_methods(
    "aquaclean_console_app/bluetooth_le/LE/BluetoothLeConnector.py",
    "_invalidate_esphome_api", "_connect_via_esphome",
)
connector_ns = {
    "asyncio": asyncio,
    "logger": logger,
    "ESPHomeConnectionError": ESPHomeConnectionError,
}
connector_code = "class Connector:\n" + connector_methods
ast.parse(connector_code)
exec(connector_code, connector_ns)
Connector = connector_ns["Connector"]


class FakeMQTT:
    def __init__(self):
        self.sent = []

    async def send_data_async(self, topic, value):
        self.sent.append((topic, value))


class FakeConnector:
    instances = []

    def __init__(self, *args):
        self._esphome_api = object()
        self._ensure_esphome_api_connected = AsyncMock(return_value=self._esphome_api)
        self.verify_esphome_api = AsyncMock()
        self.disconnect = AsyncMock()
        self.__class__.instances.append(self)


api_ns = {
    "asyncio": asyncio,
    "logger": logger,
    "ESPHomeConnectionError": ESPHomeConnectionError,
    "BluetoothLeConnector": FakeConnector,
    "esphome_host": "192.168.30.22",
    "esphome_port": 6053,
    "esphome_noise_psk": None,
    "E1001": types.SimpleNamespace(code="E1001"),
    "E1002": types.SimpleNamespace(code="E1002"),
    "ErrorManager": types.SimpleNamespace(
        to_json=lambda ec, message: f"{ec.code}:{message}"
    ),
}
api_methods = source_methods(
    "aquaclean_console_app/main.py", "_probe_esphome_proxy", "_on_demand"
)
api_code = "class ApiMode:\n" + api_methods
ast.parse(api_code)
exec(api_code, api_ns)
ApiMode = api_ns["ApiMode"]

publisher_methods = source_methods(
    "aquaclean_console_app/main.py",
    "_publish_esphome_proxy_status", "_publish_esphome_proxy_discovery",
)
publisher_ns = {
    "ErrorManager": types.SimpleNamespace(
        clear_error=lambda: "E0000",
        to_json=lambda ec, **kwargs: ec.code,
    )
}
publisher_code = "class ProxyPublisher:\n" + publisher_methods
ast.parse(publisher_code)
exec(publisher_code, publisher_ns)
ProxyPublisher = publisher_ns["ProxyPublisher"]


class TransportVerificationTests(unittest.IsolatedAsyncioTestCase):
    def connector(self, api):
        connector = Connector()
        connector._esphome_api = api
        connector._esphome_unsub_adv = None
        connector.esphome_proxy_connected = True
        connector.esphome_host = "192.168.30.22"
        connector.esphome_port = 6053
        return connector

    async def test_healthy_transport_keeps_ble_error(self):
        api = types.SimpleNamespace(
            _connection=object(),
            device_info=AsyncMock(return_value=object()),
            disconnect=AsyncMock(),
        )
        connector = self.connector(api)
        await connector.verify_esphome_api(api)
        self.assertIs(connector._esphome_api, api)
        api.device_info.assert_awaited_once()
        api.disconnect.assert_not_awaited()
        # The code must still raise E0002 after successful verification.
        source = (ROOT / "aquaclean_console_app/bluetooth_le/LE/BluetoothLeConnector.py").read_text()
        self.assertIn(
            "await self.verify_esphome_api(api)\n            hint = (", source
        )
        self.assertIn("raise ESPHomeDeviceNotFoundError(", source)

    async def test_timeout_is_e1001(self):
        api = types.SimpleNamespace(
            _connection=object(),
            device_info=AsyncMock(side_effect=asyncio.TimeoutError),
            disconnect=AsyncMock(),
        )
        connector = self.connector(api)
        with self.assertRaises(ESPHomeConnectionError) as raised:
            await connector.verify_esphome_api(api)
        self.assertTrue(raised.exception.timeout)
        self.assertIsNone(connector._esphome_api)
        api.disconnect.assert_awaited_once()

    async def test_connection_failure_is_e1002(self):
        api = types.SimpleNamespace(
            _connection=object(),
            device_info=AsyncMock(side_effect=ConnectionError("broken pipe")),
            disconnect=AsyncMock(),
        )
        with self.assertRaises(ESPHomeConnectionError) as raised:
            await self.connector(api).verify_esphome_api(api)
        self.assertFalse(raised.exception.timeout)
        self.assertIn("broken pipe", str(raised.exception))

    async def test_stale_client_cannot_close_new_client(self):
        old = types.SimpleNamespace(
            _connection=object(), device_info=AsyncMock(), disconnect=AsyncMock()
        )
        new = types.SimpleNamespace(
            _connection=object(), device_info=AsyncMock(), disconnect=AsyncMock()
        )
        connector = self.connector(new)
        with self.assertRaises(ESPHomeConnectionError):
            await connector.verify_esphome_api(old)
        self.assertIs(connector._esphome_api, new)
        old.disconnect.assert_not_awaited()
        new.disconnect.assert_not_awaited()

    async def test_unsubscribe_before_close(self):
        order = []

        async def close():
            order.append("close")

        api = types.SimpleNamespace(
            _connection=None, device_info=AsyncMock(), disconnect=close
        )
        connector = self.connector(api)
        connector._esphome_unsub_adv = lambda: order.append("unsubscribe")
        with self.assertRaises(ESPHomeConnectionError):
            await connector.verify_esphome_api(api)
        self.assertEqual(order, ["unsubscribe", "close"])


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    def mode(self):
        mode = ApiMode()
        mode._on_demand_lock = asyncio.Lock()
        mode.ble_connection = "on-demand"
        mode.esphome_api_connection = "on-demand"
        mode._esphome_connector = None
        mode._shutdown_event = asyncio.Event()
        mode._esphome_watchdog_online = None
        mode.service = types.SimpleNamespace(
            mqttConfig={"topic": "Geberit/AquaClean"},
            mqtt_service=FakeMQTT(),
            _update_esphome_proxy_state=AsyncMock(),
        )
        return mode

    async def test_probe_waits_for_ble_transaction_lock(self):
        mode = self.mode()
        FakeConnector.instances.clear()
        async with mode._on_demand_lock:
            task = asyncio.create_task(mode._probe_esphome_proxy())
            await asyncio.sleep(0)
            self.assertFalse(FakeConnector.instances)
            self.assertFalse(task.done())
        await task
        self.assertEqual(len(FakeConnector.instances), 1)
        FakeConnector.instances[0].verify_esphome_api.assert_awaited_once()
        FakeConnector.instances[0].disconnect.assert_awaited_once()

    async def test_online_transition_published(self):
        mode = self.mode()

        async def probe():
            mode._shutdown_event.set()

        mode._probe_esphome_proxy = probe
        await mode._esphome_watchdog_loop()
        self.assertEqual(
            mode.service.mqtt_service.sent,
            [("Geberit/AquaClean/esphomeProxy/watchdog", "online")],
        )
        mode.service._update_esphome_proxy_state.assert_awaited_once_with(
            connected=True, error="No error", error_code="E0000"
        )

    async def test_offline_transition_reports_network_error(self):
        mode = self.mode()

        async def probe():
            mode._shutdown_event.set()
            raise ESPHomeConnectionError("API offline")

        mode._probe_esphome_proxy = probe
        await mode._esphome_watchdog_loop()
        self.assertEqual(
            mode.service.mqtt_service.sent[0],
            ("Geberit/AquaClean/esphomeProxy/watchdog", "offline"),
        )
        self.assertEqual(len(mode.service.mqtt_service.sent), 1)
        mode.service._update_esphome_proxy_state.assert_awaited_once()
        self.assertEqual(
            mode.service._update_esphome_proxy_state.await_args.kwargs["error_code"],
            "E1002",
        )


class ProxyErrorPublicationTests(unittest.IsolatedAsyncioTestCase):
    def publisher(self):
        service = ProxyPublisher()
        service.mqttConfig = {"topic": "Geberit/AquaClean"}
        service.mqtt_service = FakeMQTT()
        service._last_published_esphome_error_code = None
        service.esphome_proxy_state = {
            "enabled": True, "connected": True, "name": "aquaclean-proxy",
            "host": "192.168.30.22", "port": 6053,
            "error_code": "E0000", "error": "No error", "error_hint": "",
            "wifi_rssi": None, "free_heap": None, "max_free_block": None,
        }
        return service

    async def test_repeated_network_poll_errors_publish_once_until_recovery(self):
        service = self.publisher()
        fake_package = types.ModuleType("aquaclean_console_app")
        fake_package.__path__ = []
        fake_error_module = types.ModuleType("aquaclean_console_app.ErrorCodes")
        fake_error_module.ErrorCode = lambda code, *_: types.SimpleNamespace(code=code)
        with patch.dict(sys.modules, {
            "aquaclean_console_app": fake_package,
            "aquaclean_console_app.ErrorCodes": fake_error_module,
        }):
            await service._publish_esphome_proxy_status()  # initial E0000
            service.esphome_proxy_state["error_code"] = "E1001"
            service.esphome_proxy_state["error"] = "ESPHome timeout"
            for _ in range(3):
                await service._publish_esphome_proxy_status()
            errors = [
                payload for topic, payload in service.mqtt_service.sent
                if topic == "Geberit/AquaClean/esphomeProxy/error"
            ]
            self.assertEqual(errors, ["E0000", "E1001"])

            # Successful API check clears the outage, allowing a future E1001.
            service.esphome_proxy_state["error_code"] = "E0000"
            await service._publish_esphome_proxy_status()
            service.esphome_proxy_state["error_code"] = "E1001"
            await service._publish_esphome_proxy_status()
            errors = [
                payload for topic, payload in service.mqtt_service.sent
                if topic == "Geberit/AquaClean/esphomeProxy/error"
            ]
            self.assertEqual(errors, ["E0000", "E1001", "E0000", "E1001"])

    async def test_different_proxy_code_remains_reportable(self):
        service = self.publisher()
        fake_package = types.ModuleType("aquaclean_console_app")
        fake_package.__path__ = []
        fake_error_module = types.ModuleType("aquaclean_console_app.ErrorCodes")
        fake_error_module.ErrorCode = lambda code, *_: types.SimpleNamespace(code=code)
        with patch.dict(sys.modules, {
            "aquaclean_console_app": fake_package,
            "aquaclean_console_app.ErrorCodes": fake_error_module,
        }):
            await service._publish_esphome_proxy_status()
            for code in ("E1001", "E1002", "E1002"):
                service.esphome_proxy_state["error_code"] = code
                await service._publish_esphome_proxy_status()
        errors = [
            payload for topic, payload in service.mqtt_service.sent
            if topic == "Geberit/AquaClean/esphomeProxy/error"
        ]
        self.assertEqual(errors, ["E0000", "E1001", "E1002"])

    def test_network_http_errors_have_single_mqtt_route(self):
        source = (ROOT / "aquaclean_console_app/main.py").read_text(encoding="utf-8")
        start = source.index("            except HTTPException as e:", source.index("    async def _polling_loop("))
        end = source.index("            except Exception as e:", start)
        handler = source[start:end]
        self.assertIn('if ec_code in ("E1001", "E1002") and esphome_host:', handler)
        self.assertIn('else:\n                    temp_ec = ErrorCode(', handler)


if __name__ == "__main__":
    unittest.main(verbosity=2)
