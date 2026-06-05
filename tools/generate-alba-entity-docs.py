#!/usr/bin/env python3
"""Generate docs/developer/alba-entity-reference.md from annotated ALBA entity lists.

Each entity tuple in the four HACS entity files carries a trailing  # DpId N  comment
(machine-readable).  This script extracts those annotations and writes the reference table.

Run after any change to ALBA_SENSORS, ALBA_BINARY_SENSORS, ALBA_ACTIVE_NUMBERS,
ALBA_COMMAND_BUTTONS, or the _ALBA_ONLY entries in BUTTONS.

Usage:
    /Users/jens/venv/bin/python tools/generate-alba-entity-docs.py
"""

import re
from pathlib import Path

ROOT   = Path(__file__).resolve().parent.parent
CUSTOM = ROOT / "custom_components" / "geberit_aquaclean"
OUT    = ROOT / "docs" / "developer" / "alba-entity-reference.md"

# ── helpers ──────────────────────────────────────────────────────────────────

def _extract_block(src: str, list_name: str) -> str:
    """Return the content between the opening [ and closing ] of list_name = [...]."""
    # Match list_name followed by = [ (skipping the type annotation list[...])
    m = re.search(rf'\b{re.escape(list_name)}\b[^=]*=\s*\[', src)
    if not m:
        return ""
    depth, pos = 1, m.end()
    while pos < len(src) and depth:
        if src[pos] == '[':
            depth += 1
        elif src[pos] == ']':
            depth -= 1
        pos += 1
    return src[m.end():pos - 1]


# Matches a tuple line and captures:
#   group 1 — first quoted string  (entity key or command)
#   group 2 — last quoted string before the closing ) that is NOT an mdi/geberit icon
#             (i.e. the friendly name)
#   group 3 — trailing DpId annotation (may be absent)
_LINE = re.compile(
    r'\(\s*"([^"]+)"'           # first string: key / command
    r'(?:.*?"([^"]+)")?'        # last non-icon string: friendly name (greedy inner)
    r'[^)]*'
    r'(?:#\s*(DpId[^)#\n]*))?'  # optional # DpId ... annotation
    r'\s*\)'
)

# Simpler pattern to extract the friendly name: last string before the closing )
# that does not start with mdi: or geberit:
_NAME = re.compile(r'"(?!mdi:|geberit:)([^"]{3,})"')


def _parse_block(block: str, domain: str, name_field: int = 1) -> list[dict]:
    """Extract rows from a list block.  name_field=1 for (key, name, ...) tuples,
    name_field=2 for (command, value, name, ...) tuples."""
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # extract all quoted strings on this line (excluding icon prefixes)
        strings = _NAME.findall(line)
        if len(strings) < 2:
            continue
        key  = strings[0]
        name = strings[name_field] if len(strings) > name_field else strings[-1]
        # extract trailing DpId comment
        dpid_m = re.search(r'#\s*((?:r:)?DpId[^\n]*)', line)
        dpid = dpid_m.group(1).strip() if dpid_m else "—"
        rows.append({"key": key, "name": name, "domain": domain, "dpid": dpid})
    return rows


def _entity_id(key: str, domain: str) -> str:
    return f"{domain}.geberit_aquaclean_{key}"


# ── parse ─────────────────────────────────────────────────────────────────────

def collect_all() -> list[dict]:
    rows: list[dict] = []

    # sensor.py — ALBA_SENSORS
    src = (CUSTOM / "sensor.py").read_text()
    rows += _parse_block(_extract_block(src, "ALBA_SENSORS"), "sensor")

    # binary_sensor.py — ALBA_BINARY_SENSORS
    src = (CUSTOM / "binary_sensor.py").read_text()
    rows += _parse_block(_extract_block(src, "ALBA_BINARY_SENSORS"), "binary_sensor")

    # number.py — ALBA_ACTIVE_NUMBERS  (key, command, name, …) → name_field=2
    src = (CUSTOM / "number.py").read_text()
    rows += _parse_block(_extract_block(src, "ALBA_ACTIVE_NUMBERS"), "number", name_field=2)

    # button.py — _ALBA_ONLY entries inside BUTTONS (key == command, name_field=1)
    src = (CUSTOM / "button.py").read_text()
    # Alba-only single-command buttons
    rows += _parse_block(_extract_block(src, "BUTTONS"), "button",
                         name_field=1)
    # Filter: only keep rows that have a DpId annotation (i.e. the _ALBA_ONLY ones)
    rows = [r for r in rows if r["dpid"] != "—" or r["domain"] != "button"
            or r["key"].startswith("alba_")]

    # button.py — ALBA_COMMAND_BUTTONS (command, value, name, …) → name_field=2
    rows += _parse_block(_extract_block(src, "ALBA_COMMAND_BUTTONS"), "button", name_field=2)

    # De-duplicate by (key, domain, name) — allows start/stop variants of same command;
    # removes true duplicates where the same entity appears in multiple passes.
    seen: set[tuple] = set()
    unique = []
    for r in rows:
        sig = (r["key"], r["domain"], r["name"])
        if sig not in seen:
            seen.add(sig)
            unique.append(r)
    return unique


# ── render ────────────────────────────────────────────────────────────────────

_DOMAIN_ORDER = ["sensor", "binary_sensor", "number", "button"]
_DOMAIN_LABEL = {
    "sensor":        "Sensors (`sensor.*`)",
    "binary_sensor": "Binary Sensors (`binary_sensor.*`)",
    "number":        "Numbers (`number.*`)",
    "button":        "Buttons (`button.*`)",
}


def render(rows: list[dict]) -> str:
    by_domain: dict[str, list[dict]] = {d: [] for d in _DOMAIN_ORDER}
    for r in rows:
        by_domain.setdefault(r["domain"], []).append(r)

    lines = [
        "# Alba HACS Entity Reference",
        "",
        "**Auto-generated** — do not edit by hand.",
        "Run `tools/generate-alba-entity-docs.py` after any change to the ALBA entity lists.",
        "",
        "Entity IDs assume the default integration name `Geberit AquaClean`.",
        "All entities are unavailable on Mera Comfort devices.",
        "",
        "DpId notation: `r:N` = read DpId, `w:N` = write DpId, `inst=N` = instance index.",
        "",
    ]

    for domain in _DOMAIN_ORDER:
        domain_rows = by_domain.get(domain, [])
        if not domain_rows:
            continue
        lines += [
            f"## {_DOMAIN_LABEL[domain]}",
            "",
            "| Entity ID | Friendly Name | DpId |",
            "|-----------|--------------|------|",
        ]
        for r in domain_rows:
            eid = _entity_id(r["key"], r["domain"])
            lines.append(f"| `{eid}` | {r['name']} | {r['dpid']} |")
        lines.append("")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    rows = collect_all()
    doc  = render(rows)
    OUT.write_text(doc)
    print(f"Written {len(rows)} entities to {OUT.relative_to(ROOT)}")
