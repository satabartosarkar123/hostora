from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.db.session import get_session
from hostora.errors import ProblemError
from hostora.realtime.broadcaster import Broadcaster
from hostora.security.firebase import HostIdentity
from hostora.security.ratelimit import client_ip
from hostora.services.demo import pick_demo_join_url, seed_demo_event
from hostora.web.deps import get_broadcaster_dep, get_host_dep, get_settings_dep

router = APIRouter(tags=["demo"])


@router.post("/v1/demo/seed")
async def api_seed_demo(
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    broadcaster: Annotated[Broadcaster, Depends(get_broadcaster_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    if not settings.demo_mode or host.uid != "demo-host":
        raise ProblemError(404, "Not found")
    return await seed_demo_event(session, settings, broadcaster)


@router.get("/demo/join")
async def demo_join(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> RedirectResponse:
    url = await pick_demo_join_url(
        session,
        client_ip(request),
        settings,
        datetime.now(UTC),
    )
    return RedirectResponse(url=url, status_code=303)
