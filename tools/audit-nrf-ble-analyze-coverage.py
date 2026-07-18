#!/usr/bin/env python3
"""
Completeness regression check for tools/nrf-ble-analyze.py.

Background (2026-07-18): two independent, confirmed bugs were found in the
same day, both caused by the same root cause. _run_tshark() defaults to
occurrence="f" (tshark's "first occurrence only" field-extraction mode) —
silently returning just the first value whenever tshark reports MORE than
one occurrence of a queried field within a single matched frame.

  1. BLE advertising: a real device sends a second, separate Manufacturer
     Specific Data entry (the RS firmware-version tail) that the tool never
     showed, because --adv only queried the first company_id/data occurrence.
  2. GATT discovery (--gatt-map): READ_BY_GROUP_TYPE_RSP / READ_BY_TYPE_RSP /
     FIND_INFO_RSP frames routinely pack MULTIPLE handle/UUID pairs into one
     PDU (not an edge case — the common case for any service/characteristic
     list longer than one item). Only the first pair was ever captured.

Both are now fixed (see _extract_gatt_handles' -T pdml rewrite and the
occurrence="a" advertising fix). This script exists so the NEXT instance of
this bug class doesn't require another manual audit to find.

How it works: monkeypatches _run_tshark to RECORD every (display_filter,
fields, occurrence) tuple actually invoked while driving the tool through
its main code paths (--markdown, --gatt-map, --adv, default) against a
corpus of real captures. This auto-tracks every CURRENT call site without a
hand-maintained list, and will pick up future ones automatically. For every
recorded call still using the default occurrence="f", it re-runs the same
query with occurrence="a" and flags any field that comes back with more
than one comma-joined value in any row — a candidate this call site MIGHT
be silently truncating.

IMPORTANT — this is a candidate finder, not an auto-fixer, and a "flagged"
result does NOT always mean a bug. Comma-joined output can also come from
tshark conflating the SAME field name at different tree depths in one frame
(e.g. a real per-entry UUID vs. an unrelated top-level "attribute type" echo
field) — naively switching such a call to occurrence="a" would produce a
WRONG value, not just a fuller one (this is exactly what made the
--gatt-map fix require -T pdml tree-walking instead of a simple occurrence
flip — see docs/developer/nrf-ble-analyze-completeness-audit.md). Every
flagged candidate below needs the same kind of manual verification that
found the original two bugs — inspect with -T pdml, don't just flip
occurrence="a" and trust the result.

Usage:
    python tools/audit-nrf-ble-analyze-coverage.py [pcapng ...]
    python tools/audit-nrf-ble-analyze-coverage.py --corpus   # default: the
        three real captures already in local-assets/Bluetooth-Logs/ used for
        the 2026-07-18 audit
"""
import argparse
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_TOOL_PATH = Path(__file__).parent / "nrf-ble-analyze.py"

_DEFAULT_CORPUS = [
    _REPO_ROOT / "local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/onboarding-real-mera.pcapng",
    _REPO_ROOT / "local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/firmware-update-vom-mac.pcapng",
    _REPO_ROOT / "local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/firmware-update-von-windows.pcapng",
]

# CLI arg combinations to drive the tool's main code paths. Each is run
# independently — one mode's failure/SystemExit doesn't block the others.
_DRIVE_MODES = [
    [],                    # default analysis path (_analyze_mera / _analyze_alba)
    ["--markdown"],        # markdown rendering path (Mera only, but exercises most helpers)
    ["--gatt-map"],        # _extract_gatt_handles (now PDML-based, no occurrence="f" calls)
    ["--adv"],             # _get_adv_packets
]


def _load_tool():
    spec = importlib.util.spec_from_file_location("nba", _TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _audit_one(nba, tshark: str, pcapng: Path) -> list:
    """Drive the tool against one capture, recording every _run_tshark call,
    then re-check every occurrence="f" call with occurrence="a". Returns a
    list of (display_filter, fields, occurrence, example_row) candidates."""
    calls_seen: set = set()
    real_run_tshark = nba._run_tshark

    def _recording_run_tshark(tshark_bin, pcap, display_filter, fields, occurrence="f"):
        calls_seen.add((display_filter, tuple(fields), occurrence))
        return real_run_tshark(tshark_bin, pcap, display_filter, fields, occurrence=occurrence)

    nba._run_tshark = _recording_run_tshark
    try:
        for mode_args in _DRIVE_MODES:
            argv_backup = sys.argv
            sys.argv = ["nrf-ble-analyze.py", str(pcapng)] + mode_args
            try:
                nba.main()
            except SystemExit:
                pass
            except Exception as e:
                print(f"    (mode {mode_args or ['(default)']} raised: {e} — partial coverage for this mode)",
                      file=sys.stderr)
            finally:
                sys.argv = argv_backup
    finally:
        nba._run_tshark = real_run_tshark

    findings = []
    for display_filter, fields, occurrence in sorted(calls_seen, key=lambda t: t[0]):
        if occurrence != "f":
            continue  # already using occurrence="a" (or something else) — not a candidate
        rows_a = real_run_tshark(tshark, pcapng, display_filter, list(fields), occurrence="a")
        for row in rows_a:
            multi_fields = [fields[i] for i, cell in enumerate(row) if i < len(fields) and "," in cell]
            if multi_fields:
                findings.append((display_filter, fields, multi_fields, row))
                break
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcapng", nargs="*", type=Path,
                     help="capture(s) to audit (default: the standing corpus below)")
    args = ap.parse_args()

    corpus = args.pcapng or _DEFAULT_CORPUS
    missing = [p for p in corpus if not p.exists()]
    if missing:
        for p in missing:
            print(f"[!] not found, skipping: {p}", file=sys.stderr)
    corpus = [p for p in corpus if p.exists()]
    if not corpus:
        print("[!] no capture files to audit.", file=sys.stderr)
        sys.exit(1)

    nba = _load_tool()
    tshark = nba._find_tshark()

    total_findings = 0
    for pcapng in corpus:
        print(f"\n=== {pcapng.name} ===")
        findings = _audit_one(nba, tshark, pcapng)
        if not findings:
            print("  no candidates — every occurrence=\"f\" call site returned single-valued "
                  "fields for every matching frame in this capture.")
            continue
        for display_filter, fields, multi_fields, example_row in findings:
            total_findings += 1
            print(f"  CANDIDATE: filter={display_filter!r}")
            print(f"    fields={fields}")
            print(f"    multi-valued: {multi_fields}")
            print(f"    example row (occurrence=\"a\"): {example_row}")

    print(f"\n{total_findings} candidate(s) across {len(corpus)} capture(s). "
          f"Each needs manual -T pdml verification before assuming it's a real bug "
          f"or a safe occurrence=\"a\" fix — see this script's module docstring.")
    sys.exit(1 if total_findings else 0)


if __name__ == "__main__":
    main()
