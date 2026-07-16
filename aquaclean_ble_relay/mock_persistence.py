"""Shared SQLite persistence for Geberit mock BLE peripherals.

Keeps mutable mock state (firmware versions, DpId values, ...) durable across
process restarts, keyed by (device_type, device_key) so multiple simultaneous
mock instances - e.g. two mock-geberit-alba.py processes bound to two USB BT
dongles via --adapter - each keep independent state in the same DB file.

device_key is normally the BLE MAC address the instance advertises with
(unique per physical adapter - see docs/developer/mock-geberit-alba.md and
mock-ble-advertising-mac.md), falling back to "default" for single-instance
use where no adapter was explicitly selected.
"""
import json
import sqlite3
import time
from pathlib import Path

_DB_PATH = Path(__file__).parent / "mock_state.db"


def set_state_dir(state_dir) -> None:
    """Point the shared store at mock_state.db under state_dir instead of the
    module's own directory. Call once at process startup (mock_service.py
    --state-dir, or a standalone mock's own arg parsing) before any
    load_all/save/reset call. Devices stay isolated by (device_type,
    device_key) within this one file, same as always - this only moves
    where the file lives, e.g. so --state-dir doesn't collide with a
    read-only install location.
    """
    global _DB_PATH
    path = Path(state_dir)
    path.mkdir(parents=True, exist_ok=True)
    _DB_PATH = path / "mock_state.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mock_state (
            device_type TEXT NOT NULL,
            device_key  TEXT NOT NULL,
            state_key   TEXT NOT NULL,
            value       TEXT NOT NULL,
            updated_at  REAL NOT NULL,
            PRIMARY KEY (device_type, device_key, state_key)
        )
        """
    )
    return conn


def load_all(device_type: str, device_key: str) -> dict:
    """Return {state_key: value} for one device instance, JSON-decoded."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT state_key, value FROM mock_state WHERE device_type=? AND device_key=?",
            (device_type, device_key),
        ).fetchall()
    return {k: json.loads(v) for k, v in rows}


def save(device_type: str, device_key: str, state_key: str, value) -> None:
    """Persist one (state_key -> value) pair immediately."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mock_state (device_type, device_key, state_key, value, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(device_type, device_key, state_key) "
            "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (device_type, device_key, state_key, json.dumps(value), time.time()),
        )
        conn.commit()


def reset(device_type: str, device_key: str) -> None:
    """Delete all persisted state for one device instance (factory reset)."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM mock_state WHERE device_type=? AND device_key=?",
            (device_type, device_key),
        )
        conn.commit()
