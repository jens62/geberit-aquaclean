"""
In-memory poll timing statistics for the AquaClean bridge.

Tracks min / max / average for three timing dimensions per BLE connection mode:
  - poll_ms        : duration of the GATT data request
  - ble_ms         : BLE connect time (on-demand: every cycle; persistent: reconnects only)
  - esphome_api_ms : ESP32 TCP connect time (same pattern as ble_ms; None for local-BLE path)

Stats are accumulated for the lifetime of the process. No persistence across restarts.
Stats per mode are kept independently — switching modes at runtime never resets either side.

Connect times (ble_ms / esphome_api_ms) are only counted when > 0, so persistent-mode
samples from polls that reuse an existing connection don't drag the average to zero.
"""

from __future__ import annotations
from typing import Optional


class _MetricStats:
    """Running min / max / sum / count for one timing metric."""

    __slots__ = ("count", "_total", "_min", "_max")

    def __init__(self):
        self.count: int = 0
        self._total: float = 0.0
        self._min: Optional[float] = None
        self._max: Optional[float] = None

    def record(self, value_ms) -> None:
        if value_ms is None:
            return
        v = float(value_ms)
        self.count += 1
        self._total += v
        if self._min is None or v < self._min:
            self._min = v
        if self._max is None or v > self._max:
            self._max = v

    @property
    def avg_ms(self) -> Optional[float]:
        return round(self._total / self.count, 1) if self.count else None

    @property
    def min_ms(self) -> Optional[float]:
        return round(self._min, 1) if self._min is not None else None

    @property
    def max_ms(self) -> Optional[float]:
        return round(self._max, 1) if self._max is not None else None

    def to_dict(self) -> dict:
        return {
            "count":  self.count,
            "min_ms": self.min_ms,
            "avg_ms": self.avg_ms,
            "max_ms": self.max_ms,
        }


class _ModeStats:
    """Stats for one connection mode (persistent or on-demand)."""

    TRANSPORTS = ("bleak", "esp32-wifi", "esp32-eth")

    def __init__(self):
        self.poll:        _MetricStats = _MetricStats()
        self.ble:         _MetricStats = _MetricStats()
        self.esphome_api: _MetricStats = _MetricStats()
        self.ble_rssi:    _MetricStats = _MetricStats()   # BLE signal: ESP32 ↔ toilet (dBm)
        self.wifi_rssi:   _MetricStats = _MetricStats()   # WiFi signal: ESP32 ↔ router (dBm)
        self._transport_counts: dict[str, int] = {t: 0 for t in self.TRANSPORTS}

    @property
    def sample_count(self) -> int:
        return self.poll.count

    def record(self, esphome_api_ms, ble_ms, poll_ms, ble_rssi=None, wifi_rssi=None, transport=None) -> None:
        self.poll.record(poll_ms)
        # Only count connect times when a real connection was established (value > 0)
        if ble_ms is not None and float(ble_ms) > 0:
            self.ble.record(ble_ms)
        if esphome_api_ms is not None and float(esphome_api_ms) > 0:
            self.esphome_api.record(esphome_api_ms)
        # RSSI: record every sample (instantaneous signal strength per poll)
        if ble_rssi is not None:
            self.ble_rssi.record(ble_rssi)
        if wifi_rssi is not None:
            self.wifi_rssi.record(wifi_rssi)
        if transport in self._transport_counts:
            self._transport_counts[transport] += 1

    def to_dict(self) -> dict:
        return {
            "sample_count":    self.sample_count,
            "transport":       self._transport_counts,
            "poll_ms":         self.poll.to_dict(),
            "ble_ms":          self.ble.to_dict(),
            "esphome_api_ms":  self.esphome_api.to_dict(),
            "ble_rssi_dbm":    self.ble_rssi.to_dict(),
            "wifi_rssi_dbm":   self.wifi_rssi.to_dict(),
        }

    def to_markdown_rows(self) -> list[str]:
        def _f(v):
            return f"{v} ms" if v is not None else "—"

        def _dbm(v):
            return f"{v} dBm" if v is not None else "—"

        rows = []
        for label, m, fmt in [
            ("Poll (query)",  self.poll,        _f),
            ("BLE connect",   self.ble,         _f),
            ("ESP32 connect", self.esphome_api, _f),
            ("BLE RSSI",      self.ble_rssi,    _dbm),
            ("WiFi RSSI",     self.wifi_rssi,   _dbm),
        ]:
            rows.append(
                f"| {label:<16} | {fmt(m.min_ms):>12} | {fmt(m.avg_ms):>12} | {fmt(m.max_ms):>12} | {m.count:>7} |"
            )
        return rows


class PollStats:
    """
    In-memory performance statistics accumulated per BLE connection mode.
    Safe for single-threaded asyncio use. Never raises.
    """

    MODES = ("persistent", "on-demand")

    def __init__(self):
        self._modes: dict[str, _ModeStats] = {m: _ModeStats() for m in self.MODES}

    def record(self, mode: str, esphome_api_ms, ble_ms, poll_ms, ble_rssi=None, wifi_rssi=None, transport=None) -> None:
        """Record one completed poll cycle's timings and signal strengths for the given connection mode.

        transport: "bleak" | "esp32-wifi" | "esp32-eth"
          bleak     — local BLE adapter on the bridge host
          esp32-wifi — ESP32 proxy reachable via WiFi (wifi_rssi present)
          esp32-eth  — ESP32 proxy reachable via Ethernet (no wifi_rssi)
        """
        try:
            stats = self._modes.get(mode)
            if stats:
                stats.record(esphome_api_ms, ble_ms, poll_ms, ble_rssi=ble_rssi, wifi_rssi=wifi_rssi, transport=transport)
        except Exception:
            pass

    def to_dict(self) -> dict:
        """Return full stats as a JSON-serialisable dict."""
        return {mode: stats.to_dict() for mode, stats in self._modes.items()}

    def to_markdown(self) -> str:
        """Return stats formatted as a Markdown table."""
        lines = [
            "## AquaClean Performance Statistics",
            "",
            "> Connect times (BLE, ESP32) are only counted when a new connection was established.",
            "> In **persistent** mode this means the first poll after each reconnect.",
            "> In **on-demand** mode every poll cycle includes a full connect.",
            "> RSSI values are recorded every poll (instantaneous signal strength at scan time).",
            "> Transport: bleak = local BLE adapter; esp32-wifi = ESP32 via WiFi; esp32-eth = ESP32 via Ethernet.",
            "",
        ]
        for mode, stats in self._modes.items():
            n = stats.sample_count
            lines.append(f"### Mode: {mode} ({n} sample{'s' if n != 1 else ''})")
            lines.append("")
            if n == 0:
                lines.append("*No data collected yet.*")
                lines.append("")
                continue
            # Transport breakdown
            tc = stats._transport_counts
            transport_str = "  ".join(f"{t}: {tc[t]}" for t in _ModeStats.TRANSPORTS if tc[t] > 0)
            if transport_str:
                lines.append(f"Transport: {transport_str}")
                lines.append("")
            lines.append(f"| {'Metric':<16} | {'Min':>12} | {'Avg':>12} | {'Max':>12} | {'Samples':>7} |")
            lines.append(f"|{'-'*18}|{'-'*14}|{'-'*14}|{'-'*14}|{'-'*9}|")
            lines.extend(stats.to_markdown_rows())
            lines.append("")
        return "\n".join(lines)
