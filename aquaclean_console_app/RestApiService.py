import logging

import uvicorn
from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)


class RestApiService:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = int(port)
        self.app = FastAPI(title="Geberit AquaClean REST API")
        self._api_mode = None
        self._register_routes()

    def set_api_mode(self, api_mode):
        self._api_mode = api_mode

    def _register_routes(self):
        app = self.app

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
        await server.serve()
