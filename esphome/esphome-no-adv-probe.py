#!/usr/bin/env python3
"""
No-advertisement BLE connect probe.

Tests whether the ESP32 can GATT-connect to the Geberit directly by MAC address
WITHOUT waiting for a live advertisement — using has_cache=True and a known
address_type.

Background: the current ESPHome proxy path always scans for a live advertisement
first (30s timeout) to discover address_type before connecting. On the direct
bleak path, the OS (BlueZ) caches devices, so no live advertisement is needed.
After a ToggleLid command the Geberit stops advertising for ~6 minutes, causing
5 consecutive E0002 failures and triggering the circuit breaker.

If has_cache=True allows connect without a prior advertisement, we can cache
address_type after the first successful scan and skip scanning on reconnects.

Test sequence:
  Phase 1: Normal scan-then-connect (baseline — get address_type from live adv)
  Phase 2: Wait for Geberit to stop advertising (you physically move far away,
           or trigger ToggleLid, or just run Phase 2 immediately and trust the
           --no-scan flag)
  Phase 3: Connect directly WITHOUT scanning, using has_cache=True + known address_type
           Repeat --cycles times to confirm reliability

Usage:
  python esphome-no-adv-probe.py <proxy_host> <ble_mac>
  python esphome-no-adv-probe.py 192.168.0.114 38:AB:41:2A:0D:67

  # Skip Phase 1 scan (use address_type=0 assumed PUBLIC) and go straight to
  # no-scan connect — useful when device is known to be temporarily non-advertising:
  python esphome-no-adv-probe.py 192.168.0.114 38:AB:41:2A:0D:67 --skip-scan

  # Control number of no-scan connect cycles in Phase 3:
  python esphome-no-adv-probe.py 192.168.0.114 38:AB:41:2A:0D:67 --cycles 3

Requires: pip install aioesphomeapi
"""

import asyncio
import argparse
import re
import sys
import time
from aioesphomeapi import APIClient, LogLevel


SETTLE_TIME = 5.0   # seconds between BLE cycles
SCAN_TIMEOUT = 30.0
CONNECT_TIMEOUT = 15.0


def mac_int_to_str(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))


async def scan_for_device(api: APIClient, mac_str: str, timeout: float = SCAN_TIMEOUT) -> tuple[int, object]:
    """
    Subscribe to BLE advertisements and wait for the device to appear.
    Returns (address_type, unsub_callable).

    IMPORTANT: caller must keep unsub_callable alive through the BLE connect
    and only call it AFTER BLE disconnect (CLAUDE.md trap 7: unsubbing while
    BLE is active causes the ESP32 to immediately drop the BLE client).

    Raises asyncio.TimeoutError if device not seen within timeout.
    """
    found = asyncio.Event()
    address_type = 0

    def on_raw(resp) -> None:
        nonlocal address_type
        for adv in resp.advertisements:
            if mac_int_to_str(adv.address) == mac_str:
                address_type = getattr(adv, "address_type", 0)
                found.set()

    unsub = api.subscribe_bluetooth_le_raw_advertisements(on_raw)
    try:
        await asyncio.wait_for(found.wait(), timeout=timeout)
        return address_type, unsub
    except asyncio.TimeoutError:
        try:
            unsub()
        except Exception:
            pass
        raise


async def connect_ble(
    api: APIClient,
    mac_int: int,
    mac_str: str,
    feature_flags: int,
    address_type: int,
    has_cache: bool,
    unsub_adv=None,
    timeout: float = CONNECT_TIMEOUT,
) -> dict:
    """
    Attempt a BLE GATT connection.
    unsub_adv: advertisement unsub callable from scan_for_device() — held alive
               through the connect and released only AFTER BLE disconnect (trap 7).
    Returns result dict: {success, ble_ms, mtu, error}.
    """
    result = {"success": False, "ble_ms": 0, "mtu": 0, "error": None}
    cancel_connection = None

    try:
        t_ble = time.perf_counter()
        loop = asyncio.get_running_loop()
        connected_future: asyncio.Future = loop.create_future()

        def on_state(connected: bool, mtu: int, error: int) -> None:
            if connected_future.done():
                return
            if error:
                connected_future.set_exception(Exception(f"BLE error code {error}"))
            elif connected:
                connected_future.set_result(mtu)
            else:
                connected_future.set_exception(Exception("BLE disconnected during connect"))

        cancel_connection = await api.bluetooth_device_connect(
            mac_int,
            on_state,
            address_type=address_type,
            feature_flags=feature_flags,
            has_cache=has_cache,
            disconnect_timeout=10.0,
            timeout=timeout,
        )

        mtu = await asyncio.wait_for(connected_future, timeout=timeout)
        result["ble_ms"] = int((time.perf_counter() - t_ble) * 1000)
        result["mtu"] = mtu
        result["success"] = True

    except asyncio.TimeoutError:
        result["error"] = f"BLE connect timeout after {timeout}s"
    except Exception as exc:
        result["error"] = str(exc)

    finally:
        # 1. Disconnect BLE first
        try:
            await api.bluetooth_device_disconnect(mac_int)
        except Exception:
            pass
        if cancel_connection:
            try:
                cancel_connection()
            except Exception:
                pass
        # 2. Only NOW release advertisement subscription (trap 7)
        if unsub_adv:
            try:
                unsub_adv()
            except Exception:
                pass

    return result


