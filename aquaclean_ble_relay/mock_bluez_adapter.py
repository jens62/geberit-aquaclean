"""Shared BlueZ adapter selection for Geberit mock BLE peripherals.

Lets a mock instance bind to a specific USB BT dongle (by BlueZ node name,
e.g. "hci1") instead of always taking the first adapter found. This is what
lets two mock instances (e.g. two mock-geberit-alba.py, or one Alba + one
Mera) run simultaneously on one host, each on its own physical adapter -
see docs/developer/mock-geberit-alba.md and mock_persistence.py (which uses
the resolved adapter address as the per-instance persistence key).
"""
from bluez_peripheral.util import Adapter
from dbus_next import Variant


async def select_adapter(bus, adapter_name: str | None):
    """Find a Bluetooth adapter via ObjectManager: the one matching adapter_name
    (BlueZ node name, e.g. "hci1"), or the first available adapter if
    adapter_name is None.

    Returns (adapter_wrapper, adapter_path, adapter_address, objmgr).
    Raises ValueError if adapter_name is given but no matching adapter is found.
    """
    introspection = await bus.introspect('org.bluez', '/')
    proxy = bus.get_proxy_object('org.bluez', '/', introspection)
    objmgr = proxy.get_interface('org.freedesktop.DBus.ObjectManager')
    managed = await objmgr.call_get_managed_objects()

    candidates = []  # (path, address)
    for path, ifaces in managed.items():
        if 'org.bluez.Adapter1' in ifaces:
            addr = ifaces['org.bluez.Adapter1'].get('Address')
            if isinstance(addr, Variant):
                addr = addr.value
            candidates.append((path, addr))

    if not candidates:
        return None, None, None, objmgr

    if adapter_name:
        target_path = f'/org/bluez/{adapter_name}'
        match = next((c for c in candidates if c[0] == target_path), None)
        if match is None:
            available = ", ".join(p.rsplit('/', 1)[-1] for p, _ in candidates)
            raise ValueError(
                f"Adapter '{adapter_name}' not found. Available adapters: {available or 'none'}"
            )
    else:
        match = candidates[0]

    adapter_path, adapter_address = match
    adapter_introspection = await bus.introspect('org.bluez', adapter_path)
    adapter_proxy = bus.get_proxy_object('org.bluez', adapter_path, adapter_introspection)
    adapter_wrapper = Adapter(adapter_proxy)

    return adapter_wrapper, adapter_path, adapter_address, objmgr
