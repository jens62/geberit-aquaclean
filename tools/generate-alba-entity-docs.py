#!/usr/bin/env python3
"""Generate docs/developer/alba-entity-reference.md — complete HACS entity reference.

Covers every entity registered by the geberit_aquaclean custom component:
  - List-based entities parsed directly from the source files
  - Class-based entities (performance stats, connection sensors) hardcoded here
  - Availability column: All / Alba only / Mera only / ESPHome only

Run after any change to entity lists in sensor/binary_sensor/number/button.py.

Usage:
    /Users/jens/venv/bin/python tools/generate-alba-entity-docs.py
"""

import re
from pathlib import Path

ROOT   = Path(__file__).resolve().parent.parent
CUSTOM = ROOT / "custom_components" / "geberit_aquaclean"
OUT    = ROOT / "docs" / "developer" / "alba-entity-reference.md"

# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_block(src: str, name: str) -> str:
    """Return content of  name = [...]  (handles type annotations like list[tuple])."""
    m = re.search(rf'\b{re.escape(name)}\b[^=]*=\s*\[', src)
    if not m:
        return ""
    depth, pos = 1, m.end()
    while pos < len(src) and depth:
        if src[pos] == '[':   depth += 1
        elif src[pos] == ']': depth -= 1
        pos += 1
    return src[m.end():pos - 1]


def _extract_set(src: str, name: str) -> set[str]:
    """Return string members of  name = {"a", "b", ...}  or  name = {\\n  "a",\\n}."""
    m = re.search(rf'\b{re.escape(name)}\b\s*=\s*\{{', src)
    if not m:
        return set()
    depth, pos = 1, m.end()
    while pos < len(src) and depth:
        if src[pos] == '{':   depth += 1
        elif src[pos] == '}': depth -= 1
        pos += 1
    block = src[m.end():pos - 1]
    return set(re.findall(r'"([^"]+)"', block))


_ICON_PREFIX = re.compile(r'^(?:mdi|geberit):')
_NAME_RE     = re.compile(r'"([^"]*)"')


def _strings(line: str) -> list[str]:
    """All quoted strings on a line that are not icon references."""
    return [s for s in _NAME_RE.findall(line) if not _ICON_PREFIX.match(s)]


def _dpid(line: str) -> str:
    m = re.search(r'#\s*((?:r:)?DpId[^\n]*)', line)
    return m.group(1).strip() if m else "—"


def _entity_id(domain: str, uid_suffix: str) -> str:
    """Derive HA entity_id from domain and unique_id suffix for AquaCleanEntity."""
    return f"{domain}.geberit_aquaclean_{uid_suffix}"


def _proxy_entity_id(domain: str, uid_suffix: str) -> str:
    """Derive HA entity_id for AquaCleanProxyEntity (device: AquaClean Proxy)."""
    return f"{domain}.aquaclean_proxy_{uid_suffix}"


# ── per-list extractors ───────────────────────────────────────────────────────

def from_sensors(src: str, list_name: str, availability: str,
                 name_field: int = 1) -> list[dict]:
    """(key, [cmd,] name, ...) lists — no availability field in tuple."""
    rows = []
    for line in _extract_block(src, list_name).splitlines():
        if not line.strip() or line.strip().startswith('#'):
            continue
        strs = _strings(line)
        if len(strs) < 2:
            continue
        key  = strs[0]
        name = strs[name_field] if len(strs) > name_field else strs[-1]
        rows.append({"entity_id": _entity_id("sensor", key),
                     "name": name, "availability": availability,
                     "dpid": _dpid(line)})
    return rows


def from_binary_sensors(src: str, list_name: str, availability: str,
                        has_mera_only_field: bool = False) -> list[dict]:
    """(key, name, device_class, icon_on, icon_off[, mera_only]) lists."""
    rows = []
    for line in _extract_block(src, list_name).splitlines():
        if not line.strip() or line.strip().startswith('#'):
            continue
        strs = _strings(line)
        if len(strs) < 2:
            continue
        key, name = strs[0], strs[1]
        avail = availability
        if has_mera_only_field:
            # last Python token on the line before closing ) is True/False
            if re.search(r'\bTrue\b', line):
                avail = "Mera only"
        rows.append({"entity_id": _entity_id("binary_sensor", key),
                     "name": name, "availability": avail, "dpid": _dpid(line)})
    return rows


