"""Shared per-device logging for Geberit mock BLE peripherals
(docs/developer/mock-service-requirements.md §7).

One logging.Logger per (model, adapter) instance, named "mock.<model>.<adapter>"
(e.g. "mock.mera.hci1") so multiple simultaneous mock instances stay
distinguishable. Every logger gets three handlers: console, a per-device file,
and a combined file shared by every device logger in this process — a script/
CI consumer can grep the combined file for cross-device correlation, or one
device's own file in isolation, without the two being a choice.

Device tag is the logger name itself (%(name)s in the format string), placed
right after the timestamp — no per-record `extra=` plumbing needed.
"""
import logging
import time
from pathlib import Path

_LOG_DIR = Path(__file__).parent / "logs"
_FORMATTER = logging.Formatter("[%(asctime)s] [%(name)s] %(message)s", datefmt="%H:%M:%S")
# Fixed once at import time (not per-logger) so every device's file and the
# combined file from the same process run share one recognizable timestamp.
_RUN_TIMESTAMP = time.strftime("%Y-%m-%d_%H-%M")
_combined_handler: logging.Handler | None = None
_configured_loggers: set = set()


def set_log_dir(state_dir) -> None:
    """Point new loggers' files at state_dir/logs instead of the module's own
    directory. Call once at process startup (mock_service.py --state-dir, or
    a standalone mock's own --state-dir arg), before any get_device_logger()
    call — mirrors mock_persistence.set_state_dir()."""
    global _LOG_DIR, _combined_handler
    _LOG_DIR = Path(state_dir) / "logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _combined_handler = None  # re-created lazily under the new dir on next use


def _get_combined_handler() -> logging.Handler:
    global _combined_handler
    if _combined_handler is None:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOG_DIR / f"mock-combined_{_RUN_TIMESTAMP}.log"
        _combined_handler = logging.FileHandler(path, encoding="utf-8")
        _combined_handler.setFormatter(_FORMATTER)
    return _combined_handler


def get_device_logger(model: str, adapter: str | None) -> logging.Logger:
    """Return the (model, adapter) logger, configuring it on first call for
    this (model, adapter) pair. Idempotent — safe to call every time a device
    instance is constructed, including across repeated test-instance
    construction in the same process (e.g. a mock restarted in a test)."""
    adapter_tag = adapter or "default"
    name = f"mock.{model}.{adapter_tag}"
    logger = logging.getLogger(name)
    if name in _configured_loggers:
        return logger
    _configured_loggers.add(name)

    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # don't bubble to the root logger

    console_h = logging.StreamHandler()
    console_h.setFormatter(_FORMATTER)
    logger.addHandler(console_h)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    device_path = _LOG_DIR / f"mock-{model}-{adapter_tag}_{_RUN_TIMESTAMP}.log"
    device_h = logging.FileHandler(device_path, encoding="utf-8")
    device_h.setFormatter(_FORMATTER)
    logger.addHandler(device_h)

    logger.addHandler(_get_combined_handler())  # same Handler instance across all device loggers
    logger.device_log_path = device_path  # exposed so callers can report it themselves
    logger.info("Per-device log: %s", device_path.name)
    return logger