async def run_probe(
    proxy_host: str,
    ble_mac: str,
    noise_psk: str | None,
    n_cycles: int,
    skip_scan: bool,
) -> None:
    mac_str = ble_mac.upper()
    mac_int = int(ble_mac.replace(":", ""), 16)

    # ------------------------------------------------------------------
    # ESP32 API connection
    # ------------------------------------------------------------------
    print(f"Connecting to ESP32 proxy at {proxy_host}:6053 ...")
    t0 = time.perf_counter()
    api = APIClient(address=proxy_host, port=6053, password="", noise_psk=noise_psk)
    await api.connect(login=True)
    api_ms = int((time.perf_counter() - t0) * 1000)

    info = await api.device_info()
    feature_flags = getattr(info, "bluetooth_proxy_feature_flags", 0)
    print(f"ESP32 proxy:  {info.name}")
    print(f"ESPHome:      {info.esphome_version}")
    print(f"Feature flags: {feature_flags}  (0b{feature_flags:08b})")
    print(f"API connected ({api_ms} ms)")
    print()

    # Optional: stream ESP32 logs for diagnostics
    ansi_escape = re.compile(r'(?:\x1b|\033)\[[0-9;]*m')

    def on_log(log_entry) -> None:
        try:
            raw = log_entry.message if hasattr(log_entry, "message") else str(log_entry)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            clean = ansi_escape.sub("", raw)
            m = re.match(r'^\[([DEWIVT])\]\[([^\]]+?)(?::\d+)?\]:\s*(.+)$', clean)
            if m:
                level_char, component, message = m.groups()
                # Only show BLE-relevant ESP32 log lines
                if any(k in component.lower() for k in ("ble", "bt", "gap", "gatt")):
                    print(f"  [ESP32:{component}] {level_char}: {message}")
        except Exception:
            pass

    unsub_logs = api.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_VERBOSE)

    # ------------------------------------------------------------------
    # Phase 1: scan-then-connect (baseline, discovers address_type)
    # ------------------------------------------------------------------
    address_type = 0  # default PUBLIC — safe for Geberit which always uses 0

    if not skip_scan:
        print("=" * 65)
        print("PHASE 1: Scan for live advertisement (baseline)")
        print("=" * 65)
        print(f"Scanning for {mac_str} (timeout: {SCAN_TIMEOUT}s) ...")
        unsub_adv = None
        try:
            address_type, unsub_adv = await scan_for_device(api, mac_str, timeout=SCAN_TIMEOUT)
            addr_label = "PUBLIC" if address_type == 0 else "RANDOM"
            print(f"  Device found ✓  address_type={address_type} ({addr_label})")
        except asyncio.TimeoutError:
            print(f"  Device NOT seen within {SCAN_TIMEOUT}s — using address_type=0 (PUBLIC) as fallback")

        print()
        print("  Connecting WITH scan result (has_cache=False) ...")
        r = await connect_ble(api, mac_int, mac_str, feature_flags, address_type, has_cache=False, unsub_adv=unsub_adv)
        if r["success"]:
            print(f"  BLE connected ✓  MTU={r['mtu']}  ({r['ble_ms']} ms)")
            print("  BLE disconnected ✓")
        else:
            print(f"  FAILED: {r['error']}")
            print("  (Continuing to Phase 2 anyway)")

        print()
        print(f"Waiting {SETTLE_TIME}s before Phase 2 ...")
        await asyncio.sleep(SETTLE_TIME)
        print()
    else:
        print("--skip-scan: skipping Phase 1 scan, assuming address_type=0 (PUBLIC)")
        print()

    # ------------------------------------------------------------------
    # Phase 2: confirm device is NOT advertising (optional manual step)
    # ------------------------------------------------------------------
    print("=" * 65)
    print("PHASE 2: Verify device is NOT advertising (optional)")
    print("=" * 65)
    print("Scanning briefly (5s) to check if device is currently advertising ...")
    try:
        at, unsub_phase2 = await scan_for_device(api, mac_str, timeout=5.0)
        try:
            unsub_phase2()
        except Exception:
            pass
        print(f"  Device IS advertising (address_type={at}).")
        print("  NOTE: For the real-world test, run this script right after a")
        print("  ToggleLid command while the device has stopped advertising.")
        print("  Proceeding with Phase 3 anyway to test both cases.")
    except asyncio.TimeoutError:
        print("  Device is NOT currently advertising ✓ — ideal for Phase 3 test")
    print()

    # ------------------------------------------------------------------
    # Phase 3: connect WITHOUT advertisement scan (the key test)
    # ------------------------------------------------------------------
    print("=" * 65)
    print(f"PHASE 3: No-scan connect (has_cache=True, address_type={address_type})")
    print(f"         {n_cycles} cycle(s)")
    print("=" * 65)
    print()
    print("This tests whether the ESP32 can GATT-connect directly by MAC")
    print("without a prior advertisement, using the cached address_type.")
    print()

    results = []
    for i in range(1, n_cycles + 1):
        print(f"Cycle {i}/{n_cycles}: connecting with has_cache=True, address_type={address_type} ...")
        r = await connect_ble(
            api, mac_int, mac_str, feature_flags,
            address_type=address_type,
            has_cache=True,
        )
        r["cycle"] = i
        results.append(r)
        if r["success"]:
            print(f"  BLE connected ✓  MTU={r['mtu']}  ({r['ble_ms']} ms)")
            print(f"  BLE disconnected ✓")
        else:
            print(f"  FAILED: {r['error']}")

        if i < n_cycles:
            print(f"  Waiting {SETTLE_TIME}s ...")
            await asyncio.sleep(SETTLE_TIME)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    try:
        unsub_logs()
    except Exception:
        pass
    try:
        await api.disconnect()
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 65)
    print("SUMMARY")
    print("=" * 65)
    ok = sum(1 for r in results if r["success"])
    fail = len(results) - ok
    print(f"  No-scan connect results: {ok}/{len(results)} succeeded")
    print()
    for r in results:
        status = "OK  " if r["success"] else "FAIL"
        ble_str = f"BLE={r['ble_ms']:4d} ms  MTU={r['mtu']}" if r["success"] else f"error: {r['error']}"
        print(f"  Cycle {r['cycle']}: [{status}]  {ble_str}")
    print()

    if ok == len(results):
        print("CONCLUSION: has_cache=True connects WITHOUT live advertisement ✓")
        print("  → Safe to cache address_type after first scan and skip scanning on reconnects.")
        print("  → This fixes the E0002 chain after ToggleLid (post-command non-advertising window).")
    elif ok > 0:
        print(f"CONCLUSION: Partial — {ok}/{len(results)} succeeded.")
        print("  → has_cache=True works sometimes but not reliably. Investigate failures above.")
    else:
        print("CONCLUSION: FAILED — has_cache=True does NOT work without live advertisement.")
        print("  → ESP32 firmware requires a prior advertisement before GATT connect.")
        print("  → Alternative fix needed (e.g. longer scan timeout, post-command backoff).")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("proxy_host", help="ESP32 IP or hostname (e.g. 192.168.0.114)")
    parser.add_argument("ble_mac",    help="BLE device MAC (e.g. 38:AB:41:2A:0D:67)")
    parser.add_argument(
        "--noise-psk", default=None, dest="noise_psk",
        help="base64 encryption key (optional)",
    )
    parser.add_argument(
        "--cycles", type=int, default=3,
        help="number of no-scan connect cycles in Phase 3 (default: 3)",
    )
    parser.add_argument(
        "--skip-scan", action="store_true", dest="skip_scan",
        help="skip Phase 1 scan; assume address_type=0 (PUBLIC) — use when device is already non-advertising",
    )
    args = parser.parse_args()
    asyncio.run(run_probe(
        args.proxy_host,
        args.ble_mac.upper(),
        args.noise_psk,
        args.cycles,
        args.skip_scan,
    ))


if __name__ == "__main__":
    main()
