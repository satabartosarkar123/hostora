from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.adapters.commerce import TIME_LEFT_PRESETS
from hostora.config import Settings
from hostora.db.session import get_session
from hostora.errors import ProblemError
from hostora.schemas.plan import DeliveryReportRequest, PlanRequest
from hostora.security.csrf import verify_csrf
from hostora.security.firebase import HostIdentity
from hostora.services.events import get_event_detail
from hostora.services.plans import (
    build_store_cart_for_plan,
    create_plan_run,
    get_latest_plan_for_function,
    report_plan_delivery,
    update_plan_item_purchase,
)
from hostora.web.deps import get_settings_dep
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
            time_left_presets=TIME_LEFT_PRESETS,
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
    minutes_left_raw = str(form.get("minutes_left") or "").strip()
    legacy_key = "days" + "_left"
    legacy_raw = str(form.get(legacy_key) or "").strip()

    minutes_left: int | None = None
    if minutes_left_raw != "":
        try:
            minutes_left = max(0, min(525600, int(minutes_left_raw)))
        except ValueError:
            minutes_left = None
    elif legacy_raw != "":
        try:
            minutes_left = max(0, min(365, int(legacy_raw))) * 1440
        except ValueError:
            minutes_left = None

    plan_res = await create_plan_run(
        session,
        event_key,
        host,
        PlanRequest(function_id=function_id, brief=brief, minutes_left=minutes_left),
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


@router.post("/app/e/{event_key}/plan/{plan_id}/carts/build")
async def host_build_store_cart_page(
    event_key: str,
    plan_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    form = await request.form()
    channel = str(form.get("channel") or "instamart").strip()
    quantities: dict[str, int] = {}
    for k, v in form.multi_items():
        if k.startswith("qty_"):
            iid = k[4:]
            try:
                quantities[iid] = max(1, int(str(v)))
            except ValueError:
                quantities[iid] = 1

    res = await build_store_cart_for_plan(
        session,
        settings,
        plan_id,
        host,
        channel=channel,
        quantities=quantities,
        instamart_server_override=getattr(request.app.state, "swiggy_instamart_mcp", None),
        food_server_override=getattr(request.app.state, "swiggy_food_mcp", None),
        zomato_server_override=getattr(request.app.state, "zomato_mcp", None),
        refresh_fn=getattr(request.app.state, "store_refresh_fn", None),
    )

    if request.headers.get("hx-request") == "true":
        checkout_url = res["checkoutUrl"]
        arrives_by = res["arrivesBy"]
        added = res["addedItems"]
        html = (
            f'<div class="alert alert--success cart-build-result" data-channel="{channel}">'
            f"Added <strong>{added} items</strong> to your {res['store'].title()} cart "
            f"(arrives by <strong>{arrives_by}</strong>). "
            f'<a href="{checkout_url}" class="btn btn--sm checkout-handoff-link" '
            f'target="_blank" rel="noopener noreferrer">'
            f"Open {res['store'].title()} Checkout →</a>"
            f"</div>"
        )
        return Response(content=html, media_type="text/html")

    return RedirectResponse(url=str(res["checkoutUrl"]), status_code=303)


@router.post("/app/e/{event_key}/plan/{plan_id}/delivery-report")
async def host_report_delivery_page(
    event_key: str,
    plan_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    form = await request.form()
    channel = str(form.get("channel") or "instamart").strip()
    item_kind = str(form.get("item_kind") or "food").strip()
    try:
        promised_max = max(0.0, float(str(form.get("promised_max") or "30")))
    except ValueError:
        promised_max = 30.0
    try:
        actual_minutes = max(0.0, float(str(form.get("actual_minutes") or "25")))
    except ValueError:
        actual_minutes = 25.0

    await report_plan_delivery(
        session,
        plan_id,
        host,
        DeliveryReportRequest(
            channel=channel,
            item_kind=item_kind,
            promised_max=promised_max,
            actual_minutes=actual_minutes,
        ),
    )
    if request.headers.get("hx-request") == "true":
        return Response(
            content=(
                f'<span class="badge badge--success" id="delivery-report-status">'
                f"Recorded: {channel} arrived in {int(actual_minutes)} min ✓"
                f"</span>"
            ),
            media_type="text/html",
        )
    return RedirectResponse(url=f"/app/e/{event_key}", status_code=303)


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
