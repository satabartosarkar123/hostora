from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.db.session import get_session
from hostora.errors import ProblemError
from hostora.schemas.plan import PlanRequest
from hostora.security.csrf import verify_csrf
from hostora.security.firebase import HostIdentity
from hostora.services.events import get_event_detail
from hostora.services.plans import (
    create_plan_run,
    get_latest_plan_for_function,
    update_plan_item_purchase,
)
from hostora.web.i18n import resolve_language
from hostora.web.templating import template_context

router = APIRouter(tags=["planner-pages"])


async def _require_page_host(request: Request) -> HostIdentity | None:
    uid = request.session.get("uid")
    if not isinstance(uid, str) or not uid:
        return None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        await verify_csrf(request)
    name = request.session.get("name")
    return HostIdentity(uid=uid, name=name if isinstance(name, str) else None)


@router.get("/app/e/{event_key}/plan/{function_id}")
async def host_planner_page(
    event_key: str,
    function_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    detail = await get_event_detail(session, event_key, host)
    fn_obj = next((f for f in detail["functions"] if f["id"] == function_id), None)
    if fn_obj is None:
        raise ProblemError(404, "Function not found")

    latest_plan, purchases = await get_latest_plan_for_function(session, function_id)
    stream_plan_id = request.query_params.get("plan_id")
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/planner.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            event=detail,
            event_key=event_key,
            function=fn_obj,
            function_id=function_id,
            latest_plan=latest_plan,
            purchases=purchases,
            stream_plan_id=stream_plan_id,
        ),
    )
    return resp


@router.post("/app/e/{event_key}/plan/{function_id}")
async def host_generate_plan_page(
    event_key: str,
    function_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    form = await request.form()
    brief = str(form.get("brief") or "").strip()[:1000]
    days_left_raw = str(form.get("days_left") or "").strip()
    days_left: int | None = None
    if days_left_raw != "":
        try:
            days_left = max(0, min(365, int(days_left_raw)))
        except ValueError:
            days_left = None

    plan_res = await create_plan_run(
        session,
        event_key,
        host,
        PlanRequest(function_id=function_id, brief=brief, days_left=days_left),
    )
    plan_id = plan_res["planId"]

    if request.headers.get("hx-request") == "true":
        html = (
            f'<div id="plan-stream-root" hx-ext="sse" '
            f'sse-connect="/v1/plans/{plan_id}/stream" '
            f'sse-swap="stage,category,done,error">'
            f'<div data-sse-target="stage"><div class="card">Starting planner...</div></div>'
            f'<div data-sse-target="category" id="streamed-categories"></div>'
            f'<div data-sse-target="done" id="streamed-done"></div>'
            f'<div data-sse-target="error"></div>'
            f"</div>"
        )
        return Response(content=html, media_type="text/html")

    return RedirectResponse(
        url=f"/app/e/{event_key}/plan/{function_id}?plan_id={plan_id}",
        status_code=303,
    )


@router.post("/app/e/{event_key}/plan/{plan_id}/items/{item_id}/toggle")
async def host_toggle_purchase_page(
    event_key: str,
    plan_id: str,
    item_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    form = await request.form()
    bought = str(form.get("bought") or "").lower() in ("true", "1", "on", "yes")
    actual_raw = str(form.get("actual_inr") or "").strip()
    actual_inr: int | None = None
    if actual_raw:
        try:
            actual_inr = int(actual_raw)
        except ValueError:
            actual_inr = None

    await update_plan_item_purchase(
        session,
        plan_id,
        item_id,
        host,
        bought=bought,
        actual_inr=actual_inr,
    )
    if request.headers.get("hx-request") == "true":
        badge = (
            '<span class="badge badge--success">Bought ✓</span>'
            if bought
            else '<span class="badge">Not bought</span>'
        )
        return Response(content=badge, media_type="text/html")
    return RedirectResponse(url=f"/app/e/{event_key}/budget", status_code=303)


@router.get("/app/e/{event_key}/budget")
async def host_budget_page(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    detail = await get_event_detail(session, event_key, host)

    by_category: dict[str, dict[str, int]] = {}
    fn_plans: list[dict[str, Any]] = []  # Any: template dicts
    total_planned = 0
    total_bought = 0

    for fn in detail["functions"]:
        plan_obj, purchases = await get_latest_plan_for_function(session, str(fn["id"]))
        if plan_obj is None:
            continue
        fn_plans.append({"function": fn, "plan": plan_obj, "purchases": purchases})
        for item in plan_obj.items:
            cat_entry = by_category.setdefault(
                item.category, {"planned_inr": 0, "bought_inr": 0, "count": 0}
            )
            cat_entry["planned_inr"] += item.est_cost_inr
            cat_entry["count"] += 1
            total_planned += item.est_cost_inr
            p = purchases.get(item.item_id)
            if p and p.bought:
                spent = p.actual_inr if p.actual_inr is not None else item.est_cost_inr
                cat_entry["bought_inr"] += spent
                total_bought += spent

    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/budget.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            event=detail,
            event_key=event_key,
            by_category=by_category,
            fn_plans=fn_plans,
            total_planned=total_planned,
            total_bought=total_bought,
        ),
    )
    return resp
