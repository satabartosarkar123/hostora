from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from hostora.adapters.llm.router import build_llm_router
from hostora.config import Settings, get_settings
from hostora.data.loader import get_data, load_data
from hostora.db.session import make_engine, make_sessionmaker
from hostora.errors import register_error_handlers
from hostora.logging import RequestLoggingMiddleware, configure_logging
from hostora.realtime.broadcaster import Broadcaster
from hostora.security.headers import SecurityHeadersMiddleware
from hostora.web.api import demo, events, health, internal, invites, plans
from hostora.web.api import gate as gate_api
from hostora.web.pages import auth, guest, host, planner
from hostora.web.pages import gate as gate_pages
from hostora.web.templating import make_templates

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.responses import Response
    from starlette.types import Scope


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    configure_logging(cfg)
    data_bundle = load_data(cfg.data_dir)
    get_data.cache_clear()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "engine", None) is None:
            engine = make_engine(cfg)
            app.state.engine = engine
            app.state.sessionmaker = make_sessionmaker(engine)
        if not hasattr(app.state, "broadcaster"):
            app.state.broadcaster = Broadcaster()
        app.state.data = data_bundle
        try:
            yield
        finally:
            if getattr(app.state, "engine", None) is not None:
                await app.state.engine.dispose()
                app.state.engine = None

    app = FastAPI(
        title="Hostora",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.engine = make_engine(cfg)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.broadcaster = Broadcaster()
    app.state.data = data_bundle
    app.state.templates = make_templates()
    app.state.llm_router = build_llm_router(
        cfg,
        app.state.sessionmaker,
        data_version=data_bundle.version,
    )

    # Added innermost-first: RequestLogging -> SecurityHeaders -> Session -> GZip
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key.get_secret_value(),
        session_cookie="hostora_session",
        max_age=7 * 24 * 3600,
        same_site="lax",
        https_only=cfg.env == "prod",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    register_error_handlers(app)

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", CachedStaticFiles(directory=str(static_dir)), name="static")

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz", tags=["health"])
    async def readyz(request: Request) -> JSONResponse:
        try:
            async with request.app.state.sessionmaker() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"ok": False})
        bundle: Any = request.app.state.data  # Any: DataBundle stored on app.state
        return JSONResponse(
            status_code=200,
            content={"ok": True, "dataVersion": bundle.version},
        )

    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(invites.router)
    app.include_router(demo.router)
    app.include_router(plans.router)
    app.include_router(gate_api.router)
    app.include_router(internal.router)
    app.include_router(guest.router)
    app.include_router(auth.router)
    app.include_router(host.router)
    app.include_router(planner.router)
    app.include_router(gate_pages.router)

    return app
