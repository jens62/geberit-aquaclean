"""GATT profile detection for Geberit AquaClean BLE devices.

Shared by the HACS config flow (config_flow.py) and the standalone connection
test tool (tools/aquaclean-connection-test.py).  Contains the classification
algorithm and constants so neither consumer duplicates the logic.

Two public entry points:
- classify_services(services_iterable)  — takes any iterable of GATT service
  objects (raw aioesphomeapi protobuf OR bleak-compatible wrappers); called by
  both the connection test tool and probe_gatt_profile().
- probe_gatt_profile(client)            — convenience wrapper for a connected
  BleakClient or ESPHomeAPIClient; reads client.services and delegates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

# Standard Geberit AquaClean GATT service UUID (Mera Comfort and similar models)
GEBERIT_SERVICE_UUID = "3334429d-90f3-4c41-a02d-5cb3a03e0000"

# Bluetooth SIG base UUID suffix — services matching "0000xxxx<suffix>" are
# standard BLE services (Generic Access, Device Information, etc.).
# Exception: some Geberit variants use a BT SIG member service UUID (e.g.
# 0000fd48) with vendor-specific characteristics inside — those ARE candidates.
_BT_SIG_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"

# BLE GATT property bitmask constants (Bluetooth Core Spec)
_PROP_WRITE = 0x04
_PROP_WRITE_NO_RESP = 0x08
_PROP_NOTIFY = 0x10


@dataclass
class GattProfile:
    """Result of a GATT profile classification for a Geberit device."""

    is_standard: bool
    """True when the standard Geberit AquaClean service UUID is present."""

    svc_uuid: str
    """Primary service UUID (standard, or first candidate for non-standard devices)."""

    write_uuids: List[str] = field(default_factory=list)
    """Write/write-no-response characteristic UUIDs (populated for non-standard devices)."""

    notify_uuids: List[str] = field(default_factory=list)
    """Notify characteristic UUIDs (populated for non-standard devices)."""


def _has_write(char) -> bool:
    """Return True if the characteristic supports writing.

    Handles both integer bitmask (aioesphomeapi protobuf / ESPHomeGATTCharacteristic)
    and list-of-strings (Bleak BleakGATTCharacteristic).
    """
    props = char.properties
    if isinstance(props, int):
        return bool(props & (_PROP_WRITE | _PROP_WRITE_NO_RESP))
    if isinstance(props, (list, tuple)):
        return any(p in props for p in ("write", "write-without-response"))
    return False


def _has_notify(char) -> bool:
    """Return True if the characteristic supports notifications."""
    props = char.properties
    if isinstance(props, int):
        return bool(props & _PROP_NOTIFY)
    if isinstance(props, (list, tuple)):
        return "notify" in props
    return False


def classify_services(services_iterable) -> GattProfile:
    """Classify a GATT service table and return a GattProfile.

    Works with any iterable of service objects that expose .uuid and
    .characteristics, where each characteristic exposes .uuid, .properties
    (int bitmask or list of strings), and optionally .handle.

    Compatible with:
    - aioesphomeapi raw protobuf BluetoothGATTService objects (from
      bluetooth_gatt_get_services — used in aquaclean-connection-test.py)
    - ESPHomeGATTService / BleakGATTService wrapper objects (used in
      BluetoothLeConnector / HACS config flow via probe_gatt_profile)

    Returns GattProfile with is_standard=True if the standard Geberit service
    UUID is present.  For non-standard devices, write_uuids and notify_uuids
    are populated for use in GitHub issue templates.
    """
    # Materialise once so we can iterate twice (first for standard check,
    # second for candidate extraction).  GATT tables are tiny (<20 services).
    try:
        services = list(services_iterable)
    except Exception:
        return GattProfile(is_standard=True, svc_uuid=GEBERIT_SERVICE_UUID)

    # First pass: check for standard Geberit service UUID
    for svc in services:
        if svc.uuid.lower() == GEBERIT_SERVICE_UUID:
            return GattProfile(is_standard=True, svc_uuid=GEBERIT_SERVICE_UUID)

    # Standard service not found — find the first candidate service that has
    # both write and notify characteristics.  This is the device's data channel.
    for svc in services:
        svc_uuid = svc.uuid.lower()

        # Skip pure BT SIG standard services unless they contain vendor-specific
        # characteristics (some Geberit variants use a SIG service UUID with
        # custom chars inside, e.g. 0000fd48 + 559ebXXX characteristics).
        is_std_svc = svc_uuid.endswith(_BT_SIG_BASE_SUFFIX) and svc_uuid.startswith("0000")
        has_vendor_chars = any(
            not (c.uuid.lower().endswith(_BT_SIG_BASE_SUFFIX) and c.uuid.lower().startswith("0000"))
            for c in svc.characteristics
        )
        if is_std_svc and not has_vendor_chars:
            continue

        write_uuids: list[str] = []
        notify_uuids: list[str] = []
        seen_handles: set = set()
        for char in svc.characteristics:
            handle = getattr(char, "handle", None)
            if _has_write(char) and handle not in seen_handles:
                if handle is not None:
                    seen_handles.add(handle)
                write_uuids.append(char.uuid)
            if _has_notify(char):
                notify_uuids.append(char.uuid)

        if write_uuids and notify_uuids:
            return GattProfile(
                is_standard=False,
                svc_uuid=svc_uuid,
                write_uuids=write_uuids,
                notify_uuids=notify_uuids,
            )

    # No candidate found at all
    return GattProfile(is_standard=False, svc_uuid="unknown")


def probe_gatt_profile(client) -> GattProfile:
    """Probe the GATT service table of a connected BLE client.

    Convenience wrapper around classify_services() for use after
    connect_ble_only() in BluetoothLeConnector.  Works with both BleakClient
    (local BLE) and ESPHomeAPIClient (ESPHome proxy).

    Returns GattProfile with is_standard=True on any error so valid devices
    are never falsely rejected.
    """
    try:
        services = client.services
    except Exception:
        return GattProfile(is_standard=True, svc_uuid=GEBERIT_SERVICE_UUID)
    return classify_services(services)
