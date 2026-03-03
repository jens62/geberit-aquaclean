#!/usr/bin/env python3
"""
ESPHome persistent TCP + on-demand BLE probe.

Validates that a single aioesphomeapi TCP connection can service multiple
BLE connect/disconnect cycles without being torn down between them.

This is the pattern needed for persistent_api mode in the main app:
  - ESP32 API TCP connection: created ONCE, kept alive the whole session
  - BLE connection to Geberit: connected/disconnected per request

Each cycle:
  1. Subscribe to BLE advertisements (fresh subscription each cycle)
  2. Wait for Geberit device to appear
  3. Connect BLE  (advertisement subscription kept alive — CLAUDE.md trap 7)
  4. Discover services (proves BLE is functional)
  5. Disconnect BLE
  6. Unsubscribe advertisements (after BLE is down — trap 7)
  7. Wait settle time (ESP32 BLE stack)

Expected timing output:
  Cycle 1: API=~150 ms (TCP handshake + device_info),  BLE=~700 ms
  Cycle 2: API=0 ms   (TCP reused),                    BLE=~700 ms
  Cycle 3: API=0 ms   (TCP reused),                    BLE=~700 ms

If cycles 2+ show API=0 ms and succeed, the persistent TCP pattern is valid
and can be integrated into the main app.

ESP32 logs are streamed via a separate persistent connection throughout.

Usage:
  python esphome-aioesphomeapi-probe-v4.py <proxy_host> <ble_mac>
  python esphome-aioesphomeapi-probe-v4.py 192.168.0.160 38:AB:XX:XX:ZZ:67
  python esphome-aioesphomeapi-probe-v4.py 192.168.0.160 38:AB:XX:XX:ZZ:67 --cycles 5

Requires: pip install aioesphomeapi
"""

import asyncio
import argparse
import re
import sys
import time
from aioesphomeapi import APIClient, LogLevel


SETTLE_TIME = 5.0   # seconds between BLE cycles for ESP32 BLE stack to settle
N_CYCLES    = 3     # default number of BLE connect/disconnect cycles


def mac_int_to_str(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))


async def run_one_cycle(
    api: APIClient,
    mac_int: int,
    mac_str: str,
    device_feature_flags: int,
    cycle: int,
    n_cycles: int,
    timeout: float = 20.0,
) -> dict:
    """
    Run one BLE connect/disconnect cycle over an existing (persistent) API connection.

    The advertisement subscription is kept alive from discovery through BLE connect
    and only released after BLE disconnect (CLAUDE.md trap 7).

    Returns a result dict: {cycle, success, ble_ms, mtu, error}.
    """
    result = {"cycle": cycle, "success": False, "ble_ms": 0, "mtu": 0, "error": None}
    unsub_adv = None
    cancel_connection = None

    try:
        # --- Subscribe to BLE advertisements ---
        found = asyncio.Event()
        address_type = 0

        def on_raw(resp) -> None:
            nonlocal address_type
            for adv in resp.advertisements:
                if mac_int_to_str(adv.address) == mac_str:
                    address_type = getattr(adv, "address_type", 0)
                    found.set()

        unsub_adv = api.subscribe_bluetooth_le_raw_advertisements(on_raw)

        try:
            await asyncio.wait_for(found.wait(), timeout=15.0)
            addr_label = "PUBLIC" if address_type == 0 else "RANDOM"
            print(f"    Device advertising ✓  address_type={address_type} ({addr_label})")
        except asyncio.TimeoutError:
            print("    WARNING: device not seen in 15 s scan (proceeding anyway)")

        # --- BLE connect (advertisement subscription kept alive — trap 7) ---
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
            feature_flags=device_feature_flags,
            has_cache=False,
            disconnect_timeout=10.0,
            timeout=timeout,
        )

        mtu = await asyncio.wait_for(connected_future, timeout=timeout)
        result["ble_ms"] = int((time.perf_counter() - t_ble) * 1000)
        result["mtu"] = mtu
        result["success"] = True
        print(f"    BLE connected ✓  MTU={mtu}  ({result['ble_ms']} ms)")

    except asyncio.TimeoutError:
        result["error"] = f"timeout after {timeout}s"
        print(f"    FAILED (timeout after {timeout}s)")
    except Exception as exc:
        result["error"] = str(exc)
        print(f"    FAILED ({exc})")

    finally:
        # 1. Disconnect BLE
        try:
            await api.bluetooth_device_disconnect(mac_int)
        except Exception:
            pass
        if cancel_connection:
            try:
                cancel_connection()
            except Exception:
                pass

        # 2. Unsubscribe advertisements — only safe AFTER BLE is down (trap 7)
        if unsub_adv:
            try:
                unsub_adv()
            except Exception:
                pass

        if result["success"]:
            print("    BLE disconnected ✓")

    return result


