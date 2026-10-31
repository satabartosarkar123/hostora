import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from hostora.adapters.llm.router import LlmRouter
from hostora.db.session import get_session
from hostora.planner.pipeline import run_planner_stream
from hostora.schemas.common import ApiModel
from hostora.schemas.plan import (
    ConfirmItemRequest,
    Plan,
    PlanRequest,
    ReplaceItemRequest,
)
from hostora.security.firebase import HostIdentity
from hostora.services.plans import (
    confirm_plan_item,
    create_plan_run,
    get_plan_for_host,
    remove_plan_item,
    replace_plan_item,
    update_plan_item_purchase,
    verify_plan_access,
)
from hostora.web.deps import get_host_dep, get_llm_router_dep

router = APIRouter(tags=["plans"])


class PatchPurchaseInput(ApiModel):
    bought: bool
    actual_inr: Annotated[int, Field(ge=0, le=10_000_000)] | None = None


@router.post("/v1/events/{event_key}/plans", status_code=202)
async def api_create_plan(
    event_key: str,
    body: PlanRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> dict[str, str]:
    return await create_plan_run(session, event_key, host, body)


@router.get("/v1/plans/{plan_id}/stream")
async def api_stream_plan(
    plan_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
    llm_router: Annotated[LlmRouter, Depends(get_llm_router_dep)],
    fmt: Annotated[str, Query(alias="format")] = "html",
) -> EventSourceResponse:
    await verify_plan_access(session, plan_id, host, need="view")
    templates = getattr(request.app.state, "templates", None)

    async def _stream() -> AsyncIterator[dict[str, str]]:
        async with request.app.state.sessionmaker() as run_session:
            async for evt_name, payload in run_planner_stream(
                session=run_session,
                plan_id=plan_id,
                router=llm_router,
            ):
                if fmt == "json" or templates is None:
                    if evt_name == "done" and isinstance(payload, Plan):
                        data_str = json.dumps(
                            {"plan": payload.model_dump(by_alias=True, mode="json")}
                        )
                    elif hasattr(payload, "model_dump"):
                        data_str = json.dumps(payload.model_dump(by_alias=True, mode="json"))
                    else:
                        data_str = json.dumps(jsonable_encoder(payload))
                else:
                    if evt_name == "stage":
                        tmpl = templates.env.get_template("host/_plan_stage.html")
                        data_str = tmpl.render(stage=payload, done_plan=None)
                    elif evt_name == "done" and isinstance(payload, Plan):
                        tmpl = templates.env.get_template("host/_plan_stage.html")
                        data_str = tmpl.render(stage=None, done_plan=payload)
                    else:
                        data_str = json.dumps(
                            payload if isinstance(payload, dict) else str(payload)
                        )

                yield {"event": evt_name, "data": data_str}
                if evt_name == "done":
                    yield {"event": "plan", "data": data_str}

    return EventSourceResponse(_stream(), ping=15)


@router.get("/v1/plans/{plan_id}", response_model=Plan)
async def api_get_plan(
    plan_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> Plan:
    _, _, plan_obj = await get_plan_for_host(session, plan_id, host)
    return plan_obj


@router.patch("/v1/plans/{plan_id}/items/{item_id}")
async def api_patch_plan_item(
    plan_id: str,
    item_id: str,
    body: PatchPurchaseInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> dict[str, Any]:
    return await update_plan_item_purchase(
        session,
        plan_id,
        item_id,
        host,
        bought=body.bought,
        actual_inr=body.actual_inr,
    )


@router.post("/v1/plans/{plan_id}/items/{item_id}:confirm", response_model=Plan)
async def api_confirm_plan_item(
    plan_id: str,
    item_id: str,
    body: ConfirmItemRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> Plan:
    return await confirm_plan_item(
        session,
        plan_id,
        item_id,
        host,
        confirmations=body.confirmations,
    )


@router.post("/v1/plans/{plan_id}/items/{item_id}:replace", response_model=Plan)
async def api_replace_plan_item(
    plan_id: str,
    item_id: str,
    body: ReplaceItemRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> Plan:
    return await replace_plan_item(
        session,
        plan_id,
        item_id,
        host,
        alternative_index=body.alternative_index,
        name=body.name,
    )


@router.post("/v1/plans/{plan_id}/items/{item_id}:remove", response_model=Plan)
async def api_remove_plan_item(
    plan_id: str,
    item_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    host: Annotated[HostIdentity, Depends(get_host_dep)],
) -> Plan:
    return await remove_plan_item(
        session,
        plan_id,
        item_id,
        host,
    )
