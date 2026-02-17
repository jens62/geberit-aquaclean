import asyncio
import json
import logging
import os
import signal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel


class BleConnectionUpdate(BaseModel):
    value: str

logger = logging.getLogger(__name__)


class RestApiService:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = int(port)
        self.app = FastAPI(title="Geberit AquaClean REST API")
        self._api_mode = None
        self._sse_queues: list[asyncio.Queue] = []
        self._register_routes()

    def set_api_mode(self, api_mode):
        self._api_mode = api_mode

    async def broadcast_state(self, state: dict):
        data = {"type": "state", **state}
        for q in list(self._sse_queues):
            await q.put(data)

    def _close_sse_connections(self):
        for q in list(self._sse_queues):
            q.put_nowait(None)  # sentinel: tells each generator to return

    def _register_routes(self):
        app = self.app

        @app.get("/")
        async def serve_ui():
            return FileResponse(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"),
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/events")
        async def sse():
            queue: asyncio.Queue = asyncio.Queue()
            self._sse_queues.append(queue)
            try:
                initial = self._api_mode.get_current_state()
                await queue.put({"type": "state", **initial})
            except Exception:
                pass

            async def generate():
                try:
                    while True:
                        try:
                            data = await asyncio.wait_for(queue.get(), timeout=30.0)
                            if data is None:  # shutdown sentinel
                                return
                            yield f"data: {json.dumps(data)}\n\n"
                        except asyncio.TimeoutError:
                            yield ": heartbeat\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    pass
                finally:
                    if queue in self._sse_queues:
                        self._sse_queues.remove(queue)

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @app.get("/status")
        async def get_status():
            return await self._api_mode.get_status()

        @app.get("/info")
        async def get_info():
            return await self._api_mode.get_info()

        @app.post("/command/toggle-lid")
        async def toggle_lid():
            result = await self._api_mode.run_command("toggle-lid") or {}
            return {"status": "success", "command": "toggle-lid", **result}

        @app.post("/command/toggle-anal")
        async def toggle_anal():
            result = await self._api_mode.run_command("toggle-anal") or {}
            return {"status": "success", "command": "toggle-anal", **result}

        @app.get("/data/system-parameters")
        async def get_system_parameters():
            return await self._api_mode.get_system_parameters()

        @app.get("/data/soc-versions")
        async def get_soc_versions():
            return await self._api_mode.get_soc_versions()

        @app.get("/data/initial-operation-date")
        async def get_initial_operation_date():
            return await self._api_mode.get_initial_operation_date()

        @app.get("/data/identification")
        async def get_identification():
            return await self._api_mode.get_identification()

        @app.get("/config")
        async def get_config():
            return self._api_mode.get_config()

        @app.post("/config/ble-connection")
        async def set_ble_connection(body: BleConnectionUpdate):
            return await self._api_mode.set_ble_connection(body.value)

        @app.post("/connect")
        async def connect():
            return await self._api_mode.do_connect()

        @app.post("/disconnect")
        async def disconnect():
            return await self._api_mode.do_disconnect()

    async def start(self, shutdown_event: asyncio.Event):
        server_config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="none",
            lifespan="off",
        )
        server = uvicorn.Server(server_config)

        # Prevent uvicorn from installing its own signal handlers —
        # the caller (ApiMode) owns signal handling via the shared shutdown_event.
        server.install_signal_handlers = lambda: None

        serve_task = asyncio.create_task(server.serve())

        # Wait for uvicorn to finish startup.
        while not server.started:
            if serve_task.done():
                break
            await asyncio.sleep(0.05)

        # NOW override whatever signal handler uvicorn may have installed
        # (even if our lambda trick didn't work on this version).
        # A single Ctrl+C sets the shared shutdown event which all
        # subsystems (BLE, MQTT, uvicorn) observe independently.
        loop = asyncio.get_running_loop()

        def _on_signal():
            logger.info("Shutdown signal received — stopping all subsystems...")
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_signal)

        # Watch the shared shutdown event in a background task.
        # When it fires, tell uvicorn to stop immediately and cancel
        # serve_task in case serve() doesn't return on its own.
        async def _shutdown_watcher():
            await shutdown_event.wait()
            self._close_sse_connections()
            server.should_exit = True
            server.force_exit = True
            serve_task.cancel()

        watcher_task = asyncio.create_task(_shutdown_watcher())

        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.remove_signal_handler(sig)
                except Exception:
                    pass
