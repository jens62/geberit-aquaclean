#!/usr/bin/env python3
"""Patch bluez_peripheral 0.1.7 characteristic.Flags for Python 3.12 compatibility.

Python 3.12 changed Flag.__and__ semantics. The original Flags D-Bus property uses
`for flag in CharacteristicFlags if self.flags & flag` which silently maps
CharFlags.WRITE(8) -> "write-without-response" instead of "write", causing BlueZ
to register the characteristic with properties=4 and never call WriteValue.

Fix: replace Flag-iteration with direct integer bitmask checks.

Usage (on mock-raspi):
    sudo /home/jens/venv/bin/python3 tools/patch_bluez_peripheral_py312.py
"""
import pathlib

path = pathlib.Path('/home/jens/venv/lib/python3.12/site-packages/bluez_peripheral/gatt/characteristic.py')
src = path.read_text()

old = '''\
        # Clear the extended properties flag (bluez doesn't seem to like this flag even though its in the docs).
        self.flags &= ~CharacteristicFlags.EXTENDED_PROPERTIES

        # Return a list of set string flag names.
        return [
            snake_to_kebab(flag.name)
            for flag in CharacteristicFlags
            if self.flags & flag
        ]'''

new = '''\
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

if old in src:
    path.write_text(src.replace(old, new))
    print('Patched OK')
    print('Verify:')
    import sys
    if 'bluez_peripheral.gatt.characteristic' in sys.modules:
        del sys.modules['bluez_peripheral.gatt.characteristic']
    from bluez_peripheral.gatt.characteristic import CharacteristicFlags
    f = CharacteristicFlags.WRITE
    v = f.value
    result = [s for b, s in [
        (0x0004, 'write-without-response'), (0x0008, 'write'), (0x0010, 'notify')
    ] if v & b]
    print(f"  CharFlags.WRITE -> {result}  (expected: ['write'])")
else:
    print('Already patched or wrong bluez_peripheral version.')
    print('Expected bluez_peripheral==0.1.7 installed at:')
    print(f'  {path}')