def from_numbers(src: str, list_name: str, availability: str,
                 name_field: int = 1,
                 mera_only_setting_ids: frozenset = frozenset()) -> list[dict]:
    """(key[, setting_id], name, ...) lists — optional mera_only_setting_ids check."""
    rows = []
    for line in _extract_block(src, list_name).splitlines():
        if not line.strip() or line.strip().startswith('#'):
            continue
        strs = _strings(line)
        if len(strs) < 2:
            continue
        key  = strs[0]
        name = strs[name_field] if len(strs) > name_field else strs[-1]
        avail = availability
        if mera_only_setting_ids:
            ints = re.findall(r'(?<!["\w])(\d+)(?!["\w])', line)
            if ints and int(ints[0]) in mera_only_setting_ids:
                avail = "Mera only"
        rows.append({"entity_id": _entity_id("number", key),
                     "name": name, "availability": avail, "dpid": _dpid(line)})
    return rows


def from_buttons(src: str, list_name: str,
                 mera_only: set[str], alba_only: set[str],
                 name_field: int = 1,
                 override_availability: str | None = None) -> list[dict]:
    """(command[, value], name, icon) — availability from _MERA_ONLY / _ALBA_ONLY,
    or overridden when override_availability is given (e.g. ALBA_COMMAND_BUTTONS)."""
    rows = []
    for line in _extract_block(src, list_name).splitlines():
        if not line.strip() or line.strip().startswith('#'):
            continue
        strs = _strings(line)
        if len(strs) < 2:
            continue
        key  = strs[0]
        name = strs[name_field] if len(strs) > name_field else strs[-1]
        if override_availability:
            avail = override_availability
        elif key in mera_only:
            avail = "Mera only"
        elif key in alba_only:
            avail = "Alba only"
        else:
            avail = "All"
        rows.append({"entity_id": _entity_id("button", key),
                     "name": name, "availability": avail, "dpid": _dpid(line)})
    return rows


# ── hardcoded class-based entities ───────────────────────────────────────────
# These are not defined in any list; they're individual classes in sensor.py / binary_sensor.py.
# Update this table when those classes change.
# entity_id is derived from _attr_name via HA slugification:
#   AquaCleanEntity     → geberit_aquaclean_{slug}
#   AquaCleanProxyEntity → aquaclean_proxy_{slug}  (separate HA device)

HARDCODED: list[dict] = [
    # ── sensor.py class-based — AquaCleanEntity (main toilet device) ──────────
    {"entity_id": "sensor.geberit_aquaclean_ble_connection",   "name": "BLE Connection",    "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_last_connect",     "name": "Last Connect",      "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_last_poll_ms",     "name": "Last Poll ms",      "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_avg_connect",      "name": "Avg Connect",       "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_min_connect",      "name": "Min Connect",       "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_max_connect",      "name": "Max Connect",       "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_avg_poll",         "name": "Avg Poll",          "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_min_poll",         "name": "Min Poll",          "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_max_poll",         "name": "Max Poll",          "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_poll_samples",     "name": "Poll Samples",      "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_transport",        "name": "Transport",         "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_avg_ble_rssi",     "name": "Avg BLE RSSI",      "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_min_ble_rssi",     "name": "Min BLE RSSI",      "availability": "All",          "dpid": "—"},
    {"entity_id": "sensor.geberit_aquaclean_max_ble_rssi",     "name": "Max BLE RSSI",      "availability": "All",          "dpid": "—"},
    # ── sensor.py class-based — AquaCleanProxyEntity (ESPHome proxy device) ──
    {"entity_id": "sensor.aquaclean_proxy_connection",         "name": "Connection",        "availability": "ESPHome only", "dpid": "—"},
    {"entity_id": "sensor.aquaclean_proxy_wifi_signal",         "name": "WiFi Signal",       "availability": "ESPHome only", "dpid": "—"},
    {"entity_id": "sensor.aquaclean_proxy_free_heap",          "name": "Free Heap",         "availability": "ESPHome only", "dpid": "—"},
    {"entity_id": "sensor.aquaclean_proxy_max_free_block",     "name": "Max Free Block",    "availability": "ESPHome only", "dpid": "—"},
    {"entity_id": "sensor.aquaclean_proxy_avg_wifi_rssi",      "name": "Avg WiFi RSSI",     "availability": "ESPHome only", "dpid": "—"},
    {"entity_id": "sensor.aquaclean_proxy_min_wifi_rssi",      "name": "Min WiFi RSSI",     "availability": "ESPHome only", "dpid": "—"},
    {"entity_id": "sensor.aquaclean_proxy_max_wifi_rssi",      "name": "Max WiFi RSSI",     "availability": "ESPHome only", "dpid": "—"},
    # ── binary_sensor.py class-based ─────────────────────────────────────────
    {"entity_id": "binary_sensor.geberit_aquaclean_ble_connected",     "name": "BLE Connected",     "availability": "All",          "dpid": "—"},
    {"entity_id": "binary_sensor.aquaclean_proxy_connected",           "name": "Connected",         "availability": "ESPHome only", "dpid": "—"},
    # ── button.py class-based — AquaCleanProxyEntity ─────────────────────────
    {"entity_id": "button.aquaclean_proxy_restart_aquaclean_proxy",    "name": "Restart AquaClean Proxy", "availability": "ESPHome only", "dpid": "—"},
]

