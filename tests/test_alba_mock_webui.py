"""In-process tests for AlbaMock's Phase 6 settings-table webui
(docs/developer/mock-service-requirements.md §6): _Ble20AppLayer's
_settings_table_data()/_write_dpid_setting(), the FastAPI /settings/dpid/{id}
route (Nvm rows writable, Protected/Info rows rejected), and the static
mock-controls.js/css mount.

Requires bluez_peripheral + fastapi + uvicorn installed — not available in
the primary dev venv (see CLAUDE.md's Python path note); skipped
automatically here via pytest.importorskip when the deps are missing. Run on
the mock VM (e.g. anneubuntu-studio, /home/jens/venv) to actually exercise
these.

The FastAPI route is defined as an inline closure inside AlbaMock.run() in
the real code (it needs the D-Bus/BLE session context) — these tests rebuild
just the two new pieces (the write route + static mount) around a directly
constructed _Ble20AppLayer, which is exactly what the route handler touches.

Uses aiohttp.ClientSession (already a project dependency-of-a-dependency via
the Mera mock) as the HTTP client against a real uvicorn server on an
OS-assigned port, run as a background asyncio task on the SAME event loop —
deliberately not a synchronous client (requests/urllib), which would block
that loop and deadlock against the server it's supposed to be talking to.

Pattern mirrors test_ble20_client.py: async test_*() functions (each also a
standalone pytest test, since pyproject.toml sets asyncio_mode = "auto") plus
a _run_all() aggregator and a test_all_*() pytest entry point.
"""

import asyncio
import logging
import os
import shutil
import sys
import tempfile
import traceback

import pytest

pytest.importorskip("bluez_peripheral")
pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import aiohttp
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import aquaclean_ble_relay.alba_mock as alba_mock
from aquaclean_ble_relay.alba_mock import _Ble20AppLayer

_STATIC_DIR = os.path.join(os.path.dirname(alba_mock.__file__), "static")


def _make_layer(device_key: str) -> _Ble20AppLayer:
    # Dedicated "test.*" logger name, never touched by mock_logging.py's
    # file-handler-attaching logic — avoids every test in this file colliding
    # on the single globally-cached "mock.alba.default" logger (logging.
    # FileHandler opens its file immediately and keeps the fd; reusing that
    # cached logger after an earlier test's tmp dir is deleted raises
    # FileNotFoundError on the next log call from a *different* test).
    return _Ble20AppLayer(device_key=device_key, logger=logging.getLogger(f"test.alba.{device_key}"))


def _build_app(layer: _Ble20AppLayer) -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.post("/settings/dpid/{dp_id}")
    async def write_dpid(dp_id: int, request: Request):
        body = await request.json()
        try:
            layer._write_dpid_setting(dp_id, body["value"])
        except (KeyError, ValueError) as e:
            return HTMLResponse(str(e), status_code=400)
        return {"ok": True}

    return app


class _RunningServer:
    """Starts a real uvicorn server on an OS-assigned port, in a background
    task on the current event loop — see module docstring for why not a
    synchronous client/blocking call."""

    def __init__(self, app: FastAPI):
        self._config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", loop="none", lifespan="off")
        self._server = uvicorn.Server(self._config)
        self._server.install_signal_handlers = lambda: None
        self._task = None

    async def __aenter__(self) -> str:
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(50):
            # .servers doesn't exist as an attribute until Server.startup()
            # actually runs and assigns it — getattr, not a truthiness check
            # on direct attribute access, or this raises AttributeError
            # during the startup window instead of just being "not ready yet".
            servers = getattr(self._server, "servers", None)
            if servers:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("uvicorn server did not start in time")
        port = servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def __aexit__(self, *exc):
        self._server.should_exit = True
        await asyncio.sleep(0.1)
        self._task.cancel()


