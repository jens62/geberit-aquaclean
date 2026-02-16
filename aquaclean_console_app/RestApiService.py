import asyncio
import json
import logging
import os

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

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

    def _register_routes(self):
        app = self.app

        @app.on_event("shutdown")
        async def _close_sse_connections():
            for q in list(self._sse_queues):
                q.put_nowait(None)  # sentinel: tells each generator to return

        @app.get("/")
        async def serve_ui():
            return FileResponse(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "static", "index.html"
            ))

        @app.get("/events")
        async def sse():
            queue: asyncio.Queue = asyncio.Queue()
            self._sse_queues.append(queue)
            try:
                initial = await self._api_mode.get_status()
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
            await self._api_mode.run_command("toggle-lid")
            return {"status": "success", "command": "toggle-lid"}

        @app.post("/command/toggle-anal")
        async def toggle_anal():
            await self._api_mode.run_command("toggle-anal")
            return {"status": "success", "command": "toggle-anal"}

        @app.post("/connect")
        async def connect():
            return await self._api_mode.do_connect()

        @app.post("/disconnect")
        async def disconnect():
            return await self._api_mode.do_disconnect()

        @app.post("/reconnect")
        async def reconnect():
            return await self._api_mode.do_reconnect()

    async def start(self):
        server_config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="none",
        )
        server = uvicorn.Server(server_config)

        # Prevent uvicorn from installing its own SIGINT/SIGTERM handlers.
        # aiorun is the outermost loop and must own signal handling so that
        # ONE Ctrl+C cleanly cancels all subsystems (uvicorn, BLE, bleak).
        server.install_signal_handlers = lambda: None

        serve_task = asyncio.create_task(server.serve())

        # When aiorun cancels this task on Ctrl+C, also tell uvicorn to
        # skip its connection-draining wait (equivalent to a second Ctrl+C).
        _original_cancel = serve_task.cancel
        def _cancel_and_force_exit(*args, **kwargs):
            server.should_exit = True
            server.force_exit = True
            return _original_cancel(*args, **kwargs)
        serve_task.cancel = _cancel_and_force_exit

        try:
            await serve_task
        except asyncio.CancelledError:
            pass
