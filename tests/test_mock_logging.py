"""Tests for aquaclean_ble_relay/mock_logging.py (docs/developer/
mock-service-requirements.md §7): per-(model, adapter) logger naming, device
tag position, per-device file isolation, and the shared combined-file handler
across multiple device loggers in one process.

Unlike test_mera_mock_webui.py/test_alba_mock_webui.py, mock_logging.py has
no bluez_peripheral/aiohttp/fastapi dependency — it's stdlib-only (logging,
time, pathlib) — so these tests run in any environment, including the
primary dev venv.

Pattern mirrors test_ble20_client.py: plain test_*() functions (each also a
standalone pytest test) plus a _run_all() aggregator and a test_all_*()
pytest entry point.
"""

import os
import shutil
import sys
import tempfile
import traceback

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import aquaclean_ble_relay.mock_logging as mock_logging


def _fresh_module_state():
    """mock_logging's logger cache and combined-handler are process-wide
    globals — reset them so each test starts clean regardless of what ran
    (or is still cached in logging's own module-level logger registry)
    before it."""
    mock_logging._configured_loggers.clear()
    mock_logging._combined_handler = None
    import logging
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("mock."):
            logger = logging.getLogger(name)
            for h in list(logger.handlers):
                logger.removeHandler(h)


def test_device_tag_at_fixed_position():
    tmp = tempfile.mkdtemp()
    try:
        _fresh_module_state()
        mock_logging.set_log_dir(tmp)
        logger = mock_logging.get_device_logger("mera", "hci0")
        assert logger.name == "mock.mera.hci0"
        logger.info("hello")

        log_dir = mock_logging._LOG_DIR
        device_file = next(p for p in log_dir.iterdir() if "mera-hci0" in p.name)
        line = [l for l in device_file.read_text().splitlines() if "hello" in l][0]
        # "[HH:MM:SS] [mock.mera.hci0] hello" — tag immediately after the timestamp
        assert line.startswith("[")
        ts_end = line.index("]") + 1
        assert line[ts_end:].lstrip().startswith("[mock.mera.hci0]")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_per_device_files_are_isolated():
    tmp = tempfile.mkdtemp()
    try:
        _fresh_module_state()
        mock_logging.set_log_dir(tmp)
        mera_logger = mock_logging.get_device_logger("mera", "hci0")
        alba_logger = mock_logging.get_device_logger("alba", "hci1")
        mera_logger.info("only mera")
        alba_logger.info("only alba")

        log_dir = mock_logging._LOG_DIR
        mera_file = next(p for p in log_dir.iterdir() if "mera-hci0" in p.name)
        alba_file = next(p for p in log_dir.iterdir() if "alba-hci1" in p.name)
        mera_text = mera_file.read_text()
        alba_text = alba_file.read_text()

        assert "only mera" in mera_text and "only alba" not in mera_text
        assert "only alba" in alba_text and "only mera" not in alba_text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_combined_file_shared_across_devices():
    tmp = tempfile.mkdtemp()
    try:
        _fresh_module_state()
        mock_logging.set_log_dir(tmp)
        mera_logger = mock_logging.get_device_logger("mera", "hci0")
        alba_logger = mock_logging.get_device_logger("alba", "hci1")
        mera_logger.info("from mera")
        alba_logger.info("from alba")

        log_dir = mock_logging._LOG_DIR
        combined_files = [p for p in log_dir.iterdir() if "combined" in p.name]
        assert len(combined_files) == 1  # one shared file, not one per device
        combined_text = combined_files[0].read_text()
        assert "[mock.mera.hci0]" in combined_text and "from mera" in combined_text
        assert "[mock.alba.hci1]" in combined_text and "from alba" in combined_text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_device_logger_is_idempotent():
    tmp = tempfile.mkdtemp()
    try:
        _fresh_module_state()
        mock_logging.set_log_dir(tmp)
        logger1 = mock_logging.get_device_logger("mera", "hci0")
        handler_count_1 = len(logger1.handlers)
        logger2 = mock_logging.get_device_logger("mera", "hci0")
        assert logger1 is logger2
        assert len(logger2.handlers) == handler_count_1  # no duplicate handlers on re-call
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_default_adapter_falls_back_to_default_tag():
    tmp = tempfile.mkdtemp()
    try:
        _fresh_module_state()
        mock_logging.set_log_dir(tmp)
        logger = mock_logging.get_device_logger("alba", None)
        assert logger.name == "mock.alba.default"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all():
    tests = [
        test_device_tag_at_fixed_position,
        test_per_device_files_are_isolated,
        test_combined_file_shared_across_devices,
        test_get_device_logger_is_idempotent,
        test_default_adapter_falls_back_to_default_tag,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  {t.__name__}: FAIL — {e}")
            traceback.print_exc()
            failed += 1
    total = passed + failed
    print(f"\n{'OK' if failed == 0 else 'FAILED'}: {passed}/{total} tests passed")
    return failed == 0


def test_all_mock_logging():
    """pytest entry point."""
    assert _run_all()


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