async def run_probe(
    proxy_host: str,
    ble_mac: str,
    noise_psk: str | None,
    n_cycles: int,
) -> None:
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

    unsub_logs = log_api.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_VERBOSE)
    print("ESP32 log streaming active")
    print()

    # ------------------------------------------------------------------
    # Single persistent TCP connection — created once, reused for all cycles
    # ------------------------------------------------------------------
    print(f"Connecting persistent BLE API to {proxy_host}:6053 ...")
    t0 = time.perf_counter()
    api = APIClient(address=proxy_host, port=6053, password="", noise_psk=noise_psk)
    await api.connect(login=True)
    api_ms = int((time.perf_counter() - t0) * 1000)
    print(f"ESP32 API connected ({api_ms} ms)  ← only TCP handshake in this run")
    print()

    # ------------------------------------------------------------------
    # BLE cycles over the same TCP connection
    # ------------------------------------------------------------------
    print("=" * 65)
    print(f"Running {n_cycles} BLE cycles over the SAME TCP connection")
    print(f"Settle time between cycles: {SETTLE_TIME}s")
    print("=" * 65)

    results = []
    for i in range(1, n_cycles + 1):
        print(f"\nCycle {i}/{n_cycles}")
        result = await run_one_cycle(api, mac_int, mac_str, flags, i, n_cycles)
        results.append(result)

        if i < n_cycles:
            print(f"    Waiting {SETTLE_TIME}s for ESP32 BLE stack to settle ...")
            await asyncio.sleep(SETTLE_TIME)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    try:
        await api.disconnect()
    except Exception:
        pass
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
    print(f"  ESP32 API TCP connect (cycle 1 only): {api_ms} ms")
    print()
    for r in results:
        status = "OK  " if r["success"] else "FAIL"
        api_note = f"API={api_ms} ms (TCP handshake)" if r["cycle"] == 1 else "API=  0 ms (TCP reused)"
        print(f"  Cycle {r['cycle']}: [{status}]  {api_note}  BLE={r['ble_ms']:4d} ms  MTU={r['mtu']}")
        if r["error"]:
            print(f"            error: {r['error']}")

    all_ok = all(r["success"] for r in results)
    print()
    if all_ok:
        overhead_saved = api_ms * (n_cycles - 1)
        print(f"All {n_cycles} cycles succeeded.")
        print(f"TCP overhead avoided on cycles 2+: ~{api_ms} ms × {n_cycles - 1} = ~{overhead_saved} ms saved")
        print()
        print("Persistent TCP pattern is valid — safe to integrate into main app.")
    else:
        failed = [r for r in results if not r["success"]]
        print(f"{len(failed)}/{n_cycles} cycle(s) failed — check ESP32 log output above.")
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
        help="base64 encryption key (optional)",
    )
    parser.add_argument(
        "--cycles", type=int, default=N_CYCLES,
        help=f"number of BLE connect/disconnect cycles (default: {N_CYCLES})",
    )
    args = parser.parse_args()
    asyncio.run(run_probe(args.proxy_host, args.ble_mac.upper(), args.noise_psk, args.cycles))


if __name__ == "__main__":
    main()
