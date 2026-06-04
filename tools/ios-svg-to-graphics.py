#!/usr/bin/env python3
"""
ios-svg-to-graphics.py — Convert Geberit Home iOS app SVGs to graphics/ format
================================================================================

The SVGs in local-assets/geberit-home-v2.14.1-from-iOS/images/ are produced by
potrace with a coordinate transform:

    <g transform="translate(0,72) scale(0.1,-0.1)" fill="#000000" stroke="none">

This script normalises the path data, removes the transform, and writes a clean
SVG suitable for graphics/ and geberit-aquaclean-icons.js.

Normalisation rules (for transform translate(0,H) scale(S,-S), H=72, S=0.1):
  - Absolute coordinates:  x' = x          (unchanged)
                            y' = (H/S) - y  = 720 - y
  - Relative coordinates:  dx' = dx         (unchanged)
                            dy' = -dy        (negate)
  - Output viewBox: "0 0 720 720"

The output SVG uses fill="currentColor" for HA theme integration.

Usage
-----
  # Convert a single file, write to stdout:
  python tools/ios-svg-to-graphics.py images/odourextraction_Normal.svg

  # Convert and save to graphics/:
  python tools/ios-svg-to-graphics.py images/odourextraction_Normal.svg -o graphics/odourextraction.svg

  # Batch-convert all *_Normal.svg files, strip "_Normal" from names:
  python tools/ios-svg-to-graphics.py images/ -o graphics/

  # Preview: show the extracted icon name and path length only:
  python tools/ios-svg-to-graphics.py images/odourextraction_Normal.svg --info
"""

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# SVG path normaliser
# ---------------------------------------------------------------------------

def _num(s: str) -> float:
    return float(s)


def _fmt(v: float) -> str:
    """Format a number: drop trailing .0 for integers, 4 sig figs otherwise."""
    if v == int(v):
        return str(int(v))
    return f"{v:.4g}"


def _normalise_path(d: str, canvas_height: float = 720.0) -> str:
    """
    Apply the inverse of translate(0, H/10) scale(0.1, -0.1) to all coordinates
    in an SVG path data string.

    Effective mapping:
      absolute (x, y)  → (x, canvas_height - y)
      relative (dx,dy) → (dx, -dy)

    Handles all standard path commands: M m L l H h V v C c S s Q q T t A a Z z.
    """
    # Tokenise: split into command letters and numeric tokens
    tokens = re.findall(r'[MmLlHhVvCcSsSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)

    out = []
    cmd = None
    idx = 0

    while idx < len(tokens):
        t = tokens[idx]

        if t.isalpha():
            cmd = t
            out.append(cmd)
            idx += 1
            continue

        # Numeric token — interpret according to current command
        def take(n=1):
            nonlocal idx
            vals = [_num(tokens[idx + i]) for i in range(n)]
            idx += n
            return vals[0] if n == 1 else vals

        if cmd in ('M', 'L'):       # absolute moveto / lineto: x y
            x, y = take(2)
            out.append(f"{_fmt(x)},{_fmt(canvas_height - y)}")
            cmd = 'L' if cmd == 'M' else cmd
        elif cmd in ('m', 'l'):     # relative: dx dy
            dx, dy = take(2)
            out.append(f"{_fmt(dx)},{_fmt(-dy)}")
            cmd = 'l' if cmd == 'm' else cmd
        elif cmd == 'H':            # absolute horizontal: x
            x = take()
            out.append(_fmt(x))
        elif cmd == 'h':            # relative horizontal: dx (unchanged)
            dx = take()
            out.append(_fmt(dx))
        elif cmd == 'V':            # absolute vertical: y → canvas_height - y
            y = take()
            out.append(_fmt(canvas_height - y))
        elif cmd == 'v':            # relative vertical: dy → -dy
            dy = take()
            out.append(_fmt(-dy))
        elif cmd in ('C', 'S'):     # absolute cubic bezier: 6 or 4 coords
            n = 6 if cmd == 'C' else 4
            coords = take(n)
            pairs = [(coords[i], coords[i+1]) for i in range(0, n, 2)]
            out.append(' '.join(f"{_fmt(x)},{_fmt(canvas_height - y)}" for x, y in pairs))
        elif cmd in ('c', 's'):     # relative cubic bezier
            n = 6 if cmd == 'c' else 4
            coords = take(n)
            pairs = [(coords[i], coords[i+1]) for i in range(0, n, 2)]
            out.append(' '.join(f"{_fmt(dx)},{_fmt(-dy)}" for dx, dy in pairs))
        elif cmd in ('Q', 'T'):     # absolute quadratic bezier: 4 or 2 coords
            n = 4 if cmd == 'Q' else 2
            coords = take(n)
            pairs = [(coords[i], coords[i+1]) for i in range(0, n, 2)]
            out.append(' '.join(f"{_fmt(x)},{_fmt(canvas_height - y)}" for x, y in pairs))
        elif cmd in ('q', 't'):     # relative quadratic
            n = 4 if cmd == 'q' else 2
            coords = take(n)
            pairs = [(coords[i], coords[i+1]) for i in range(0, n, 2)]
            out.append(' '.join(f"{_fmt(dx)},{_fmt(-dy)}" for dx, dy in pairs))
        elif cmd in ('A', 'a'):     # arc: rx ry x-rot large-arc sweep x y
            rx, ry, xrot, large, sweep, ex, ey = take(7)
            if cmd == 'A':
                out.append(f"{_fmt(rx)},{_fmt(ry)},{_fmt(xrot)},{int(large)},{int(sweep)},{_fmt(ex)},{_fmt(canvas_height - ey)}")
            else:
                out.append(f"{_fmt(rx)},{_fmt(ry)},{_fmt(xrot)},{int(large)},{1-int(sweep)},{_fmt(ex)},{_fmt(-ey)}")
        elif cmd in ('Z', 'z'):
            out.append('z')
            idx += 0  # Z has no coords, token already advanced above
        else:
            # Unknown — pass through as-is
            out.append(t)
            idx += 1
            continue

    return ' '.join(out)


# ---------------------------------------------------------------------------
# SVG parser / converter
# ---------------------------------------------------------------------------

def _detect_transform(svg_text: str):
    """
    Detect and return (translate_y, scale) from a potrace <g transform>.
    Returns (72.0, 0.1) for the standard Geberit iOS format, or None if not found.
    """
    m = re.search(
        r'<g[^>]+transform=["\']translate\(\s*[\d.]+\s*,\s*([\d.]+)\s*\)\s*scale\(\s*([\d.]+)\s*,\s*-([\d.]+)\s*\)',
        svg_text
    )
    if m:
        H = float(m.group(1))
        Sx = float(m.group(2))
        Sy = float(m.group(3))
        if abs(Sx - Sy) < 1e-6:  # symmetric scale
            return H, Sx
    return None


def convert_svg(svg_text: str) -> str:
    """
    Convert a Geberit iOS potrace SVG to a normalised graphics/ SVG.
    Returns the converted SVG string.
    Raises ValueError if the input doesn't look like a convertible potrace SVG.
    """
    transform_info = _detect_transform(svg_text)
    if not transform_info:
        raise ValueError("No recognised potrace transform found in SVG")

    H, S = transform_info
    canvas = H / S  # e.g. 72 / 0.1 = 720

    # Extract all path d attributes
    paths = re.findall(r'<path\b[^>]+\bd="([^"]+)"', svg_text)
    if not paths:
        raise ValueError("No <path d=\"...\"> elements found in SVG")

    normalised = [_normalise_path(p, canvas) for p in paths]
    combined = " ".join(normalised)

    viewbox = f"0 0 {_fmt(canvas)} {_fmt(canvas)}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">\n'
        f'<path fill="currentColor" d="{combined}"/>\n'
        f'</svg>\n'
    )


