#!/usr/bin/env python3
"""
ESPHome BLE connection probe — tests has_cache/address_type combinations.

The CONNECT_V3_WITHOUT_CACHE request type (has_cache=False) causes a
"Disconnect before connected" error on ESPHome 2026.1.x firmware,
immediately disconnecting with reason 0x16.

aioesphomeapi source (client.py) reveals only two code paths:
  has_cache=True  → CONNECT_V3_WITH_CACHE
  has_cache=False → CONNECT_V3_WITHOUT_CACHE  (requires REMOTE_CACHING flag, bit 2)
The old CONNECT method is fully removed; feature_flags only matters for the
REMOTE_CACHING bit check. All tests therefore use the actual feature_flags=127
reported by the ESP32; only has_cache and address_type are varied.

ESP32 logs are streamed via a separate persistent API connection so you can
see exactly what the firmware does during each BLE connection attempt.

Each test uses a fresh API connection. The advertisement subscription is kept
alive during the BLE connect, matching production code (unsubscribing first
causes the ESP32 to close the TCP connection — CLAUDE.md trap 7).

Usage:
  python esphome-aioesphomeapi-probe-v3.py <proxy_host> <ble_mac>
  python esphome-aioesphomeapi-probe-v3.py 192.168.0.160 38:AB:XX:XX:ZZ:67

Requires: pip install aioesphomeapi
"""

import asyncio
import argparse
import re
import sys
from aioesphomeapi import APIClient, LogLevel


# -------------------------------------------------------------------
# Test matrix — (label, has_cache, address_type)
# feature_flags is read live from device_info and passed as-is.
# The only variable that changes the protocol is has_cache.
# -------------------------------------------------------------------
TESTS = [
    # label                       has_cache  address_type
    ("WITHOUT_CACHE + PUBLIC",    False,     0),  # current ESPHomeAPIClient.py — fails
    ("WITH_CACHE    + PUBLIC",    True,      0),  # CONNECT_V3_WITH_CACHE
    ("WITHOUT_CACHE + RANDOM",    False,     1),
    ("WITH_CACHE    + RANDOM",    True,      1),
]

# Seconds to wait between BLE connection attempts so the ESP32 BLE stack settles
SETTLE_TIME = 8.0


def mac_int_to_str(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))


async def try_connect_one(
    proxy_host: str,
    noise_psk: str | None,
    mac_int: int,
    mac_str: str,
    label: str,
    device_feature_flags: int,
    has_cache: bool,
    address_type: int,
    timeout: float = 20.0,
) -> bool:
    """
    Run a single BLE connection attempt using a fresh API connection.

    The advertisement subscription is kept alive throughout the BLE connect,
    matching production code behaviour (unsubscribing before BLE connect causes
    the ESP32 to close the TCP connection — CLAUDE.md trap 7).
    """
    addr_label = "PUBLIC" if address_type == 0 else "RANDOM"
    print(f"\n  [{label}]")
    print(f"    feature_flags={device_feature_flags}  has_cache={has_cache}  address_type={address_type} ({addr_label})")

    api = APIClient(address=proxy_host, port=6053, password="", noise_psk=noise_psk)
    unsub_adv = None
    cancel_connection = None

    try:
        await api.connect(login=True)

        # --- Advertisement subscription (keep alive until BLE connect completes) ---
        found = asyncio.Event()

        def on_raw(resp) -> None:
            for adv in resp.advertisements:
                if mac_int_to_str(adv.address) == mac_str:
                    found.set()

        unsub_adv = api.subscribe_bluetooth_le_raw_advertisements(on_raw)

        try:
            await asyncio.wait_for(found.wait(), timeout=10.0)
            print(f"    Device advertising ✓")
        except asyncio.TimeoutError:
            print(f"    WARNING: device not seen in quick scan (may still connect)")

        # --- BLE connection (advertisement subscription intentionally still active) ---
        loop = asyncio.get_running_loop()
        connected_future: asyncio.Future = loop.create_future()

        def on_state(connected: bool, mtu: int, error: int) -> None:
            if connected_future.done():
                return
            if error:
                connected_future.set_exception(Exception(f"connection error code {error}"))
            elif connected:
                connected_future.set_result(mtu)
            else:
                connected_future.set_exception(Exception("disconnected during connect"))

        cancel_connection = await api.bluetooth_device_connect(
            mac_int,
            on_state,
            address_type=address_type,
            feature_flags=device_feature_flags,
            has_cache=has_cache,
            disconnect_timeout=10.0,
            timeout=timeout,
        )

        mtu = await asyncio.wait_for(connected_future, timeout=timeout)
        print(f"    RESULT: SUCCESS  MTU={mtu}")
        return True

    except asyncio.TimeoutError:
        print(f"    RESULT: FAILED   (timeout after {timeout}s)")
        return False
    except Exception as exc:
        print(f"    RESULT: FAILED   ({exc})")
        return False
    finally:
        # Disconnect BLE
        try:
            await api.bluetooth_device_disconnect(mac_int)
        except Exception:
            pass
        if cancel_connection:
            try:
                cancel_connection()
            except Exception:
                pass
        # Now safe to unsubscribe from advertisements (BLE is done)
        if unsub_adv:
            try:
                unsub_adv()
            except Exception:
                pass
        try:
            await api.disconnect()
        except Exception:
            pass
        print(f"    Waiting {SETTLE_TIME}s for ESP32 BLE stack to settle ...")
        await asyncio.sleep(SETTLE_TIME)


