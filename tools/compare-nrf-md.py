#!/usr/bin/env python3
"""
Compare two nrf-ble-analyze.py markdown capture files, ignoring timestamps.

Timestamps (HH:MM:SS.mmm, t=XX.Xs) are stripped before comparison so that
captures at different absolute times can be diffed by protocol content only.

--from / --to filter each file by the HH:MM:SS clock times present in that file.
A filter is only applied to a file whose timestamps share the same clock-hour as
the filter value; a file with timestamps in a different clock-hour is left unfiltered.
This lets you write --to 04:32:29 to slice the mock capture (04:xx) while the
real-device capture (14:xx) is shown in full.

Examples:
    compare-nrf-md.py mock.md real.md
    compare-nrf-md.py mock.md real.md --to 04:32:29
    compare-nrf-md.py mock.md real.md --from 04:30:00 --to 04:32:29
    compare-nrf-md.py mock.md real.md --to 04:32:29 --context 5
"""

import argparse
import difflib
import re
import sys
from pathlib import Path


# ── timestamp helpers ─────────────────────────────────────────────────────────

_RE_TICK_TS  = re.compile(r'`(\d{2}:\d{2}:\d{2}\.\d{3})`')   # `HH:MM:SS.mmm`
_RE_BARE_TS  = re.compile(r'\b(\d{2}:\d{2}:\d{2}\.\d{3})\b')  # HH:MM:SS.mmm
_RE_REL_TS   = re.compile(r'\bt=\d+\.?\d*s\b')                 # t=XX.Xs


def _parse_hms(s: str, end: bool = False):
    """
    Parse 'HH:MM:SS' or 'HH:MM:SS.mmm' to a comparable tuple (h, m, s, ms).
    When end=True and no milliseconds are given, extends to .999 so that
    --to 04:32:29 includes all frames within that second.
    Returns None on parse failure.
    """
    s = s.strip()
    has_ms = bool(re.search(r'\.\d+$', s))
    if end and not has_ms:
        s += '.999'
    m = re.fullmatch(r'(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?', s)
    if not m:
        return None
    ms_str = m.group(4) or '0'
    ms = int(ms_str[:3].ljust(3, '0'))   # normalise to milliseconds
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), ms)


def _line_ts(line: str):
    """Return the first absolute timestamp tuple from a line, or None."""
    m = _RE_TICK_TS.search(line) or _RE_BARE_TS.search(line)
    return _parse_hms(m.group(1)) if m else None


def _strip_ts(line: str) -> str:
    """Remove all timestamps from a line so only protocol content remains."""
    line = _RE_TICK_TS.sub('`__TS__`', line)
    line = _RE_BARE_TS.sub('__TS__', line)
    line = _RE_REL_TS.sub('t=__REL__', line)
    return line


# ── file loading & filtering ──────────────────────────────────────────────────

def _load(path: Path) -> list:
    return path.read_text(encoding='utf-8').splitlines()


def _filter(lines: list, from_t, to_t) -> list:
    """
    Filter lines to the [from_t, to_t] clock range.
    Only applied when this file has timestamps in the same clock-hour as
    the filter; otherwise the file is returned unchanged.
    """
    if from_t is None and to_t is None:
        return lines

    ref_hour = (to_t or from_t)[0]

    # Check whether any line in this file has a timestamp in the filter's hour
    file_hours = {ts[0] for l in lines if (ts := _line_ts(l)) is not None}
    if ref_hour not in file_hours:
        return lines   # different clock range — don't filter this file

    result = []
    include = (from_t is None)   # start included only when there's no --from
    for line in lines:
        ts = _line_ts(line)
        if ts is not None:
            include = (
                (from_t is None or ts >= from_t) and
                (to_t   is None or ts <= to_t)
            )
        # Non-timestamp lines inherit the current include state
        if include:
            result.append(line)
    return result


# ── diff ──────────────────────────────────────────────────────────────────────

def _diff(lines1, lines2, name1, name2, context=2):
    """
    Diff stripped content, emit original lines with < / > / (space) prefix.
    Equal blocks longer than 2*context lines are collapsed with a skip notice.
    """
    stripped1 = [_strip_ts(l) for l in lines1]
    stripped2 = [_strip_ts(l) for l in lines2]

    sm = difflib.SequenceMatcher(None, stripped1, stripped2, autojunk=False)
    opcodes = sm.get_opcodes()

    # Determine whether there are any differences at all
    has_diff = any(tag != 'equal' for tag, *_ in opcodes)

    out = []
    out.append(f"--- {name1}  ({len(lines1)} lines after filter)")
    out.append(f"+++ {name2}  ({len(lines2)} lines after filter)")
    if not has_diff:
        out.append("")
        out.append("(no differences — files are identical after stripping timestamps)")
        return out
    out.append("")

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal':
            block = lines1[i1:i2]
            if len(block) <= 2 * context:
                for l in block:
                    out.append(f"  {l}")
            else:
                for l in block[:context]:
                    out.append(f"  {l}")
                skipped = len(block) - 2 * context
                out.append(f"  ... {skipped} identical line{'s' if skipped != 1 else ''} ...")
                for l in block[-context:]:
                    out.append(f"  {l}")
        elif tag == 'delete':
            for l in lines1[i1:i2]:
                out.append(f"< {l}")
        elif tag == 'insert':
            for l in lines2[j1:j2]:
                out.append(f"> {l}")
        elif tag == 'replace':
            for l in lines1[i1:i2]:
                out.append(f"< {l}")
            out.append("  ---")
            for l in lines2[j1:j2]:
                out.append(f"> {l}")

    return out


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file1", type=Path, metavar="FILE1",
                    help="First markdown (e.g. mock capture)")
    ap.add_argument("file2", type=Path, metavar="FILE2",
                    help="Second markdown (e.g. real device capture)")
    ap.add_argument("--from", dest="from_t", metavar="HH:MM:SS",
                    help="Include only content at or after this time")
    ap.add_argument("--to", dest="to_t", metavar="HH:MM:SS",
                    help="Include only content at or before this time (inclusive of the second)")
    ap.add_argument("--context", type=int, default=2, metavar="N",
                    help="Lines of context around each difference (default: 2)")
    args = ap.parse_args()

    from_t = _parse_hms(args.from_t, end=False) if args.from_t else None
    to_t   = _parse_hms(args.to_t,   end=True)  if args.to_t   else None

    if args.from_t and from_t is None:
        print(f"ERROR: could not parse --from value: {args.from_t!r}", file=sys.stderr)
        sys.exit(1)
    if args.to_t and to_t is None:
        print(f"ERROR: could not parse --to value: {args.to_t!r}", file=sys.stderr)
        sys.exit(1)

    lines1 = _filter(_load(args.file1), from_t, to_t)
    lines2 = _filter(_load(args.file2), from_t, to_t)

    output = _diff(lines1, lines2, args.file1.name, args.file2.name, args.context)
    print("\n".join(output))


if __name__ == "__main__":
    main()