async def test_settings_table_data_sections():
    tmp = tempfile.mkdtemp()
    try:
        from aquaclean_ble_relay import mock_persistence
        mock_persistence.set_state_dir(tmp)
        layer = _make_layer("test-device")
        data = layer._settings_table_data()
        titles = [s["title"] for s in data["sections"]]
        assert titles == ["Settings", "Identity & Firmware", "DpId Reference (all)"]
        writable_ids = {r["id"] for r in data["sections"][0]["rows"]}
        readonly_ids = {r["id"] for r in data["sections"][1]["rows"]}
        assert writable_ids == {13, 580, 581, 582, 583, 795}  # the Nvm (behavior==3) DpIds
        assert 12 in readonly_ids  # PAIRING_SECRET — Protected, must stay read-only
        assert 369 in readonly_ids  # SALES_PRODUCT_SERIAL_NUMBER — Protected

        # DpId Reference (2026-07-18 ask): every (dp_id, instance) in the store,
        # not just the ~14 curated Settings/Identity rows above.
        reference_rows = data["sections"][2]["rows"]
        assert len(reference_rows) == len(layer._store)
        assert all(r["kind"] == "readonly" for r in reference_rows)
        names = [r["name"] for r in reference_rows]
        assert any(n.startswith("0: DEVICE_SERIES") for n in names)
        assert any(n.startswith("786: GEBERIT_LOADER_VERSION (inst=2)") for n in names)

        # Timestamps (datatype 13, TimeStampUtc — 2026-07-18 ask) render as human-
        # readable UTC strings, not raw epoch ints; other 4-byte-LE types (e.g.
        # DEVICE_SERIES, datatype 9/Counter) are unaffected and stay plain ints.
        rtc_row = next(r for r in reference_rows if r["id"] == "15-None")
        assert rtc_row["value"] == "2000-01-07 23:07:23 UTC"
        series_row = next(r for r in reference_rows if r["id"] == "0-None")
        assert series_row["value"] == 250
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_write_dpid_setting_persists():
    tmp = tempfile.mkdtemp()
    try:
        from aquaclean_ble_relay import mock_persistence
        mock_persistence.set_state_dir(tmp)
        layer = _make_layer("test-device")
        layer._write_dpid_setting(580, 2)
        assert bytes(layer._find_entry(580)["value"]) == b"\x02"

        # A fresh instance with the same device_key must see the persisted
        # value — the "survives a mock restart" guarantee (requirements doc
        # §0/§5), not just an in-memory mutation.
        layer2 = _make_layer("test-device")
        assert bytes(layer2._find_entry(580)["value"]) == b"\x02"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_write_dpid_setting_rejects_protected():
    tmp = tempfile.mkdtemp()
    try:
        from aquaclean_ble_relay import mock_persistence
        mock_persistence.set_state_dir(tmp)
        layer = _make_layer("test-device")
        with pytest.raises(ValueError):
            layer._write_dpid_setting(12, "1111")  # PAIRING_SECRET is Protected, not Nvm
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_write_route_over_http():
    tmp = tempfile.mkdtemp()
    try:
        from aquaclean_ble_relay import mock_persistence
        mock_persistence.set_state_dir(tmp)
        layer = _make_layer("test-device-http")
        app = _build_app(layer)

        async with _RunningServer(app) as base_url:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{base_url}/settings/dpid/581", json={"value": 3}) as r:
                    assert r.status == 200
                assert bytes(layer._find_entry(581)["value"]) == b"\x03"

                async with session.post(f"{base_url}/settings/dpid/12", json={"value": "1111"}) as r:
                    assert r.status == 400  # Protected DpId — must be rejected, not silently written

                async with session.get(f"{base_url}/static/mock-controls.js") as r:
                    assert r.status == 200
                    assert "mcRenderSettingsTable" in await r.text()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_broadcast_fn_called_on_persisted_write():
    """_write_dpid_setting() (webui) and _write() (real BLE WriteCmd) both call
    broadcast_fn after a persisted (behavior==3, Nvm) write — the hook AlbaMock
    threads its own webui-SSE broadcaster through, so real BLE writes push a
    fresh state update, not just webui-initiated ones (docs/developer/
    mock-service-requirements.md §6). A write to a non-Nvm DpId (nothing
    persisted) must NOT broadcast."""
    tmp = tempfile.mkdtemp()
    try:
        from aquaclean_ble_relay import mock_persistence
        mock_persistence.set_state_dir(tmp)
        calls = []
        layer = _Ble20AppLayer(
            device_key="test-broadcast", broadcast_fn=lambda: calls.append(1),
            logger=logging.getLogger("test.alba.broadcast"),
        )

        layer._write_dpid_setting(580, 2)
        assert len(calls) == 1

        from aquaclean_console_app.bluetooth_le.LE.Ble20Client import encode_address
        layer._write(bytes([0x20]) + encode_address(581) + bytes([3]))
        assert len(calls) == 2

        layer._write(bytes([0x20]) + encode_address(607) + bytes([1]))  # behavior==1, not Nvm
        assert len(calls) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_alba_mock_broadcast_state_nowait_payload():
    """AlbaMock._broadcast_state_nowait() pushes a well-formed {"type":"state",
    "settings":..., "notify":...} payload to every subscriber queue — the same
    shape the /events SSE route sends."""
    tmp = tempfile.mkdtemp()
    try:
        from aquaclean_ble_relay import mock_persistence
        mock_persistence.set_state_dir(tmp)
        # Unique adapter (tmp's own unique suffix) so mock_logging's globally-
        # cached "mock.alba.<adapter>" logger is never reused across separate
        # invocations of this test (individual pytest run vs _run_all()) with
        # different, already-deleted tmp dirs — see _make_layer's comment above.
        mock = alba_mock.AlbaMock(adapter=os.path.basename(tmp), mode="ble20", web_port=0, state_dir=tmp)
        queue = asyncio.Queue()
        mock._sse_queues.append(queue)

        layer = _make_layer("test-broadcast-payload")
        mock._broadcast_state_nowait(layer)
        assert not queue.empty()
        data = queue.get_nowait()
        assert data["type"] == "state"
        assert "settings" in data
        assert "notify" in data

        # None (no active BLE session) must still produce a valid payload, just
        # without a "settings" key — the webui shows an empty table, not a crash.
        mock._broadcast_state_nowait(None)
        data2 = queue.get_nowait()
        assert data2["type"] == "state"
        assert "settings" not in data2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _run_all():
    tests = [
        test_settings_table_data_sections,
        test_write_dpid_setting_persists,
        test_write_dpid_setting_rejects_protected,
        test_write_route_over_http,
        test_broadcast_fn_called_on_persisted_write,
        test_alba_mock_broadcast_state_nowait_payload,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            await t()
            passed += 1
        except Exception as e:
            print(f"  {t.__name__}: FAIL — {e}")
            traceback.print_exc()
            failed += 1
    total = passed + failed
    print(f"\n{'OK' if failed == 0 else 'FAILED'}: {passed}/{total} tests passed")
    return failed == 0


def test_all_alba_mock_webui():
    """pytest entry point."""
    assert asyncio.run(_run_all())


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(_run_all()) else 1)