async def run_probe(proxy_host: str, ble_mac: str, noise_psk: str | None) -> None:
    mac_str = ble_mac.upper()
    mac_int = int(ble_mac.replace(":", ""), 16)

    # ------------------------------------------------------------------
    # Persistent log streaming connection (separate from BLE operations)
    # ------------------------------------------------------------------
    print(f"Connecting log streaming API to {proxy_host}:6053 ...")
    log_api = APIClient(address=proxy_host, port=6053, password="", noise_psk=noise_psk)
    await log_api.connect(login=True)

    info = await log_api.device_info()
    flags = getattr(info, "bluetooth_proxy_feature_flags", 0)
    print(f"Device:  {info.name}")
    print(f"ESPHome: {info.esphome_version}")
    print(f"bluetooth_proxy_feature_flags: {flags}  (0b{flags:08b})")
    print()

    ansi_escape = re.compile(r'(?:\x1b|\033)\[[0-9;]*m')

    def on_log(log_entry) -> None:
        """Parse ESP32 log entry — same logic as main.py _on_esphome_log_message."""
        try:
            raw = log_entry.message if hasattr(log_entry, "message") else str(log_entry)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            clean = ansi_escape.sub("", raw)
            m = re.match(r'^\[([DEWIVT])\]\[([^\]]+?)(?::\d+)?\]:\s*(.+)$', clean)
            if m:
                level_char, component, message = m.groups()
                print(f"  [ESP32:{component}] {level_char}: {message}")
            else:
                print(f"  [ESP32] {clean}")
        except Exception as exc:
            print(f"  [ESP32:?] parse error: {exc}")

    # subscribe_logs returns a callable (not a coroutine) — do NOT await
    unsub_logs = log_api.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_VERBOSE)
    print("ESP32 log streaming active (firmware compiled at INFO — will show INFO+ messages)")
    print()

    # ------------------------------------------------------------------
    # Test matrix
    # ------------------------------------------------------------------
    print("=" * 65)
    print("Running BLE connection test matrix ...")
    print("Each test uses a FRESH API connection.")
    print("Advertisement subscription stays alive during BLE connect.")
    print("=" * 65)

    results: list[tuple[str, bool, int, bool]] = []
    for label, hc, at in TESTS:
        ok = await try_connect_one(
            proxy_host, noise_psk, mac_int, mac_str, label, flags, hc, at
        )
        results.append((label, hc, at, ok))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    try:
        unsub_logs()
    except Exception:
        pass
    try:
        await log_api.disconnect()
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 65)
    print("SUMMARY")
    print("=" * 65)
    for label, hc, at, ok in results:
        status = "OK  " if ok else "FAIL"
        addr_str = "PUBLIC" if at == 0 else "RANDOM"
        print(f"  [{status}]  has_cache={str(hc):<5}  addr={addr_str:<6}  — {label}")

    passing = [(label, hc, at) for label, hc, at, ok in results if ok]
    print()
    if passing:
        label, hc, at = passing[0]
        addr_str = "PUBLIC" if at == 0 else "RANDOM"
        print(f"First working combination:")
        print(f"  feature_flags={flags} (device actual), has_cache={hc}, address_type={at} ({addr_str})")
        print()
        print(f"Apply to ESPHomeAPIClient.py bluetooth_device_connect() call:")
        print(f"  has_cache={hc},")
        print(f"  address_type={at},")
    else:
        print("All combinations failed — check ESP32 log output above for clues.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("proxy_host", help="ESP32 IP or hostname (e.g. 192.168.0.160)")
    parser.add_argument("ble_mac",    help="BLE device MAC (e.g. 38:AB:XX:XX:ZZ:67)")
    parser.add_argument(
        "--noise-psk", default=None, dest="noise_psk",
        help="base64 encryption key (optional)"
    )
    args = parser.parse_args()
    asyncio.run(run_probe(args.proxy_host, args.ble_mac.upper(), args.noise_psk))


if __name__ == "__main__":
    main()
