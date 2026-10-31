from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.db.session import get_session
from hostora.realtime.broadcaster import Broadcaster
from hostora.schemas.common import ApiModel
from hostora.schemas.gate import GateManifest, GateScanBatch
from hostora.security.firebase import HostIdentity
from hostora.services.gate import create_gate_link, get_gate_manifest, submit_gate_scans
from hostora.web.deps import get_broadcaster_dep, get_host_dep, get_settings_dep

router = APIRouter(tags=["gate"])


class GateLinkInput(ApiModel):
    function_id: Annotated[str, Field(min_length=1, max_length=40)]
    hours: Annotated[int, Field(ge=1, le=168)] = 24


@router.post("/v1/events/{event_key}/gate-links")
async def api_create_gate_link(
    event_key: str,
    body: GateLinkInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, str]:
    return await create_gate_link(
        session,
        event_key,
        host,
        function_id=body.function_id,
        hours=body.hours,
        settings=settings,
    )


@router.get("/v1/gate/{event_key}/manifest", response_model=GateManifest)
async def api_get_gate_manifest(
    event_key: str,
    t: Annotated[str, Query(min_length=5)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> GateManifest:
    return await get_gate_manifest(session, event_key, t, settings)


@router.post("/v1/gate/{event_key}/scans:batch")
async def api_submit_gate_scans(
    event_key: str,
    body: GateScanBatch,
    t: Annotated[str, Query(min_length=5)],
    session: Annotated[AsyncSession, Depends(get_session)],
    broadcaster: Annotated[Broadcaster, Depends(get_broadcaster_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    return await submit_gate_scans(
        session,
        broadcaster,
        event_key=event_key,
        token=t,
        batch=body,
        settings=settings,
    )
