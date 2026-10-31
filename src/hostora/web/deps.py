from fastapi import Request

from hostora.adapters.llm.router import LlmRouter
from hostora.config import Settings
from hostora.realtime.broadcaster import Broadcaster
from hostora.security.firebase import HostIdentity
from hostora.security.session import current_host


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_broadcaster_dep(request: Request) -> Broadcaster:
    broadcaster: Broadcaster = request.app.state.broadcaster
    return broadcaster


def get_llm_router_dep(request: Request) -> LlmRouter:
    router: LlmRouter = request.app.state.llm_router
    return router


async def get_host_dep(request: Request) -> HostIdentity:
    return await current_host(request)
