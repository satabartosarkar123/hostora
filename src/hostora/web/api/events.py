import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from hostora.config import Settings
from hostora.db.session import get_session
from hostora.realtime.broadcaster import Broadcaster
from hostora.schemas.common import ApiModel
from hostora.schemas.events import (
    CreatedEvent,
    CreateEventInput,
    HouseholdBatch,
    PreparedInvite,
)
from hostora.schemas.summary import EventSummary
from hostora.security.firebase import HostIdentity
from hostora.security.session import require_event_access
from hostora.services.accounts import delete_account
from hostora.services.events import (
    create_event,
    get_event_detail,
    list_host_events,
    record_outcome,
)
from hostora.services.households import add_households_batch, list_households
from hostora.services.invites import prepare_invites
from hostora.services.summary import get_event_summary
from hostora.web.deps import get_broadcaster_dep, get_host_dep, get_settings_dep

router = APIRouter(tags=["events"])


class OutcomeInput(ApiModel):
    function_id: Annotated[str, Field(min_length=1, max_length=40)]
    actual_count: Annotated[int, Field(ge=0, le=100_000)] | None = None
    ran_short: list[str] = Field(default_factory=list)
    leftover: list[str] = Field(default_factory=list)
    item_outcomes: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/v1/events", response_model=CreatedEvent, status_code=201)
async def api_create_event(
    body: CreateEventInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> CreatedEvent:
    return await create_event(session, host, body)


@router.get("/v1/events")
async def api_list_events(
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    events = await list_host_events(session, host)
    return {"events": events}


@router.get("/v1/events/{event_key}")
async def api_get_event(
    event_key: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    return await get_event_detail(session, event_key, host)


@router.post("/v1/events/{event_key}/households:batch")
async def api_add_households_batch(
    event_key: str,
    body: HouseholdBatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    created = await add_households_batch(session, event_key, host, body, settings)
    return {"households": created}


@router.get("/v1/events/{event_key}/households")
async def api_list_households(
    event_key: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    rows = await list_households(session, event_key, host, settings)
    return {"households": rows}


@router.post("/v1/events/{event_key}/invites:prepare", response_model=list[PreparedInvite])
async def api_prepare_invites(
    event_key: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> list[PreparedInvite]:
    return await prepare_invites(session, event_key, host, settings)


@router.get("/v1/events/{event_key}/summary", response_model=EventSummary)
async def api_get_summary(
    event_key: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> EventSummary:
    await require_event_access(session, event_key, host, need="view")
    return await get_event_summary(session, event_key)


@router.get("/v1/events/{event_key}/live")
async def api_event_live(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
    broadcaster: Annotated[Broadcaster, Depends(get_broadcaster_dep)],
    fmt: Annotated[str, Query(alias="format")] = "json",
) -> EventSourceResponse:
    async with session.begin():
        await require_event_access(session, event_key, host, need="view")

    async def _stream() -> AsyncIterator[dict[str, str]]:
        async with broadcaster.subscribe(event_key) as q:
            while True:
                try:
                    await asyncio.wait_for(q.get(), timeout=15.0)
                except TimeoutError:
                    continue
                await asyncio.sleep(0.15)
                while not q.empty():
                    q.get_nowait()

                async with (
                    request.app.state.sessionmaker() as fresh_session,
                    fresh_session.begin(),
                ):
                    summary = await get_event_summary(fresh_session, event_key)

                templates = getattr(request.app.state, "templates", None)
                if fmt == "html" and templates is not None:
                    tmpl = templates.env.get_template("host/_summary.html")
                    data_str = tmpl.render(
                        request=request,
                        summary=summary,
                        event_key=event_key,
                    )
                else:
                    data_str = summary.model_dump_json(by_alias=True)
                yield {"event": "summary", "data": data_str}

    return EventSourceResponse(_stream(), ping=15)


@router.post("/v1/events/{event_key}/outcome")
async def api_record_outcome(
    event_key: str,
    body: OutcomeInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    return await record_outcome(
        session,
        event_key,
        host,
        function_id=body.function_id,
        actual_count=body.actual_count,
        ran_short=body.ran_short,
        leftover=body.leftover,
        item_outcomes=body.item_outcomes,
        now=datetime.now(UTC),
    )


@router.delete("/v1/me", status_code=204)
async def api_delete_me(
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> Response:
    await delete_account(session, host)
    return Response(status_code=204)
