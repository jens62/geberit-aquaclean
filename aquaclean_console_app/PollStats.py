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

    def __init__(self):
        self.poll:        _MetricStats = _MetricStats()
        self.ble:         _MetricStats = _MetricStats()
        self.esphome_api: _MetricStats = _MetricStats()

    @property
    def sample_count(self) -> int:
        return self.poll.count

    def record(self, esphome_api_ms, ble_ms, poll_ms) -> None:
        self.poll.record(poll_ms)
        # Only count connect times when a real connection was established (value > 0)
        if ble_ms is not None and float(ble_ms) > 0:
            self.ble.record(ble_ms)
        if esphome_api_ms is not None and float(esphome_api_ms) > 0:
            self.esphome_api.record(esphome_api_ms)

    def to_dict(self) -> dict:
        return {
            "sample_count":   self.sample_count,
            "poll_ms":        self.poll.to_dict(),
            "ble_ms":         self.ble.to_dict(),
            "esphome_api_ms": self.esphome_api.to_dict(),
        }

    def to_markdown_rows(self) -> list[str]:
        def _f(v):
            return f"{v} ms" if v is not None else "—"

        rows = []
        for label, m in [
            ("Poll (query)",  self.poll),
            ("BLE connect",   self.ble),
            ("ESP32 connect", self.esphome_api),
        ]:
            rows.append(
                f"| {label:<16} | {_f(m.min_ms):>10} | {_f(m.avg_ms):>10} | {_f(m.max_ms):>10} | {m.count:>7} |"
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

    def record(self, mode: str, esphome_api_ms, ble_ms, poll_ms) -> None:
        """Record one completed poll cycle's timings for the given connection mode."""
        try:
            stats = self._modes.get(mode)
            if stats:
                stats.record(esphome_api_ms, ble_ms, poll_ms)
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
            lines.append(f"| {'Metric':<16} | {'Min':>10} | {'Avg':>10} | {'Max':>10} | {'Samples':>7} |")
            lines.append(f"|{'-'*18}|{'-'*12}|{'-'*12}|{'-'*12}|{'-'*9}|")
            lines.extend(stats.to_markdown_rows())
            lines.append("")
        return "\n".join(lines)
