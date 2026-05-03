from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from token_scrooge.api.websocket import ConnectionManager
from token_scrooge.db.database import dispose_engine, init_db, startup_vacuum

logger = logging.getLogger(__name__)

_ws_manager = ConnectionManager()


def get_ws_manager() -> ConnectionManager:
    return _ws_manager


def create_app(settings=None) -> FastAPI:
    from token_scrooge.config import Settings

    if settings is None:
        settings = Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        _ws_manager.set_loop(asyncio.get_event_loop())
        settings.ensure_dirs()
        init_db(settings.storage.db_path)
        startup_vacuum()
        # Start proxy
        from token_scrooge.proxy.runner import start_proxy
        start_proxy(settings, _ws_manager)
        yield
        # Shutdown
        from token_scrooge.proxy.runner import stop_proxy
        stop_proxy()
        dispose_engine()

    app = FastAPI(title="Token-Scrooge", lifespan=lifespan)
    app.state.settings = settings
    app.state.ws_manager = _ws_manager

    # Routers
    from token_scrooge.api.routers import proxy as proxy_router
    from token_scrooge.api.routers import requests as requests_router
    from token_scrooge.api.routers import sessions as sessions_router
    from token_scrooge.api.routers import stats as stats_router
    from token_scrooge.api.routers import tokenize as tokenize_router

    app.include_router(sessions_router.router, prefix="/api")
    app.include_router(requests_router.router, prefix="/api")
    app.include_router(stats_router.router, prefix="/api")
    app.include_router(proxy_router.router, prefix="/api")
    app.include_router(tokenize_router.router, prefix="/api")

    # WebSocket
    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await _ws_manager.connect(websocket)
        try:
            while True:
                # Keep connection alive; we only push from server side
                await websocket.receive_text()
        except WebSocketDisconnect:
            _ws_manager.disconnect(websocket)

    # Serve built React UI (production)
    ui_dist = Path(__file__).parent.parent.parent / "ui" / "dist"
    if ui_dist.exists():
        app.mount("/", StaticFiles(directory=str(ui_dist), html=True), name="ui")

    return app
