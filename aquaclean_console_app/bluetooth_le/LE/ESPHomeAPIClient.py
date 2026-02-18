"""
Bleak-compatible wrapper around aioesphomeapi for ESP32 Bluetooth Proxy.

This module provides a BleakClient-compatible interface for connecting to BLE
devices through an ESPHome Bluetooth Proxy, without requiring Home Assistant's
habluetooth infrastructure.

Key features:
- Implements bleak's BleakClient interface (connect, disconnect, write, notify)
- Uses aioesphomeapi's native API directly
- Maps UUIDs to handles for characteristic operations
- Routes notifications from handles back to UUID-based callbacks
- Maintains connection state and invokes disconnection callbacks

Usage:
    api = APIClient(address=host, port=port, password="", noise_psk=psk)
    await api.connect(login=True)

    client = ESPHomeAPIClient(api, mac_address, disconnected_callback)
    await client.connect()

    await client.start_notify(uuid, callback)
    await client.write_gatt_char(uuid, data)
    await client.disconnect()
"""

import asyncio
import logging
from typing import Callable, Dict, Union
from uuid import UUID

from aioesphomeapi import APIClient

logger = logging.getLogger(__name__)


class ESPHomeAPIClient:
    """Bleak-compatible wrapper around aioesphomeapi for ESP32 Bluetooth Proxy."""

    def __init__(
        self,
        api_client: APIClient,
        mac_address: str,
        disconnected_callback: Callable = None,
        address_type: int = 0,
        feature_flags: int = 0
    ):
        """
        Initialize the ESPHome API client wrapper.

        Args:
            api_client: Connected aioesphomeapi.APIClient instance
            mac_address: BLE device MAC address (format: "AA:BB:CC:DD:EE:FF")
            disconnected_callback: Callback invoked on disconnection (callback(client))
            address_type: BLE address type (0=PUBLIC, 1=RANDOM, default=0)
            feature_flags: ESP32 bluetooth_proxy_feature_flags from device_info (default=0)
        """
        self._api = api_client
        self._mac_address = mac_address
        self._mac_int = int(mac_address.replace(":", ""), 16)
        self._address_type = address_type
        self._feature_flags = feature_flags
        self._disconnected_callback = disconnected_callback
        self._is_connected = False
        self._services = None
        self._uuid_to_handle: Dict[str, int] = {}
        self._handle_to_uuid: Dict[int, str] = {}
        self._notify_callbacks: Dict[int, Callable] = {}
        self._cancel_connection = None

        logger.trace(f"[ESPHomeAPIClient] Initialized for device {mac_address} (int: {self._mac_int}, address_type: {address_type}, feature_flags: {feature_flags})")

    @property
    def is_connected(self) -> bool:
        """Return current connection status."""
        return self._is_connected

    @property
    def services(self):
        """Return GATT service collection (bleak-compatible)."""
        if self._services is None:
            logger.warning("[ESPHomeAPIClient] Services accessed before connection")
            return ESPHomeGATTServiceCollection([])
        return self._services

    async def connect(self, timeout: float = 30.0) -> bool:
        """
        Connect to the BLE device via ESP32 proxy.

        Args:
            timeout: Connection timeout in seconds

        Returns:
            True on successful connection

        Raises:
            Exception: On connection failure or timeout
        """
        logger.trace(f"[ESPHomeAPIClient] Connecting to BLE device {self._mac_address} via ESP32 proxy")
        logger.debug(f"[ESPHomeAPIClient] Using feature_flags: {self._feature_flags}")

        # Create future to track connection state
        connected_future = asyncio.get_running_loop().create_future()

        def on_bluetooth_connection_state(connected: bool, mtu: int, error: int) -> None:
            """Handle connection state changes from ESP32 proxy."""
            if not connected_future.done():
                if error:
                    logger.error(f"[ESPHomeAPIClient] Connection error: {error}")
                    connected_future.set_exception(Exception(f"BLE connection error: {error}"))
                elif connected:
                    logger.debug(f"[ESPHomeAPIClient] BLE connected (MTU: {mtu})")
                    self._is_connected = True
                    connected_future.set_result(mtu)
                else:
                    logger.warning("[ESPHomeAPIClient] Disconnected during connection")
                    connected_future.set_exception(Exception("Disconnected during connection"))
            else:
                # Connection state change after initial connection
                if not connected:
                    logger.debug("[ESPHomeAPIClient] Device disconnected")
                    self._is_connected = False
                    if self._disconnected_callback:
                        try:
                            self._disconnected_callback(self)
                        except Exception as e:
                            logger.error(f"[ESPHomeAPIClient] Error in disconnected callback: {e}")

        # Initiate connection with feature flags
        logger.trace(f"[ESPHomeAPIClient] Calling bluetooth_device_connect for mac_int={self._mac_int}, address_type={self._address_type}, feature_flags={self._feature_flags}")
        self._cancel_connection = await self._api.bluetooth_device_connect(
            self._mac_int,
            on_bluetooth_connection_state,
            address_type=self._address_type,
            feature_flags=self._feature_flags,  # Pass ESP32's advertised features
            has_cache=False,                    # Disable client-side caching
            disconnect_timeout=10.0,            # Allow graceful disconnect
            timeout=timeout
        )

        try:
            # Wait for connection to complete
            mtu = await asyncio.wait_for(connected_future, timeout=timeout)
            logger.info(f"[ESPHomeAPIClient] Successfully connected to {self._mac_address} (MTU: {mtu})")

            # Fetch GATT services and build UUID↔handle mappings
            await self._fetch_services()

            return True

        except asyncio.TimeoutError:
            logger.error(f"[ESPHomeAPIClient] Connection timeout after {timeout}s")
            self._is_connected = False
            if self._cancel_connection:
                self._cancel_connection()
            raise Exception(f"Connection timeout after {timeout}s")
        except Exception as e:
            logger.error(f"[ESPHomeAPIClient] Connection failed: {e}")
            self._is_connected = False
            if self._cancel_connection:
                self._cancel_connection()
            raise

    async def _fetch_services(self):
        """Fetch GATT services and build UUID↔handle mappings."""
        logger.trace("[ESPHomeAPIClient] Fetching GATT services")

        try:
            resp = await self._api.bluetooth_gatt_get_services(self._mac_int)
            services = []

            for svc in resp.services:
                logger.debug(f"[ESPHomeAPIClient] Service: {svc.uuid}")
                characteristics = []

                for char in svc.characteristics:
                    # Normalize UUID to lowercase for consistency with bleak
                    uuid_str = char.uuid.lower()
                    handle = char.handle

                    # Build bidirectional UUID↔handle mapping
                    self._uuid_to_handle[uuid_str] = handle
                    self._handle_to_uuid[handle] = uuid_str

                    logger.debug(
                        f"[ESPHomeAPIClient]   Characteristic: {uuid_str} → handle=0x{handle:04x} "
                        f"properties=0x{char.properties:02x}"
                    )

                    characteristics.append(
                        ESPHomeGATTCharacteristic(
                            uuid=uuid_str,
                            handle=handle,
                            properties=char.properties
                        )
                    )

                services.append(
                    ESPHomeGATTService(
                        uuid=svc.uuid.lower(),
                        characteristics=characteristics
                    )
                )

            self._services = ESPHomeGATTServiceCollection(services)
            logger.trace(
                f"[ESPHomeAPIClient] Service discovery complete: "
                f"{len(services)} services, {len(self._uuid_to_handle)} characteristics"
            )

        except Exception as e:
            logger.error(f"[ESPHomeAPIClient] Failed to fetch services: {e}")
            raise

    async def start_notify(
        self,
        char_specifier: Union[str, UUID, 'ESPHomeGATTCharacteristic'],
        callback: Callable
    ):
        """
        Register notification callback for a characteristic.

        Args:
            char_specifier: UUID string, UUID object, or characteristic object
            callback: Callback function(sender, data) invoked on notifications

        Raises:
            ValueError: If characteristic UUID not found in device services
        """
        # Convert char_specifier to UUID string
        if isinstance(char_specifier, ESPHomeGATTCharacteristic):
            uuid_str = char_specifier.uuid
        elif isinstance(char_specifier, UUID):
            uuid_str = str(char_specifier).lower()
        else:
            uuid_str = str(char_specifier).lower()

        # Look up handle from UUID
        handle = self._uuid_to_handle.get(uuid_str)
        if handle is None:
            logger.error(f"[ESPHomeAPIClient] UUID {uuid_str} not found in services")
            raise ValueError(f"Characteristic UUID {uuid_str} not found in device services")

        logger.trace(f"[ESPHomeAPIClient] Registering notification: {uuid_str} (handle=0x{handle:04x})")

        # Store callback for this handle
        self._notify_callbacks[handle] = callback

        # Internal notification handler that routes to the registered callback
        def on_notify(handle: int, data: bytes) -> None:
            """Route notification from handle to UUID-based callback."""
            uuid = self._handle_to_uuid.get(handle)
            logger.trace(
                f"[ESPHomeAPIClient] Notification received: handle=0x{handle:04x} uuid={uuid} "
                f"len={len(data)} data={data.hex()[:40]}{'...' if len(data) > 20 else ''}"
            )

            callback_fn = self._notify_callbacks.get(handle)
            if callback_fn:
                try:
                    # Create a characteristic wrapper for the callback (bleak compatibility)
                    char_wrapper = ESPHomeGATTCharacteristic(uuid=uuid, handle=handle, properties=0x10)
                    # Invoke the callback asynchronously if it's a coroutine
                    result = callback_fn(char_wrapper, data)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception as e:
                    logger.error(f"[ESPHomeAPIClient] Error in notification callback: {e}")
            else:
                logger.warning(f"[ESPHomeAPIClient] No callback registered for handle 0x{handle:04x}")

        # Subscribe to notifications via ESP32 proxy
        try:
            await self._api.bluetooth_gatt_start_notify(self._mac_int, handle, on_notify)
            logger.debug(f"[ESPHomeAPIClient] Notification enabled for {uuid_str} (handle=0x{handle:04x})")
        except Exception as e:
            logger.error(f"[ESPHomeAPIClient] Failed to start notifications for {uuid_str}: {e}")
            raise

    async def write_gatt_char(
        self,
        char_specifier: Union[str, UUID, 'ESPHomeGATTCharacteristic'],
        data: bytes,
        response: bool = True
    ):
        """
        Write data to a GATT characteristic.

        Args:
            char_specifier: UUID string, UUID object, or characteristic object
            data: Bytes to write
            response: Whether to wait for write response (default: True)

        Raises:
            ValueError: If characteristic UUID not found in device services
        """
        # Convert char_specifier to UUID string
        if isinstance(char_specifier, ESPHomeGATTCharacteristic):
            uuid_str = char_specifier.uuid
        elif isinstance(char_specifier, UUID):
            uuid_str = str(char_specifier).lower()
        else:
            uuid_str = str(char_specifier).lower()

        # Look up handle from UUID
        handle = self._uuid_to_handle.get(uuid_str)
        if handle is None:
            logger.error(f"[ESPHomeAPIClient] UUID {uuid_str} not found in services")
            raise ValueError(f"Characteristic UUID {uuid_str} not found in device services")

        logger.trace(
            f"[ESPHomeAPIClient] Write characteristic: {uuid_str} (handle=0x{handle:04x}) "
            f"len={len(data)} data={data.hex()[:40]}{'...' if len(data) > 20 else ''}"
        )

        try:
            await self._api.bluetooth_gatt_write(
                self._mac_int,
                handle,
                data,
                response=response
            )
            logger.debug(f"[ESPHomeAPIClient] Write successful: {uuid_str} (handle=0x{handle:04x})")
        except Exception as e:
            logger.error(f"[ESPHomeAPIClient] Write failed for {uuid_str}: {e}")
            raise

    async def disconnect(self):
        """Disconnect from the BLE device."""
        logger.trace(f"[ESPHomeAPIClient] Disconnecting from {self._mac_address}")

        if not self._is_connected:
            logger.trace("[ESPHomeAPIClient] Already disconnected")
            return

        try:
            # Disconnect from BLE device
            await self._api.bluetooth_device_disconnect(self._mac_int)
            logger.debug(f"[ESPHomeAPIClient] Disconnected from {self._mac_address}")
        except Exception as e:
            logger.warning(f"[ESPHomeAPIClient] Error during disconnect: {e}")
        finally:
            # Unsubscribe from connection state updates
            if self._cancel_connection:
                self._cancel_connection()
                self._cancel_connection = None

            self._is_connected = False

            # Invoke disconnected callback
            if self._disconnected_callback:
                try:
                    self._disconnected_callback(self)
                except Exception as e:
                    logger.error(f"[ESPHomeAPIClient] Error in disconnected callback: {e}")


# --- Bleak-compatible GATT data structures ---


class ESPHomeGATTServiceCollection:
    """Iterable collection of GATT services (bleak-compatible)."""

    def __init__(self, services: list):
        self._services = services

    def __iter__(self):
        return iter(self._services)

    def __len__(self):
        return len(self._services)


class ESPHomeGATTService:
    """Wrapper for aioesphomeapi GATT service (bleak-compatible)."""

    def __init__(self, uuid: str, characteristics: list):
        self.uuid = uuid
        self.characteristics = characteristics

    def __repr__(self):
        return f"ESPHomeGATTService(uuid={self.uuid}, chars={len(self.characteristics)})"


class ESPHomeGATTCharacteristic:
    """Wrapper for aioesphomeapi GATT characteristic (bleak-compatible)."""

    def __init__(self, uuid: str, handle: int, properties: int):
        self.uuid = uuid
        self.handle = handle
        self.properties = properties

    def __repr__(self):
        return f"ESPHomeGATTCharacteristic(uuid={self.uuid}, handle=0x{self.handle:04x})"
