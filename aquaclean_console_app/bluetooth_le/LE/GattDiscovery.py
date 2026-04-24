"""GATT profile detection for Geberit AquaClean BLE devices.

Used by the HACS config flow to distinguish standard Geberit devices
(standard service UUID 3334429d-...) from unsupported variants (e.g. Alba)
so that users get a clear error with UUID details for GitHub issues instead
of the generic "Cannot connect" message.

Works with both BleakClient (local BLE) and ESPHomeAPIClient (ESPHome proxy).
The client must already be connected when probe_gatt_profile() is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

GEBERIT_SERVICE_UUID = "3334429d-90f3-4c41-a02d-5cb3a03e0000"

# BLE GATT property bitmask constants (Bluetooth Core Spec)
_PROP_WRITE = 0x04
_PROP_WRITE_NO_RESP = 0x08
_PROP_NOTIFY = 0x10


@dataclass
class GattProfile:
    """Result of a GATT profile probe on a connected Geberit device."""

    is_standard: bool
    """True when the standard Geberit AquaClean service UUID is present."""

    svc_uuid: str
    """Primary service UUID (standard, or first candidate for non-standard devices)."""

    write_uuids: List[str] = field(default_factory=list)
    """Write/write-no-response characteristic UUIDs (populated for non-standard devices)."""

    notify_uuids: List[str] = field(default_factory=list)
    """Notify characteristic UUIDs (populated for non-standard devices)."""


def _has_write(char) -> bool:
    """Return True if the characteristic supports writing."""
    props = char.properties
    if isinstance(props, int):
        return bool(props & (_PROP_WRITE | _PROP_WRITE_NO_RESP))
    # Bleak: properties is a list of strings
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


def probe_gatt_profile(client) -> GattProfile:
    """Probe the GATT service table of a connected BLE client.

    Returns a GattProfile describing whether the device has the standard
    Geberit service UUID or a non-standard one.  For non-standard devices,
    write_uuids and notify_uuids are populated so callers can include them
    in a GitHub issue template.

    On any error (e.g. services not yet populated), returns a GattProfile
    with is_standard=True so the caller does not block valid devices.
    """
    try:
        services = client.services
    except Exception:
        # Can't read services — assume standard to avoid false positives
        return GattProfile(is_standard=True, svc_uuid=GEBERIT_SERVICE_UUID)

    # First pass: check for standard Geberit service
    for svc in services:
        if svc.uuid.lower() == GEBERIT_SERVICE_UUID:
            return GattProfile(is_standard=True, svc_uuid=GEBERIT_SERVICE_UUID)

    # Standard service not found.  Find the first candidate service that has
    # both write and notify characteristics — this is the device's data channel.
    _BT_SIG_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"

    for svc in services:
        svc_uuid = svc.uuid.lower()

        # Skip pure BT SIG standard services (0000xxxx-...) unless they contain
        # vendor-specific characteristics (some Geberit variants use a SIG service
        # UUID with vendor chars inside, e.g. 0000fd48 + 559ebXXX chars).
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
