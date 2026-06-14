#!/usr/bin/env python3
"""
analyze-ips.py — Parse iOS IPS crash reports and print a readable summary.

Usage:
    analyze-ips.py <file.ips> [options]

Options:
    --all-threads   Show all threads, not just the crashed one
    --no-color      Disable ANSI color output
    --app-only      Show only app (Home.IOS) frames in each thread
"""

import argparse
import json
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_RED    = "\033[1;31m"
_YELLOW = "\033[1;33m"
_CYAN   = "\033[1;36m"
_GREEN  = "\033[1;32m"
_GRAY   = "\033[90m"

_color = True  # toggled by --no-color


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _color else text


# ---------------------------------------------------------------------------
# IPS parsing
# ---------------------------------------------------------------------------

def _parse_ips(path: str):
    """Return (header_dict, body_dict) from a two-JSON-line IPS file."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    nl = content.index("\n")
    header = json.loads(content[:nl])
    body   = json.loads(content[nl + 1:])
    return header, body


def _format_uptime(ms: int) -> str:
    s = ms // 1000
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} m {s % 60} s"
    return f"{s // 3600} h {(s % 3600) // 60} m"


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

SEP = "=" * 72


def _print_header(header: dict, body: dict) -> None:
    app     = header.get("app_name", "?")
    version = header.get("app_version", "?")
    build   = header.get("build_version", "?")
    bundle  = header.get("bundleID", "?")

    model   = body.get("modelCode", "?")
    os_ver  = body.get("osVersion", {})
    os_str  = f"{os_ver.get('train', '?')} ({os_ver.get('build', '?')})"
    capture = body.get("captureTime", "?")
    launch  = body.get("procLaunch", "?")
    uptime  = _format_uptime(body.get("uptime", 0))
    pid     = body.get("pid", "?")

    print(_c(_BOLD, SEP))
    print(_c(_BOLD, f"CRASH REPORT: {app} v{version} ({build})"))
    print(_c(_BOLD, SEP))
    print(f"  Bundle  : {bundle}")
    print(f"  Device  : {model}")
    print(f"  OS      : {os_str}")
    print(f"  Crash   : {capture}")
    print(f"  Launched: {launch}  (uptime {uptime})")
    print(f"  PID     : {pid}")
    print()


def _print_exception(body: dict) -> None:
    exc  = body.get("exception", {})
    term = body.get("termination", {})
    asi  = body.get("asi", {})

    etype  = exc.get("type", "?")
    signal = exc.get("signal", "")
    label  = f"{etype} ({signal})" if signal else etype
    indicator = term.get("indicator", "")

    print(_c(_RED, f"EXCEPTION: {label}"))
    if indicator:
        print(f"  {indicator}")
    for lib, msgs in asi.items():
        for m in msgs:
            print(f"  {_c(_DIM, lib)}: {m}")
    print()


def _frame_line(idx: int, frame: dict, images: list, app_name: str,
                app_only: bool) -> str | None:
    img_idx    = frame.get("imageIndex", -1)
    img_offset = frame.get("imageOffset", 0)
    symbol     = frame.get("symbol", "")
    sym_loc    = frame.get("symbolLocation", 0)

    if 0 <= img_idx < len(images):
        img = images[img_idx]
        img_name = img.get("name", "?")
    else:
        img_name = "?"

    is_app = img_name == app_name
    if app_only and not is_app:
        return None

    # Address relative to image base
    addr_str = f"+{img_offset}"

    # symbol with offset from symbol start
    if symbol and sym_loc:
        sym_str = f"{symbol}  +{sym_loc}"
    elif symbol:
        sym_str = symbol
    else:
        sym_str = addr_str
        addr_str = ""

    lib_col  = 30
    sym_col  = 55
    lib_part = img_name.ljust(lib_col)
    sym_part = sym_str.ljust(sym_col)
    addr_part = addr_str

    if is_app:
        line = f"  {idx:>3}  {_c(_CYAN, lib_part)} {_c(_BOLD, sym_part)} {_c(_GRAY, addr_part)}"
    else:
        line = f"  {idx:>3}  {_c(_DIM, lib_part)} {sym_part} {_c(_GRAY, addr_part)}"

    return line


def _print_thread(thread: dict, images: list, app_name: str,
                  app_only: bool, is_crashed: bool) -> None:
    name  = thread.get("name", "?")
    queue = thread.get("queue", "")
    label = f"{name}  [{queue}]" if queue else name

    if is_crashed:
        print(_c(_RED, f"CRASHED THREAD: {label}"))
    else:
        print(_c(_YELLOW, f"Thread: {label}"))

    frames = thread.get("frames", [])
    printed = 0
    for i, frame in enumerate(frames):
        line = _frame_line(i, frame, images, app_name, app_only)
        if line is not None:
            print(line)
            printed += 1

    if app_only and printed == 0:
        print(_c(_DIM, "  (no app frames)"))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global _color

    parser = argparse.ArgumentParser(
        description="Analyze iOS IPS crash reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ips", help="Path to .ips crash report file")
    parser.add_argument("--all-threads", action="store_true",
                        help="Show all threads (default: crashed thread only)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI color output")
    parser.add_argument("--app-only", action="store_true",
                        help="Show only app frames in each thread")
    args = parser.parse_args()

    if args.no_color:
        _color = False

    try:
        header, body = _parse_ips(args.ips)
    except FileNotFoundError:
        print(f"Error: file not found: {args.ips}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: JSON parse failed: {e}", file=sys.stderr)
        return 1

    images   = body.get("usedImages", [])
    app_name = images[0].get("name", "Home.IOS") if images else "Home.IOS"
    faulting = body.get("faultingThread", 0)
    threads  = body.get("threads", [])

    _print_header(header, body)
    _print_exception(body)

    if args.all_threads:
        for i, t in enumerate(threads):
            _print_thread(t, images, app_name, args.app_only,
                          is_crashed=i == faulting)
    else:
        # Crashed thread only — prefer the one with triggered=true, fall back to faultingThread index
        crashed = next((t for t in threads if t.get("triggered")), None)
        if crashed is None and faulting < len(threads):
            crashed = threads[faulting]
        if crashed:
            _print_thread(crashed, images, app_name, args.app_only,
                          is_crashed=True)
        else:
            print(_c(_YELLOW, "(no thread data found)"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