# CommonSetting IDs available only on Mera Comfort (IDs 4, 6, 7)
_COMMON_MERA_ONLY_IDS: frozenset[int] = frozenset({4, 6, 7})

# ── collect ───────────────────────────────────────────────────────────────────

def collect_all() -> list[dict]:
    sensor_src  = (CUSTOM / "sensor.py").read_text()
    bsensor_src = (CUSTOM / "binary_sensor.py").read_text()
    number_src  = (CUSTOM / "number.py").read_text()
    button_src  = (CUSTOM / "button.py").read_text()

    _MERA_ONLY_PROFILE_IDS = frozenset({0, 3, 5, 7, 8, 9, 13})
    mera_only_btn = _extract_set(button_src, "_MERA_ONLY")
    alba_only_btn = _extract_set(button_src, "_ALBA_ONLY")

    rows: list[dict] = []

    # sensor.py
    rows += from_sensors(sensor_src, "SENSORS",      "All")
    rows += from_sensors(sensor_src, "ALBA_SENSORS",  "Alba only")
    rows += HARDCODED

    # binary_sensor.py
    rows += from_binary_sensors(bsensor_src, "BINARY_SENSORS",      "All", has_mera_only_field=True)
    rows += from_binary_sensors(bsensor_src, "ALBA_BINARY_SENSORS",  "Alba only")

    # number.py
    rows += from_numbers(number_src, "PROFILE_NUMBERS", "All",
                         mera_only_setting_ids=_MERA_ONLY_PROFILE_IDS)
    rows += from_numbers(number_src, "COMMON_NUMBERS",  "All",
                         mera_only_setting_ids=_COMMON_MERA_ONLY_IDS)
    rows += from_numbers(number_src, "ALBA_ACTIVE_NUMBERS", "Alba only", name_field=2)

    # button.py
    rows += from_buttons(button_src, "BUTTONS",             mera_only_btn, alba_only_btn)
    rows += from_buttons(button_src, "ALBA_COMMAND_BUTTONS", mera_only_btn, alba_only_btn,
                         name_field=2, override_availability="Alba only")

    # de-duplicate by (entity_id, name) — allows start/stop variants of same command
    seen: set[tuple] = set()
    unique = []
    for r in rows:
        sig = (r["entity_id"], r["name"])
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
_AVAIL_BADGE = {
    "All":          "All",
    "Alba only":    "Alba only",
    "Mera only":    "Mera only",
    "ESPHome only": "ESPHome only",
}


def render(rows: list[dict]) -> str:
    by_domain: dict[str, list[dict]] = {d: [] for d in _DOMAIN_ORDER}
    for r in rows:
        domain = r["entity_id"].split(".")[0]
        by_domain.setdefault(domain, []).append(r)

    lines = [
        "# HACS Entity Reference",
        "",
        "**Auto-generated** — do not edit by hand.",
        "Run `tools/generate-alba-entity-docs.py` after any change to the entity lists.",
        "",
        "Entity IDs assume the default integration name `Geberit AquaClean`.",
        "ESPHome proxy entities use the `aquaclean_proxy_` prefix (separate HA device).",
        "",
        "**Availability:** All = Mera Comfort + Alba · Alba only · Mera only ·",
        "ESPHome only = only when an ESPHome BLE proxy is configured.",
        "",
        "**DpId** (Alba only): `r:N` = read, `w:N` = write, `inst=N` = instance index.",
        "",
    ]

    for domain in _DOMAIN_ORDER:
        domain_rows = by_domain.get(domain, [])
        if not domain_rows:
            continue
        lines += [
            f"## {_DOMAIN_LABEL[domain]}",
            "",
            "| Entity ID | Friendly Name | Availability | DpId (Alba) |",
            "|-----------|--------------|--------------|-------------|",
        ]
        for r in domain_rows:
            avail = _AVAIL_BADGE.get(r["availability"], r["availability"])
            dpid  = r["dpid"] if r["availability"] == "Alba only" else "—"
            lines.append(f"| `{r['entity_id']}` | {r['name']} | {avail} | {dpid} |")
        lines.append("")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    rows = collect_all()
    doc  = render(rows)
    OUT.write_text(doc)
    by_avail: dict[str, int] = {}
    for r in rows:
        by_avail[r["availability"]] = by_avail.get(r["availability"], 0) + 1
    print(f"Written {len(rows)} entities to {OUT.relative_to(ROOT)}")
    for avail, count in sorted(by_avail.items()):
        print(f"  {avail}: {count}")
