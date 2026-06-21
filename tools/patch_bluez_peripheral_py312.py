#!/usr/bin/env python3
"""Patch bluez_peripheral 0.1.7 for Python 3.12 compatibility.

Python 3.12 changed Flag.__and__ semantics. Two files are affected:

characteristic.py — CharacteristicFlags.Flags D-Bus property:
  `for flag in CharacteristicFlags if self.flags & flag` silently maps
  WRITE(8) -> "write-without-response" instead of "write", causing BlueZ
  to register with properties=4 and never invoke WriteValue.

descriptor.py — DescriptorFlags.Flags D-Bus property:
  Same bug. The CCCD descriptor (auto-registered for every NOTIFY characteristic)
  returns wrong flag strings → BlueZ applies wrong security policy to CCCDs →
  iOS gets ATT Error 0x05 (Insufficient Authentication) on CCCD reads/writes.

Fix: replace Flag-iteration with direct integer bitmask checks in both files.

Usage (on mock VM):
    sudo /home/jens/venv/bin/python3 tools/patch_bluez_peripheral_py312.py
"""
import pathlib
import sys

_BASE = pathlib.Path('/home/jens/venv/lib/python3.12/site-packages/bluez_peripheral/gatt')

# ---- 1. characteristic.py ------------------------------------------------

_char_path = _BASE / 'characteristic.py'
_char_src = _char_path.read_text()

_char_old = '''\
        # Clear the extended properties flag (bluez doesn't seem to like this flag even though its in the docs).
        self.flags &= ~CharacteristicFlags.EXTENDED_PROPERTIES

        # Return a list of set string flag names.
        return [
            snake_to_kebab(flag.name)
            for flag in CharacteristicFlags
            if self.flags & flag
        ]'''

_char_new = '''\
        # Python 3.12 Flag.__and__ regression: iterate by integer bitmask directly.
        # The original Flag-iteration approach silently maps WRITE(8) to
        # "write-without-response" in Python 3.12, causing BlueZ to register
        # properties=4 and never invoke WriteValue. EXTENDED_PROPERTIES(0x80)
        # is intentionally omitted (BlueZ rejects it).
        v = self.flags.value if hasattr(self.flags, 'value') else 0
        return [s for b, s in [
            (0x0001, 'broadcast'),
            (0x0002, 'read'),
            (0x0004, 'write-without-response'),
            (0x0008, 'write'),
            (0x0010, 'notify'),
            (0x0020, 'indicate'),
            (0x0040, 'authenticated-signed-writes'),
            (0x0100, 'reliable-write'),
            (0x0200, 'writable-auxiliaries'),
            (0x0400, 'encrypt-read'),
            (0x0800, 'encrypt-write'),
            (0x1000, 'encrypt-authenticated-read'),
            (0x2000, 'encrypt-authenticated-write'),
            (0x4000, 'secure-read'),
            (0x8000, 'secure-write'),
            (0x10000, 'authorize'),
        ] if v & b]'''

if _char_old in _char_src:
    _char_path.write_text(_char_src.replace(_char_old, _char_new))
    print('characteristic.py: Patched OK')
else:
    print('characteristic.py: Already patched or wrong bluez_peripheral version.')

# ---- 2. descriptor.py ----------------------------------------------------

_desc_path = _BASE / 'descriptor.py'
_desc_src = _desc_path.read_text()

_desc_old = '''\
        # Return a list of string flag names.
        return [
            snake_to_kebab(flag.name) for flag in DescriptorFlags if self.flags & flag
        ]'''

_desc_new = '''\
        # Python 3.12 Flag.__and__ regression: iterate by integer bitmask directly.
        # The original Flag-iteration approach returns wrong strings for descriptor
        # CCCDs in Python 3.12, causing BlueZ to enforce wrong security policy and
        # iOS to receive ATT Error 0x05 on CCCD reads/writes.
        v = self.flags.value if hasattr(self.flags, 'value') else 0
        return [s for b, s in [
            (0x01, 'read'),
            (0x02, 'write'),
            (0x04, 'encrypt-read'),
            (0x08, 'encrypt-write'),
            (0x10, 'encrypt-authenticated-read'),
            (0x20, 'encrypt-authenticated-write'),
            (0x40, 'secure-read'),
            (0x80, 'secure-write'),
            (0x100, 'authorize'),
        ] if v & b]'''

if _desc_old in _desc_src:
    _desc_path.write_text(_desc_src.replace(_desc_old, _desc_new))
    print('descriptor.py:     Patched OK')
else:
    print('descriptor.py:     Already patched or wrong bluez_peripheral version.')

# ---- verify ---------------------------------------------------------------

print()
print('Verify characteristic.py:')
for m in ('bluez_peripheral.gatt.characteristic', 'bluez_peripheral.gatt.descriptor'):
    if m in sys.modules:
        del sys.modules[m]
from bluez_peripheral.gatt.characteristic import CharacteristicFlags
f = CharacteristicFlags.WRITE
v_int = f.value
result = [s for b, s in [
    (0x0004, 'write-without-response'), (0x0008, 'write'), (0x0010, 'notify')
] if v_int & b]
print(f"  CharFlags.WRITE -> {result}  (expected: ['write'])")

print()
print('Verify descriptor.py:')
from bluez_peripheral.gatt.descriptor import DescriptorFlags
d = DescriptorFlags.READ | DescriptorFlags.WRITE
v_int = d.value
result_d = [s for b, s in [(0x01, 'read'), (0x02, 'write')] if v_int & b]
print(f"  DescriptorFlags.READ|WRITE -> {result_d}  (expected: ['read', 'write'])")
