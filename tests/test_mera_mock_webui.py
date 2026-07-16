"""In-process tests for MeraMock's Phase 6 settings-table webui
(docs/developer/mock-service-requirements.md §6): _settings_table_data(), the
aiohttp write routes, the static mock-controls.js/css mount, and that a write
survives a mock restart via mock_persistence.py.

Requires bluez_peripheral + aiohttp installed — not available in the primary
dev venv (see CLAUDE.md's Python path note); skipped automatically here via
pytest.importorskip when the deps are missing. Run on the mock VM (e.g.
anneubuntu-studio, /home/jens/venv) to actually exercise these.

Pattern mirrors test_ble20_client.py: async test_*() functions (each also a
standalone pytest test, since pyproject.toml sets asyncio_mode = "auto") plus
a _run_all() aggregator and a test_all_*() pytest entry point.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import traceback

import pytest

pytest.importorskip("bluez_peripheral")
pytest.importorskip("aiohttp")

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import aquaclean_ble_relay.mera_mock as mera_mock
from aquaclean_ble_relay.mera_mock import MeraMock

_STATIC_DIR = os.path.join(os.path.dirname(mera_mock.__file__), "static")


def _make_mock(state_dir: str) -> MeraMock:
    # Unique adapter (state_dir's own unique suffix), not None/"default" —
    # mock_logging caches its "mock.mera.<adapter>" logger globally by name,
    # with a logging.FileHandler that opens its file immediately and keeps
    # the fd; reusing "mock.mera.default" across tests with different (and,
    # by the time a later test runs, already-deleted) tmp dirs risks a stale
    # handle. A unique adapter per test avoids the collision entirely.
    return MeraMock(adapter=os.path.basename(state_dir), web_port=0, state_dir=state_dir)


def _build_app(mock: MeraMock) -> web.Application:
    app = web.Application()
    app.router.add_get("/", mock._handle_root)
    app.router.add_post("/settings/common/{setting_id}", mock._handle_write_common_setting)
    app.router.add_post("/settings/profile/{setting_id}", mock._handle_write_profile_setting)
    app.router.add_get("/events", mock._handle_events)
    app.router.add_static("/static/", path=_STATIC_DIR)
    return app


async def test_settings_table_data_sections():
    tmp = tempfile.mkdtemp()
    try:
        mock = _make_mock(tmp)
        data = mock._settings_table_data()
        titles = [s["title"] for s in data["sections"]]
        assert titles == ["Profile Settings", "Common Settings", "Firmware Versions"]
        assert len(data["sections"][0]["rows"]) == len(mock._STORED_PROFILE_SETTINGS)
        assert len(data["sections"][1]["rows"]) == len(mock._STORED_COMMON_SETTINGS)
        assert len(data["sections"][2]["rows"]) == len(mock._FW_COMPONENT_VERSIONS)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_root_page_renders_settings_table():
    tmp = tempfile.mkdtemp()
    try:
        mock = _make_mock(tmp)
        server = TestServer(_build_app(mock))
        client = TestClient(server)
        await client.start_server()
        try:
            r = await client.get("/")
            html = await r.text()
            assert r.status == 200
            assert "mc-root" in html
            assert "mcRenderSettingsTable" in html
        finally:
            await client.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_static_assets_served():
    tmp = tempfile.mkdtemp()
    try:
        mock = _make_mock(tmp)
        server = TestServer(_build_app(mock))
        client = TestClient(server)
        await client.start_server()
        try:
            r_js = await client.get("/static/mock-controls.js")
            r_css = await client.get("/static/mock-controls.css")
            assert r_js.status == 200
            assert r_css.status == 200
            assert "mcRenderSettingsTable" in await r_js.text()
        finally:
            await client.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_write_common_setting_persists():
    tmp = tempfile.mkdtemp()
    try:
        mock = _make_mock(tmp)
        server = TestServer(_build_app(mock))
        client = TestClient(server)
        await client.start_server()
        try:
            before = mock._STORED_COMMON_SETTINGS[1]
            new_value = 0 if before >= 4 else before + 1
            r = await client.post("/settings/common/1", json={"value": new_value})
            assert r.status == 200
            assert mock._STORED_COMMON_SETTINGS[1] == new_value
        finally:
            await client.close()

        # A fresh instance with the same state_dir/adapter must see the persisted
        # value — the actual "survives a mock restart" guarantee (requirements
        # doc §0/§5), not just an in-memory dict mutation.
        mock2 = _make_mock(tmp)
        assert mock2._STORED_COMMON_SETTINGS[1] == new_value
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_write_profile_setting_persists():
    tmp = tempfile.mkdtemp()
    try:
        mock = _make_mock(tmp)
        server = TestServer(_build_app(mock))
        client = TestClient(server)
        await client.start_server()
        try:
            before = mock._STORED_PROFILE_SETTINGS[2]
            new_value = 0 if before >= 4 else before + 1
            r = await client.post("/settings/profile/2", json={"value": new_value})
            assert r.status == 200
            assert mock._STORED_PROFILE_SETTINGS[2] == new_value
        finally:
            await client.close()

        mock2 = _make_mock(tmp)
        assert mock2._STORED_PROFILE_SETTINGS[2] == new_value
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_sse_events_pushes_on_write():
    """/events (docs/developer/mock-service-requirements.md §6 SSE) sends an
    initial state snapshot on connect, then a fresh push after a settings
    write — replacing the old full-page-reload polling mechanism."""
    tmp = tempfile.mkdtemp()
    try:
        mock = _make_mock(tmp)
        server = TestServer(_build_app(mock))
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.get("/events")
            assert resp.status == 200
            line1 = await resp.content.readline()
            await resp.content.readline()  # blank line: SSE message terminator ("data: ...\n\n")
            assert line1.startswith(b"data: ")
            initial = json.loads(line1[len(b"data: "):])
            assert initial["type"] == "state"
            assert "settings" in initial

            before = mock._STORED_COMMON_SETTINGS[1]
            new_value = 0 if before >= 4 else before + 1
            r = await client.post("/settings/common/1", json={"value": new_value})
            assert r.status == 200

            line2 = await resp.content.readline()
            await resp.content.readline()  # blank line terminator again
            pushed = json.loads(line2[len(b"data: "):])
            common_rows = pushed["settings"]["sections"][1]["rows"]
            row = next(x for x in common_rows if x["id"] == 1)
            assert row["value"] == new_value
            resp.close()
        finally:
            await client.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _run_all():
    tests = [
        test_settings_table_data_sections,
        test_root_page_renders_settings_table,
        test_static_assets_served,
        test_write_common_setting_persists,
        test_write_profile_setting_persists,
        test_sse_events_pushes_on_write,
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


def test_all_mera_mock_webui():
    """pytest entry point."""
    assert asyncio.run(_run_all())


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(_run_all()) else 1)
