import asyncio
import time
from bleak import BleakClient, BleakScanner, BleakError
from bleak.backends.scanner import AdvertisementData
from bleak.backends.device import BLEDevice


from uuid import UUID

from binascii import hexlify
import logging

from aquaclean_console_app.aquaclean_utils                                     import utils
from aquaclean_console_app.myEvent                                             import myEvent

from typing import Dict, Callable


logger = logging.getLogger(__name__)


class ESPHomeConnectionError(Exception):
    """Raised when the app cannot reach the ESP32 API (TCP port 6053).

    The ``timeout`` attribute distinguishes a connect timeout (E1001) from
    a general connection failure (E1002).
    """
    def __init__(self, message: str, timeout: bool = False):
        super().__init__(message)
        self.timeout = timeout


class ESPHomeDeviceNotFoundError(Exception):
    """Raised when the Geberit device is not found via the ESPHome BLE proxy (E0002)."""


class IBluetoothLeConnector:
    pass

class BluetoothLeConnector(IBluetoothLeConnector):
    SERVICE_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a03e0000")
    BULK_CHAR_BULK_WRITE_0_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a13e0000")
    BULK_CHAR_BULK_WRITE_1_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a23e0000")
    BULK_CHAR_BULK_WRITE_2_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a33e0000")
    BULK_CHAR_BULK_WRITE_3_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a43e0000")
    BULK_CHAR_BULK_READ_0_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a53e0000")
    BULK_CHAR_BULK_READ_1_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a63e0000")
    BULK_CHAR_BULK_READ_2_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a73e0000")
    BULK_CHAR_BULK_READ_3_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a83e0000")
    CCC_UUID = UUID("00002902-0000-1000-8000-00805f9b34fb")

    def __init__(self, esphome_host=None, esphome_port=6053, esphome_noise_psk=None, hass=None):
        self.client = None
        self.read_characteristics = {}
        self.data_received_handlers = myEvent.EventHandler()
        self.data_received = None
        self.connection_status_changed_handlers = myEvent.EventHandler()
        self.device_address = 'Unknown'
        self.device_name = 'Unknown'
        self.esphome_host      = esphome_host
        self.esphome_port      = esphome_port
        self.esphome_noise_psk = esphome_noise_psk
        self.esphome_proxy_name = None  # ESP32 device name from device_info
        self.esphome_proxy_connected = False  # True when ESP32 API is connected
        self.last_esphome_api_ms: int | None = None  # Time to connect/verify ESP32 API (None = local BLE, 0 = reused)
        self._esphome_unsub_adv = None  # BLE advertisement unsubscribe callable; held until disconnect()
        self.last_ble_ms: int | None = None           # Time for BLE scan + handshake to toilet
        self._esphome_api = None          # Persistent ESP32 API TCP connection (reused when persistent_api=true)
        self._esphome_feature_flags = 0   # Cached bluetooth_proxy_feature_flags from device_info
        self.rssi: int | None = None                  # BLE advertisement RSSI of the Geberit (dBm)
        self.esphome_wifi_rssi: float | None = None   # ESP32 WiFi signal strength (dBm)
        self.esphome_free_heap: int | None = None     # ESP32 free heap in bytes
        self.esphome_max_free_block: int | None = None  # ESP32 max contiguous free block in bytes
        self._esphome_wifi_key: int | None = None     # aioesphomeapi entity key for wifi_signal sensor
        self._esphome_free_heap_key: int | None = None       # aioesphomeapi entity key for free heap sensor
        self._esphome_max_free_block_key: int | None = None  # aioesphomeapi entity key for max free block sensor
        self._hass = hass  # Home Assistant instance (HACS integration only); None = standalone bridge
        self._subscribed_characteristics: list = []  # BleakGATTCharacteristic objects registered via start_notify()


    async def connect_async(self, device_id):
        logger.silly("BluetoothLeConnector: connect")
        if self.esphome_host:
            await self._connect_via_esphome(device_id)
        else:
            await self._connect_local(device_id)


    async def _connect_local(self, device_id):
        t0 = time.perf_counter()

        if self._hass is not None:
            # HACS integration path: use HA's bluetooth stack.
            # BleakClient is wrapped globally by habluetooth and requires a BLEDevice
            # sourced from HA's scanner cache — not from a raw BleakScanner call.
            # Using bleak_retry_connector for reliable connection as recommended by HA.
            device = await self._get_ble_device_via_ha(device_id)
            if device is None:
                raise BleakError(
                    f"AquaClean device {device_id} not found by HA bluetooth scanner."
                )
            self.device_address = device.address
            self.device_name = device.name or "Unknown"
            # self.rssi already set by _get_ble_device_via_ha() from service_info.rssi
            logger.debug(
                f"[HA-BLE] Connecting: address={device.address}, name={self.device_name}, rssi={self.rssi}"
            )
            try:
                from bleak_retry_connector import establish_connection
                self.client = await establish_connection(
                    BleakClient,
                    device,
                    device.name or device_id,
                    disconnected_callback=self._on_disconnected,
                )
            except ImportError:
                # bleak_retry_connector not available — fall back to direct connect.
                # Shouldn't happen on HA OS but safe to handle.
                self.client = BleakClient(device, disconnected_callback=self._on_disconnected)
                await self.client.connect()
        else:
            # Standalone bridge path: use BleakScanner directly.
            # No habluetooth interception outside of HA OS.
            device = await BleakScanner.find_device_by_address(device_id)
            if device is None:
                raise BleakError(f"AquaClean device with address {device_id} not found.")
            self.device_address = device.address
            self.device_name = device.name
            self.rssi = getattr(device, "rssi", None)
            logger.debug(
                f"device.address: {device.address}, device.name: {device.name}, rssi: {self.rssi}"
            )
            self.client = BleakClient(
                address_or_ble_device=device, disconnected_callback=self._on_disconnected
            )
            await self.client.connect()

        self.last_esphome_api_ms = None  # No ESP32 proxy
        self.last_ble_ms = int((time.perf_counter() - t0) * 1000)
        await self._post_connect()

    async def _get_ble_device_via_ha(self, device_id: str):
        """Get a BLEDevice from HA's bluetooth scanner cache.

        Checks the cache immediately; if the device is not yet known, registers a
        callback and waits up to 30 seconds for the device to advertise.
        Returns None if the device is not seen within the timeout.

        Only called when self._hass is set (HACS integration path).
        The standalone bridge uses BleakScanner.find_device_by_address() directly.
        """
        from homeassistant.components import bluetooth
        from homeassistant.core import callback as ha_callback

        address = device_id.upper()

        # Fast path: device already in HA's scanner cache.
        # async_last_service_info returns BluetoothServiceInfoBleak which carries .rssi;
        # async_ble_device_from_address returns only BLEDevice where .rssi is always None.
        service_info = bluetooth.async_last_service_info(self._hass, address, connectable=True)
        if service_info is not None:
            logger.debug(f"[HA-BLE] Device {address} found in HA bluetooth cache immediately")
            self.rssi = service_info.rssi
            return service_info.device

        # Slow path: wait for HA's scanner to see the device advertise.
        logger.debug(
            f"[HA-BLE] Device {address} not in cache yet; waiting up to 30s for advertisement"
        )
        found_event = asyncio.Event()
        found_device: list[BLEDevice | None] = [None]
        found_rssi: list[int | None] = [None]

        @ha_callback
        def _on_advertisement(service_info, change) -> None:
            found_device[0] = service_info.device
            found_rssi[0] = service_info.rssi
            found_event.set()

        cancel = bluetooth.async_register_callback(
            self._hass,
            _on_advertisement,
            {"address": address},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        try:
            await asyncio.wait_for(found_event.wait(), timeout=30.0)
            self.rssi = found_rssi[0]
            logger.debug(
                f"[HA-BLE] Device {address} seen by HA scanner: {getattr(found_device[0], 'name', 'Unknown')}"
            )
            return found_device[0]
        except asyncio.TimeoutError:
            logger.warning(
                f"[HA-BLE] Device {address} not seen by HA bluetooth scanner within 30s. "
                f"Ensure the toilet is powered on and within BLE range."
            )
            return None
        finally:
            cancel()


    async def _ensure_esphome_api_connected(self):
        """Return a connected ESP32 API client, reusing the existing TCP connection if alive.

        On first call (or after a full disconnect): opens a new TCP connection, fetches
        device_info, stores feature_flags and proxy name, sets last_esphome_api_ms to
        the actual connect time.

        On subsequent calls when the TCP connection is still alive: returns immediately
        with last_esphome_api_ms = 0 (no TCP handshake overhead).

        Raises ESPHomeConnectionError on connection failure.
        """
        from aioesphomeapi import APIClient

        if self._esphome_api is not None:
            # Check whether the underlying TCP connection is still alive.
            # aioesphomeapi sets _connection = None internally when the TCP link
            # drops (e.g. after the 90-second ping-response timeout).  Returning
            # a dead APIClient here would cause "Not connected" errors on every
            # subsequent poll with no chance of recovery.  Detect the dead state
            # and fall through to open a fresh connection instead.
            if getattr(self._esphome_api, '_connection', None) is not None:
                logger.debug("Reusing existing ESP32 API connection")
                self.last_esphome_api_ms = 0
                return self._esphome_api
            logger.warning(
                "ESP32 API connection lost (ping timeout?); "
                "clearing stale client and reconnecting"
            )
            self._esphome_api = None
            self.esphome_proxy_connected = False
            # Fall through to open a fresh connection

        t0 = time.perf_counter()
        api = APIClient(
            address=self.esphome_host,
            port=self.esphome_port,
            password="",
            noise_psk=self.esphome_noise_psk
        )
        try:
            await asyncio.wait_for(api.connect(login=True), timeout=10.0)
        except asyncio.TimeoutError:
            raise ESPHomeConnectionError(
                f"Timeout connecting to ESPHome proxy at {self.esphome_host}:{self.esphome_port}",
                timeout=True,
            )
        except Exception as e:
            raise ESPHomeConnectionError(
                f"Failed to connect to ESPHome proxy at {self.esphome_host}: {e}",
                timeout=False,
            )

        try:
            device_info = await asyncio.wait_for(api.device_info(), timeout=10.0)
            self._esphome_feature_flags = getattr(device_info, "bluetooth_proxy_feature_flags", 0)
            self.esphome_proxy_name = getattr(device_info, "name", "unknown")
        except Exception as e:
            logger.warning(f"Failed to get device info, using default feature_flags=0: {e}")
            self._esphome_feature_flags = 0
            self.esphome_proxy_name = "unknown"

        self._esphome_api = api
        self.esphome_proxy_connected = True
        self.last_esphome_api_ms = int((time.perf_counter() - t0) * 1000)
        logger.debug(f"ESP32 proxy connected: {self.esphome_proxy_name} ({self.last_esphome_api_ms} ms)")
        return api

    async def _read_esphome_wifi_rssi_async(self) -> None:
        """Read ESP32 diagnostic sensor values via the native API.

        Reads WiFi RSSI, Free Heap, and Max Free Block in a single subscribe_states() call.
        Requires `platform: wifi_signal` and `platform: debug` in the ESPHome YAML.
        Safe to call while API is connected but BLE is idle (before advertisement scan).
        Silently skips sensors that are not configured or if the read times out.
        """
        api = self._esphome_api
        if api is None:
            return
        try:
            # Cache entity keys on first call to avoid list_entities_services() every poll.
            if self._esphome_wifi_key is None or self._esphome_free_heap_key is None or self._esphome_max_free_block_key is None:
                entities, _ = await asyncio.wait_for(api.list_entities_services(), timeout=5.0)

                if self._esphome_wifi_key is None:
                    self._esphome_wifi_key = next(
                        (
                            e.key for e in entities
                            if getattr(e, 'unit_of_measurement', '') == 'dBm'
                            and 'wifi' in getattr(e, 'object_id', '').lower()
                        ),
                        -1,  # sentinel: -1 = "not found", None = "not yet looked up"
                    )
                    if self._esphome_wifi_key == -1:
                        logger.debug("No wifi_signal sensor on ESP32 (add platform: wifi_signal to ESPHome YAML)")

                if self._esphome_free_heap_key is None:
                    self._esphome_free_heap_key = next(
                        (
                            e.key for e in entities
                            if 'heap' in getattr(e, 'object_id', '').lower()
                        ),
                        -1,
                    )
                    if self._esphome_free_heap_key == -1:
                        logger.debug("No free heap sensor on ESP32 (add platform: debug with free: to ESPHome YAML)")

                if self._esphome_max_free_block_key is None:
                    self._esphome_max_free_block_key = next(
                        (
                            e.key for e in entities
                            if 'block' in getattr(e, 'object_id', '').lower()
                        ),
                        -1,
                    )
                    if self._esphome_max_free_block_key == -1:
                        logger.debug("No max free block sensor on ESP32 (add platform: debug with block: to ESPHome YAML)")

            # Collect which keys we need to read (skip absent ones)
            keys_to_read: dict[int, str] = {}
            if self._esphome_wifi_key != -1:
                keys_to_read[self._esphome_wifi_key] = 'wifi'
            if self._esphome_free_heap_key != -1:
                keys_to_read[self._esphome_free_heap_key] = 'heap'
            if self._esphome_max_free_block_key != -1:
                keys_to_read[self._esphome_max_free_block_key] = 'block'

            if not keys_to_read:
                return

            captured: dict[int, object] = {}
            all_received = asyncio.Event()

            def _on_state(state) -> None:
                key = getattr(state, 'key', None)
                if key in keys_to_read and key not in captured:
                    captured[key] = getattr(state, 'state', None)
                    if len(captured) >= len(keys_to_read):
                        all_received.set()

            unsub = api.subscribe_states(_on_state)
            try:
                await asyncio.wait_for(all_received.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.debug(f"Timeout reading ESP32 diagnostic sensors (got {len(captured)}/{len(keys_to_read)})")
            finally:
                try:
                    unsub()
                except Exception:
                    pass

            # Store captured values
            if self._esphome_wifi_key in captured and captured[self._esphome_wifi_key] is not None:
                self.esphome_wifi_rssi = round(float(captured[self._esphome_wifi_key]), 1)
                logger.debug(f"ESP32 WiFi RSSI: {self.esphome_wifi_rssi} dBm")
            if self._esphome_free_heap_key in captured and captured[self._esphome_free_heap_key] is not None:
                self.esphome_free_heap = int(float(captured[self._esphome_free_heap_key]))
                logger.debug(f"ESP32 Free Heap: {self.esphome_free_heap} B")
            if self._esphome_max_free_block_key in captured and captured[self._esphome_max_free_block_key] is not None:
                self.esphome_max_free_block = int(float(captured[self._esphome_max_free_block_key]))
                logger.debug(f"ESP32 Max Free Block: {self.esphome_max_free_block} B")
        except Exception as e:
            logger.debug(f"Failed to read ESP32 diagnostic sensors: {e}")

    async def _connect_via_esphome(self, device_id):
        from aquaclean_console_app.bluetooth_le.LE.ESPHomeAPIClient import ESPHomeAPIClient

        logger.debug(f"BluetoothLeConnector: connecting to BLE device via ESPHome proxy")

        # Defensive cleanup: release any leftover advertisement subscription before
        # subscribing again.  Normally disconnect_ble_only() handles this, but on a
        # fresh bridge start the previous bridge may have left a dangling subscription
        # on the ESP32 (if it exited before the TCP close was processed).
        # Calling unsub here is safe — BLE is not connected yet (trap 7 does not apply).
        if self._esphome_unsub_adv is not None:
            try:
                self._esphome_unsub_adv()
                logger.debug("Cleaned up leftover advertisement subscription before scan")
            except Exception as e:
                logger.debug(f"Advertisement unsubscribe (pre-scan cleanup): {e}")
            self._esphome_unsub_adv = None

        try:
            api = await self._ensure_esphome_api_connected()
        except ESPHomeConnectionError:
            self._esphome_api = None  # Force reconnect on next attempt
            raise

        # Read WiFi signal strength while API is connected and idle (before BLE scan).
        await self._read_esphome_wifi_rssi_async()

        t_ble = time.perf_counter()  # BLE timing starts after ESP32 API is ready

        mac_int = int(device_id.replace(":", ""), 16)
        found_event = asyncio.Event()
        device_name = ""
        address_type = 0  # Default to PUBLIC (0) if not present in advertisement

        def on_raw_advertisements(resp):
            nonlocal device_name, address_type
            for adv in resp.advertisements:
                if adv.address == mac_int:
                    device_name = self._parse_local_name(bytes(adv.data))
                    address_type = getattr(adv, 'address_type', 0)
                    self.rssi = getattr(adv, 'rssi', None)
                    found_event.set()

        logger.silly(f"Scanning for BLE device {device_id} (mac_int={mac_int})")
        unsub_adv = api.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
        # Store immediately so that disconnect_ble_only() / disconnect() can unsubscribe
        # even if this coroutine is cancelled (e.g. SIGTERM during the scan window).
        # Without this, a mid-scan cancellation leaves a dangling subscription on the
        # ESP32 that blocks the next bridge startup with "Only one API subscription
        # is allowed at a time".
        self._esphome_unsub_adv = unsub_adv
        try:
            await asyncio.wait_for(found_event.wait(), timeout=10.0)
            logger.debug(
                f"Found BLE device {device_id}: name={device_name or 'Unknown'}, "
                f"address_type={address_type}"
            )
        except asyncio.TimeoutError:
            unsub_adv()
            self._esphome_unsub_adv = None  # already called; prevent double-unsubscribe in disconnect()
            raise ESPHomeDeviceNotFoundError(
                f"AquaClean device {device_id} not found via ESPHome proxy at {self.esphome_host}"
            )
        # Advertisement subscription intentionally kept alive until BLE connect completes.
        # Calling unsub_adv() before bluetooth_device_connect() sends
        # UnsubscribeBluetoothLEAdvertisementsRequest which clears api_connection_ on the
        # ESP32, causing it to disconnect any BLE client in CONNECTING state immediately
        # → "Disconnect before connected, disconnect scheduled" (reason 0x16).
        # See CLAUDE.md trap 7.
        self.device_address = device_id
        self.device_name = device_name or "Unknown"

        # Connect to BLE device using the known address_type.
        # unsub_adv is kept alive through the connect (see trap 7 above).
        logger.debug(f"Creating ESPHomeAPIClient for {device_id} (address_type={address_type})")
        self.client = ESPHomeAPIClient(api, device_id, self._on_disconnected, address_type, self._esphome_feature_flags)
        try:
            await self.client.connect(timeout=30.0)
            logger.info(f"BLE connection successful with address_type={address_type}")
        except Exception as e:
            logger.warning(f"BLE connection failed with address_type={address_type}: {e}")
            unsub_adv()
            self._esphome_unsub_adv = None
            raise

        self.last_ble_ms = int((time.perf_counter() - t_ble) * 1000)
        logger.debug(f"BLE connect complete ({self.last_ble_ms} ms)")
        await self._post_connect()


    def _parse_local_name(self, data: bytes) -> str:
        """Extract device name from raw BLE advertisement AD structures."""
        i = 0
        name = ""
        while i < len(data):
            length = data[i]
            if length == 0 or i + length >= len(data):
                break
            ad_type = data[i + 1]
            value = data[i + 2 : i + 1 + length]
            if ad_type == 0x09:  # Complete Local Name
                return value.decode("utf-8", errors="replace")
            elif ad_type == 0x08:  # Shortened Local Name
                name = value.decode("utf-8", errors="replace")
            i += 1 + length
        return name


    async def _post_connect(self):
        self.read_characteristics = {
            self.BULK_CHAR_BULK_READ_0_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_1_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_2_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_3_UUID: self.data_received
        }
        logger.silly(f"self.read_characteristics: {self.read_characteristics}")
        await self._list_services()
        self.connection_status_changed_handlers(self, True, self.device_address, self.device_name)


    async def _list_services(self):
        logger.silly("BluetoothLeConnector: _list_services")

        if not self.client.is_connected:
            logger.silly('1. Error. Client not connected.')
            await self.client.connect()
        else:
            logger.silly('1. in subscribe 1: connected.')

        for service in self.client.services:
            if service.uuid == str(self.SERVICE_UUID):
                for characteristic in service.characteristics:
                    logger.silly(f"got characteristic.uuid {characteristic.uuid}")
                    if characteristic.uuid in str(self.read_characteristics):
                        logger.silly(f"Registering characteristic {characteristic.uuid} for notification.")
                        # Preemptively release any stale BlueZ notification subscription from
                        # a previous session.  If stop_notify() in disconnect() failed (e.g.
                        # because the connection broke mid-command), BlueZ still holds
                        # "Notifying: True" on the D-Bus characteristic object — the next
                        # start_notify() then fails with [org.bluez.Error.NotPermitted].
                        # Calling stop_notify() here before start_notify() clears that state.
                        # Harmless if the characteristic is not currently notifying.
                        try:
                            await self.client.stop_notify(characteristic)
                            logger.debug(f"Preemptive stop_notify OK: {characteristic.uuid}")
                        except Exception as _stop_exc:
                            logger.debug(f"Preemptive stop_notify failed (expected if not notifying): {characteristic.uuid}: {type(_stop_exc).__name__}: {_stop_exc}")
                        await self.client.start_notify(characteristic, self._on_data_received)
                        self._subscribed_characteristics.append(characteristic)


    async def _on_data_received(self, sender, data):
        logger.silly("BluetoothLeConnector: _on_data_received")
        logger.silly(f"Received data from characteristic {sender.uuid} data: {''.join(f'{b:02X}' for b in data)}")

        await self.data_received_handlers.invoke_async(data)


    def _on_disconnected(self, client):
        logger.silly("BluetoothLeConnector: _on_disconnected")
        self.connection_status_changed_handlers(self, False)


    async def send_message(self, data):
        logger.silly(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        # 13:03:18:989	18.11.2024 13:03:18: Sending data to characteristic 3334429d-90f3-4c41-a02d-5cb3a13e0000 data: 700008000F000000000000000000000000000000
        logger.silly(f"Sending data to characteristic {self.BULK_CHAR_BULK_WRITE_0_UUID} data: {''.join(f'{b:02X}' for b in data)}")
        # result = await self.client.write_gatt_char(self.BULK_CHAR_BULK_WRITE_0_UUID, data)
        result = await self.client.write_gatt_char(self.BULK_CHAR_BULK_WRITE_0_UUID, data)

        logger.silly(f"result: {result}")

    async def send_message_cons(self, data):
        """Send a CONS frame to WRITE_1 (second BLE write characteristic)."""
        logger.silly(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.silly(f"Sending CONS data to characteristic {self.BULK_CHAR_BULK_WRITE_1_UUID} data: {''.join(f'{b:02X}' for b in data)}")
        result = await self.client.write_gatt_char(self.BULK_CHAR_BULK_WRITE_1_UUID, data)
        logger.silly(f"result: {result}")


    def get_gatt_profile(self):
        """Return the GATT profile of the currently connected device.

        Must be called after connect_ble_only() while BLE is still connected.
        Returns a GattProfile (is_standard=True on any error so valid devices
        are never falsely rejected).
        """
        from aquaclean_console_app.bluetooth_le.LE.GattDiscovery import probe_gatt_profile
        if self.client is None:
            from aquaclean_console_app.bluetooth_le.LE.GattDiscovery import (
                GattProfile, GEBERIT_SERVICE_UUID,
            )
            return GattProfile(is_standard=True, svc_uuid=GEBERIT_SERVICE_UUID)
        return probe_gatt_profile(self.client)

    async def restart_esp32_async(self):
        """Press the 'Restart AquaClean Proxy' button on the ESP32 via the native API.

        Opens a dedicated fresh TCP connection — does not touch any active BLE
        connection or the persistent _esphome_api used for BLE cycling.
        The ESP32 reboots within a few seconds; the TCP connection drops on its own.

        Raises:
            ESPHomeConnectionError: if the ESP32 is unreachable.
            ValueError: if no restart button entity is found (ESP32 not yet flashed
                        with YAML containing 'button: platform: restart').
        """
        from aioesphomeapi import APIClient, ButtonInfo

        api = APIClient(
            address=self.esphome_host,
            port=self.esphome_port,
            password="",
            noise_psk=self.esphome_noise_psk,
        )
        try:
            await asyncio.wait_for(api.connect(login=True), timeout=10.0)
        except asyncio.TimeoutError:
            raise ESPHomeConnectionError(
                f"Timeout connecting to ESPHome proxy at {self.esphome_host}:{self.esphome_port}",
                timeout=True,
            )
        except Exception as e:
            raise ESPHomeConnectionError(
                f"Failed to connect to ESPHome proxy at {self.esphome_host}: {e}",
                timeout=False,
            )
        try:
            entities, _ = await asyncio.wait_for(api.list_entities_services(), timeout=10.0)
            restart_key = None
            for entity in entities:
                if isinstance(entity, ButtonInfo):
                    if "restart" in entity.name.lower() or entity.object_id.lower() == "restart":
                        restart_key = entity.key
                        logger.debug(f"[BluetoothLeConnector] Found restart button: '{entity.name}' (key={restart_key})")
                        break
            if restart_key is None:
                raise ValueError(
                    "Restart button not found on ESP32. "
                    "Flash the ESP32 with updated YAML that includes 'button: platform: restart'."
                )
            await api.button_command(restart_key)
            logger.info(f"[BluetoothLeConnector] ESP32 restart command sent (key={restart_key})")
        finally:
            try:
                await api.disconnect()
            except Exception:
                pass

    async def disconnect_ble_only(self):
        """BLE-only disconnect — ESP32 API TCP connection stays alive for reuse.

        Used in persistent_api mode: tears down only the BLE link to the Geberit.
        The advertisement subscription reference (_esphome_unsub_adv) is kept alive
        so that the pre-scan cleanup at the start of _connect_via_esphome() can call
        it before the next subscribe attempt (see CLAUDE.md trap 12).
        self._esphome_api stays connected for TCP reuse at 0 ms overhead.
        """
        logger.silly(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        if self.client:
            await self.client.disconnect(close_api=False)
            self.client = None
        else:
            logger.silly("not self.client, no need to disconnect BLE.")

        # Do NOT call _esphome_unsub_adv() here — the subscription is kept alive so
        # that _connect_via_esphome() can release it at the very start of the next request
        # (before any new BLE connection is active, so trap 7 does not apply).
        # Calling it here would null the reference and the pre-scan cleanup would skip it,
        # leaving the old subscription on the ESP32 until it expires.
        # See CLAUDE.md trap 12 / commit 0c6ba46.

        # Do NOT reset self._esphome_api or esphome_proxy_connected — TCP stays alive.

    async def disconnect(self):
        logger.silly(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        if self.client:
            # Log is_connected status — when False (Geberit dropped the BLE link during
            # the 1 s post-command sleep), bleak's disconnect() returns early without
            # closing the D-Bus MessageBus, leaving BlueZ with a live notification
            # subscription that causes "Notify acquired" on the next start_notify call.
            is_connected = self.client.is_connected
            logger.debug(f"[disconnect] is_connected={is_connected}, subscribed chars={len(self._subscribed_characteristics)}")
            for char in self._subscribed_characteristics:
                try:
                    await self.client.stop_notify(char)
                    logger.debug(f"[disconnect] stop_notify OK: {char.uuid}")
                except Exception as e:
                    logger.debug(f"[disconnect] stop_notify FAILED: {char.uuid}: {type(e).__name__}: {e}")
            self._subscribed_characteristics = []
            logger.silly(f"before asyncio.create_task(self.client.disconnect())")
            await self.client.disconnect()
            # Release the BleakClient so Python GC can close its D-Bus MessageBus.
            # bleak's internal GC cycles (MessageBus ↔ signal handlers) require the
            # cyclic collector — gc.collect() runs it immediately instead of waiting
            # for the next scheduled GC pass (~60-90 s under HA's allocation pattern).
            self.client = None
            import gc
            gc.collect()
        else:
            logger.silly(f"not self.client, no need to disconnect BLE.")
            # No BLE client means the scan timed out (device not found) before BLE connect.
            # ESPHomeAPIClient.disconnect() would normally close the TCP connection, but
            # since self.client was never created, we must close it explicitly here.
            # Without this, the dangling TCP connection keeps the ESP32's advertisement
            # subscription slot occupied, blocking ble-scan.py and other clients.
            if self._esphome_api is not None:
                try:
                    await self._esphome_api.disconnect()
                    logger.debug("[BluetoothLeConnector] Closed ESP32 API TCP connection (no BLE client was established)")
                except Exception as e:
                    logger.debug(f"[BluetoothLeConnector] ESP32 API TCP close: {e}")

        # Unsubscribe from BLE advertisements now that BLE is fully torn down.
        # Must NOT be called while BLE is active — the UnsubscribeBluetoothLEAdvertisementsRequest
        # causes the ESP32 to disconnect any active BLE client (see CLAUDE.md trap 7).
        if self._esphome_unsub_adv is not None:
            try:
                self._esphome_unsub_adv()
            except Exception:
                pass
            self._esphome_unsub_adv = None

        # Reset ESP32 proxy connection state
        if self.esphome_host:
            self.esphome_proxy_connected = False
            self._esphome_api = None  # TCP connection is gone; force reconnect on next call