def output_name(src: Path) -> str:
    """Strip _Normal suffix and return a clean stem."""
    name = src.stem
    name = re.sub(r'_Normal$', '', name, flags=re.IGNORECASE)
    return name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Convert Geberit iOS potrace SVGs to normalised graphics/ format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("input", help="Source SVG file or directory of SVG files")
    ap.add_argument("-o", "--output", metavar="PATH",
                    help="Output file (if input is a file) or directory (if input is a dir). "
                         "Default: print to stdout (single file) or write alongside input (dir).")
    ap.add_argument("--info", action="store_true",
                    help="Print icon name and path length only — no output written")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing output files without prompting")
    args = ap.parse_args()

    src = Path(args.input)

    if src.is_file():
        files = [src]
    elif src.is_dir():
        files = sorted(src.glob("*.svg"))
        if not files:
            print(f"No SVG files found in {src}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Error: {src} does not exist", file=sys.stderr)
        sys.exit(1)

    for f in files:
        try:
            svg_text = f.read_text(encoding="utf-8")
            converted = convert_svg(svg_text)
        except ValueError as e:
            if len(files) > 1:
                print(f"  SKIP {f.name}: {e}", file=sys.stderr)
                continue
            else:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

        name = output_name(f)

        if args.info:
            path_match = re.search(r'd="([^"]+)"', converted)
            plen = len(path_match.group(1)) if path_match else 0
            print(f"{name:<40}  {plen} chars")
            continue

        if args.output:
            out_path = Path(args.output)
            if out_path.is_dir() or (not out_path.suffix and len(files) > 1):
                out_path.mkdir(parents=True, exist_ok=True)
                dest = out_path / f"{name}.svg"
            else:
                dest = out_path
        else:
            if len(files) == 1:
                print(converted, end="")
                continue
            else:
                dest = f.parent / f"{name}.svg"

        if dest.exists() and not args.force:
            ans = input(f"Overwrite {dest}? [y/N] ").strip().lower()
            if ans != "y":
                print(f"  skipped {dest.name}")
                continue

        dest.write_text(converted, encoding="utf-8")
        print(f"  wrote {dest}")


if __name__ == "__main__":
    main()
