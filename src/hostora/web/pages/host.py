import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import segno
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.data.loader import get_data
from hostora.db.models import Event
from hostora.db.session import get_session
from hostora.errors import ProblemError
from hostora.schemas.common import EventType, GateMode, Language, Meal, RuleId
from hostora.schemas.events import (
    CreateEventInput,
    FunctionInput,
    HouseholdBatch,
    HouseholdInput,
    Venue,
)
from hostora.security.csrf import verify_csrf
from hostora.security.firebase import HostIdentity
from hostora.services.accounts import delete_account
from hostora.services.events import (
    create_event,
    get_event_detail,
    list_host_events,
)
from hostora.services.households import add_households_batch, list_households
from hostora.services.invites import mark_invite_sent, prepare_invites
from hostora.services.learning import (
    get_insights_data,
    record_preference_comparison,
    save_event_rule,
)
from hostora.web.deps import get_settings_dep
from hostora.web.i18n import resolve_language
from hostora.web.templating import template_context

router = APIRouter(tags=["host-pages"])


async def _require_page_host(request: Request) -> HostIdentity | None:
    uid = request.session.get("uid")
    if not isinstance(uid, str) or not uid:
        return None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        await verify_csrf(request)
    name = request.session.get("name")
    return HostIdentity(uid=uid, name=name if isinstance(name, str) else None)


def _apply_lang_cookie(request: Request, response: Response, lang: str) -> Response:
    if request.query_params.get("lang") in ("en", "hi"):
        response.set_cookie("lang", lang, max_age=30 * 24 * 3600, samesite="lax")
    return response


def _parse_datetime(raw: str, fallback: datetime) -> datetime:
    val = raw.strip()
    if not val:
        return fallback
    try:
        dt = datetime.fromisoformat(val)
    except ValueError:
        return fallback
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def parse_household_csv(
    csv_text: str,
    functions_by_name: dict[str, str],
    all_function_ids: list[str],
) -> tuple[list[HouseholdInput], list[dict[str, Any]]]:  # Any: row preview dicts
    valid_inputs: list[HouseholdInput] = []
    preview_rows: list[dict[str, Any]] = []

    stream = io.StringIO(csv_text.strip())
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        return [], [{"row": 1, "name": "", "error": "CSV is empty or missing headers"}]

    for idx, row in enumerate(reader, start=2):
        name = str(row.get("name") or "").strip()
        phone_raw = str(row.get("phone") or "").strip() or None
        seats_raw = str(row.get("seats") or "2").strip()
        fns_raw = str(row.get("functions") or "").strip()
        lang_raw = str(row.get("language") or "en").strip().lower()
        lang = Language.HI if lang_raw == "hi" else Language.EN

        errors: list[str] = []
        if not name:
            errors.append("Name is required")

        try:
            seat_cap = int(seats_raw)
            if not (1 <= seat_cap <= 20):
                errors.append("Seats must be between 1 and 20")
        except ValueError:
            seat_cap = 0
            errors.append("Seats must be an integer")

        fn_ids: list[str] = []
        if not fns_raw or fns_raw == "*":
            fn_ids = list(all_function_ids)
        else:
            parts = [p.strip() for p in fns_raw.split(";") if p.strip()]
            for part in parts:
                matched = functions_by_name.get(part.lower())
                if matched:
                    fn_ids.append(matched)
                elif part in all_function_ids:
                    fn_ids.append(part)
                else:
                    errors.append(f"Unknown function '{part}'")
        if not fn_ids and not errors:
            errors.append("At least one function is required")

        if not errors:
            try:
                hh_in = HouseholdInput(
                    display_name=name,
                    phone=phone_raw,
                    seat_cap=seat_cap,
                    function_ids=fn_ids,
                    language=lang,
                )
                valid_inputs.append(hh_in)
            except ValidationError as exc:
                errors.append(exc.errors()[0].get("msg", "Invalid row"))

        preview_rows.append(
            {
                "row": idx,
                "name": name,
                "phone": phone_raw or "",
                "seats": seats_raw,
                "functions": fns_raw or "All",
                "error": "; ".join(errors) if errors else None,
            }
        )

    return valid_inputs, preview_rows


@router.get("/app")
async def host_events_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    events = await list_host_events(session, host)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/events.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            events=events,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.get("/app/events/new")
async def host_new_event_page(request: Request) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    data = get_data()
    traditions = data.traditions.get("traditions", {})
    default_date = (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%dT18:00")
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/event_new.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            traditions=traditions,
            traditions_json=json.dumps(traditions),
            all_rules=data.rules,
            default_date=default_date,
            error_message=None,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.post("/app/events/new")
async def host_create_event_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    form = await request.form()

    title = str(form.get("title") or "").strip()
    event_type_raw = str(form.get("type") or "wedding").strip()
    tradition = str(form.get("tradition") or "none").strip()
    venue_label = str(form.get("venue_label") or "").strip()
    pincode_raw = str(form.get("pincode") or "").strip() or None
    try:
        venue_lat = float(str(form.get("venue_lat") or "12.9716"))
        venue_lng = float(str(form.get("venue_lng") or "77.5946"))
    except ValueError:
        venue_lat, venue_lng = 12.9716, 77.5946
    try:
        budget_inr = int(str(form.get("budget_inr") or "50000"))
    except ValueError:
        budget_inr = 50000

    fn_names = [str(v).strip() for v in form.getlist("fn_name")]
    fn_starts = [str(v).strip() for v in form.getlist("fn_starts_at")]
    fn_meals = [str(v).strip() for v in form.getlist("fn_meal")]
    fn_gates = [str(v).strip() for v in form.getlist("fn_gate_mode")]

    default_dt = datetime.now(UTC) + timedelta(days=7)
    functions: list[FunctionInput] = []
    for idx, fn_name in enumerate(fn_names):
        if not fn_name or (idx > 0 and fn_name == "Main event"):
            continue
        start_str = fn_starts[idx] if idx < len(fn_starts) else ""
        meal_str = fn_meals[idx] if idx < len(fn_meals) else "dinner"
        gate_str = fn_gates[idx] if idx < len(fn_gates) else "off"
        rules_raw = [str(r) for r in form.getlist(f"fn_rules_{idx}") if str(r)]
        starts_at = _parse_datetime(start_str, default_dt + timedelta(hours=idx * 4))
        meal = Meal(meal_str) if meal_str in Meal._value2member_map_ else Meal.DINNER
        gate_mode = GateMode(gate_str) if gate_str in GateMode._value2member_map_ else GateMode.OFF
        valid_rules = [RuleId(r) for r in rules_raw if r in RuleId._value2member_map_]
        functions.append(
            FunctionInput(
                name=fn_name,
                starts_at=starts_at,
                meal=meal,
                rules=valid_rules,
                gate_mode=gate_mode,
            )
        )

    try:
        ev_type = (
            EventType(event_type_raw)
            if event_type_raw in EventType._value2member_map_
            else EventType.WEDDING
        )
        body = CreateEventInput(
            title=title,
            type=ev_type,
            tradition=tradition or "none",
            venue=Venue(
                label=venue_label,
                lat=venue_lat,
                lng=venue_lng,
                pincode=pincode_raw,
            ),
            budget_inr=budget_inr,
            functions=functions,
        )
        created = await create_event(session, host, body)
    except (ValidationError, ProblemError) as exc:
        err_msg = exc.title if isinstance(exc, ProblemError) else str(exc.errors()[0].get("msg"))
        data = get_data()
        traditions = data.traditions.get("traditions", {})
        default_date = default_dt.strftime("%Y-%m-%dT18:00")
        templates = request.app.state.templates
        resp: Response = templates.TemplateResponse(
            request,
            "host/event_new.html",
            template_context(
                request,
                lang=lang,
                include_csrf=True,
                include_manifest=True,
                host=host,
                traditions=traditions,
                traditions_json=json.dumps(traditions),
                all_rules=data.rules,
                default_date=default_date,
                error_message=err_msg,
            ),
            status_code=422,
        )
        return resp

    return RedirectResponse(url=f"/app/e/{created.event_key}", status_code=303)


@router.get("/app/e/{event_key}")
async def host_dashboard_page(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    detail = await get_event_detail(session, event_key, host)
    from hostora.schemas.summary import EventSummary

    summary = EventSummary.model_validate(detail["summary"])

    demo_qr_svg: str | None = None
    if detail.get("isDemo"):
        join_url = f"{settings.public_origin.rstrip('/')}/demo/join"
        qr = segno.make(join_url, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="svg", scale=4, xmldecl=False)
        demo_qr_svg = buf.getvalue().decode("utf-8")

    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/dashboard.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            event=detail,
            event_key=event_key,
            summary=summary,
            demo_qr_svg=demo_qr_svg,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.get("/app/e/{event_key}/guests")
async def host_guests_page(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    detail = await get_event_detail(session, event_key, host)
    households = await list_households(session, event_key, host, settings)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/guests.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            event=detail,
            event_key=event_key,
            households=households,
            csv_preview=None,
            csv_raw="",
            error_message=None,
            added_count=None,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.post("/app/e/{event_key}/guests")
async def host_add_guest_page(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    form = await request.form()
    display_name = str(form.get("display_name") or "").strip()
    phone = str(form.get("phone") or "").strip() or None
    try:
        seat_cap = int(str(form.get("seat_cap") or "2"))
    except ValueError:
        seat_cap = 2
    hh_lang_str = str(form.get("language") or "en").strip()
    hh_lang = Language.HI if hh_lang_str == "hi" else Language.EN
    fn_ids = [str(f) for f in form.getlist("function_ids") if str(f)]

    try:
        hh_input = HouseholdInput(
            display_name=display_name,
            phone=phone,
            seat_cap=seat_cap,
            function_ids=fn_ids,
            language=hh_lang,
        )
        await add_households_batch(
            session,
            event_key,
            host,
            HouseholdBatch(households=[hh_input]),
            settings,
        )
    except (ValidationError, ProblemError) as exc:
        err_msg = exc.title if isinstance(exc, ProblemError) else str(exc.errors()[0].get("msg"))
        detail = await get_event_detail(session, event_key, host)
        households = await list_households(session, event_key, host, settings)
        templates = request.app.state.templates
        resp: Response = templates.TemplateResponse(
            request,
            "host/guests.html",
            template_context(
                request,
                lang=lang,
                include_csrf=True,
                include_manifest=True,
                host=host,
                event=detail,
                event_key=event_key,
                households=households,
                csv_preview=None,
                csv_raw="",
                error_message=err_msg,
                added_count=None,
            ),
            status_code=422,
        )
        return resp

    return RedirectResponse(url=f"/app/e/{event_key}/guests", status_code=303)


@router.post("/app/e/{event_key}/guests/csv")
async def host_import_csv_page(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    detail = await get_event_detail(session, event_key, host)
    form = await request.form()
    action = str(form.get("action") or "save").strip()

    csv_text = str(form.get("csv_text") or "").strip()
    upload = form.get("csv_file")
    if upload is not None and hasattr(upload, "read"):
        raw_bytes = await upload.read()
        if isinstance(raw_bytes, bytes) and raw_bytes.strip():
            csv_text = raw_bytes.decode("utf-8", errors="replace").strip()

    fns = detail["functions"]
    fns_by_name = {str(f["name"]).lower(): str(f["id"]) for f in fns}
    all_fn_ids = [str(f["id"]) for f in fns]

    valid_inputs, preview_rows = parse_household_csv(csv_text, fns_by_name, all_fn_ids)
    has_errors = any(r.get("error") for r in preview_rows)

    if action == "preview" or has_errors or not valid_inputs:
        households = await list_households(session, event_key, host, settings)
        templates = request.app.state.templates
        resp: Response = templates.TemplateResponse(
            request,
            "host/guests.html",
            template_context(
                request,
                lang=lang,
                include_csrf=True,
                include_manifest=True,
                host=host,
                event=detail,
                event_key=event_key,
                households=households,
                csv_preview=preview_rows,
                csv_raw=csv_text,
                error_message="Some rows have errors" if has_errors else None,
                added_count=None,
            ),
            status_code=422 if has_errors else 200,
        )
        return resp

    await add_households_batch(
        session,
        event_key,
        host,
        HouseholdBatch(households=valid_inputs),
        settings,
    )
    return RedirectResponse(url=f"/app/e/{event_key}/guests", status_code=303)


@router.get("/app/e/{event_key}/invite")
async def host_invite_page(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    detail = await get_event_detail(session, event_key, host)
    households = await list_households(session, event_key, host, settings)
    prepared = await prepare_invites(session, event_key, host, settings)
    prep_by_id = {p.household_id: p for p in prepared}

    rows: list[dict[str, Any]] = []
    sent_count = 0
    for hh in households:
        hid = str(hh["id"])
        p = prep_by_id.get(hid)
        is_sent = bool(hh.get("inviteSentAt"))
        if is_sent:
            sent_count += 1
        rows.append(
            {
                "household_id": hid,
                "display_name": hh["displayName"],
                "phone_masked": hh.get("phoneMasked"),
                "seat_cap": hh["seatCap"],
                "code": hh["code"],
                "link": p.link if p else "",
                "whatsapp_url": p.whatsapp_url if p else "",
                "invite_sent_at": hh.get("inviteSentAt"),
                "is_sent": is_sent,
            }
        )

    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/invite.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            event=detail,
            event_key=event_key,
            invite_rows=rows,
            sent_count=sent_count,
            total_count=len(rows),
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.post("/app/e/{event_key}/invite/{household_id}/sent")
async def host_mark_invite_sent(
    event_key: str,
    household_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    await mark_invite_sent(session, event_key, household_id, host)
    if request.headers.get("hx-request") == "true":
        return Response(
            content='<span class="badge badge--success">Sent ✓</span>',
            media_type="text/html",
        )
    return RedirectResponse(url=f"/app/e/{event_key}/invite", status_code=303)


@router.get("/app/settings")
async def host_settings_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    from hostora.adapters.stores import get_store_connection_status

    swiggy_status = await get_store_connection_status(session, settings, host.uid, "swiggy")
    zomato_status = await get_store_connection_status(session, settings, host.uid, "zomato")
    notice = request.query_params.get("notice")
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/settings.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            swiggy_status=swiggy_status,
            zomato_status=zomato_status,
            store_notice=notice,
            error_message=None,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.post("/app/settings/stores/{store}/connect")
async def host_connect_store_page(
    store: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    if store not in ("swiggy", "zomato"):
        raise ProblemError(404, "Store not found")

    from datetime import UTC, datetime, timedelta

    from hostora.adapters.stores import (
        StoreTokenData,
        build_oauth_authorize_url,
        generate_pkce_pair,
        get_store_config,
        get_store_connection_status,
        save_store_token,
    )

    cfg = get_store_config(store)
    status = await get_store_connection_status(session, settings, host.uid, store)
    if status == "not_approved_yet":
        return RedirectResponse(
            url=f"/app/settings?notice={store}_not_approved",
            status_code=303,
        )

    verifier, challenge = generate_pkce_pair()
    request.session[f"pkce_verifier_{store}"] = verifier

    form = await request.form()
    manual_token = str(form.get("access_token") or "").strip()
    if manual_token or settings.demo_mode or settings.env == "test":
        tok = manual_token or f"oauth_{store}_{host.uid}"
        token_data = StoreTokenData(
            access_token=tok,
            refresh_token=f"refresh_{store}_{host.uid}",
            scopes=["mcp:tools", "cart:write"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await save_store_token(session, settings, host.uid, store, token_data)
        await session.commit()
        return RedirectResponse(url=f"/app/settings?notice={store}_connected", status_code=303)

    redirect_uri = f"{settings.public_origin.rstrip('/')}/app/settings/stores/{store}/callback"
    auth_url = build_oauth_authorize_url(
        store,
        client_id=str(cfg.get("client_id", "hostora-local")),
        redirect_uri=redirect_uri,
        state=verifier[:16],
        code_challenge=challenge,
    )
    return RedirectResponse(url=auth_url, status_code=303)


@router.get("/app/settings/stores/{store}/callback")
async def host_connect_store_callback(
    store: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    if store not in ("swiggy", "zomato"):
        raise ProblemError(404, "Store not found")

    from hostora.adapters.stores import exchange_oauth_code, save_store_token

    code = str(request.query_params.get("code") or "").strip()
    verifier = str(request.session.pop(f"pkce_verifier_{store}", "") or "")
    if not code or not verifier:
        return RedirectResponse(url=f"/app/settings?notice={store}_oauth_error", status_code=303)

    redirect_uri = f"{settings.public_origin.rstrip('/')}/app/settings/stores/{store}/callback"
    token_data = await exchange_oauth_code(
        store,
        code=code,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
    )
    await save_store_token(session, settings, host.uid, store, token_data)
    await session.commit()
    return RedirectResponse(url=f"/app/settings?notice={store}_connected", status_code=303)


@router.post("/app/settings/stores/{store}/disconnect")
async def host_disconnect_store_page(
    store: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    if store not in ("swiggy", "zomato"):
        raise ProblemError(404, "Store not found")

    from hostora.adapters.stores import disconnect_store

    await disconnect_store(session, host.uid, store)
    await session.commit()
    return RedirectResponse(url=f"/app/settings?notice={store}_disconnected", status_code=303)


@router.post("/app/settings/delete")
async def host_delete_account_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    form = await request.form()
    confirm_text = str(form.get("confirm") or "").strip()
    if confirm_text != "DELETE":
        from hostora.adapters.stores import get_store_connection_status

        swiggy_status = await get_store_connection_status(session, settings, host.uid, "swiggy")
        zomato_status = await get_store_connection_status(session, settings, host.uid, "zomato")
        templates = request.app.state.templates
        resp: Response = templates.TemplateResponse(
            request,
            "host/settings.html",
            template_context(
                request,
                lang=lang,
                include_csrf=True,
                include_manifest=True,
                host=host,
                swiggy_status=swiggy_status,
                zomato_status=zomato_status,
                store_notice=None,
                error_message="Please type DELETE to confirm account deletion.",
            ),
            status_code=422,
        )
        return resp

    await delete_account(session, host)
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@router.post("/app/e/{event_key}/slider")
async def host_update_slider(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    form = await request.form()
    raw_val = str(form.get("slider") or form.get("short_waste_slider") or "5")
    val = max(1, min(10, int(raw_val)))

    stmt = sa_update(Event).where(Event.event_key == event_key).values(short_waste_slider=val)
    await session.execute(stmt)
    await session.commit()

    if request.headers.get("hx-request") == "true":
        return Response(
            f'<span class="badge badge--success" id="slider-status">Saved (ratio: {val}/10)</span>',
            media_type="text/html",
        )
    return RedirectResponse(url=f"/app/e/{event_key}", status_code=303)


@router.get("/app/insights")
async def app_insights_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    lang = resolve_language(request)
    insights = await get_insights_data(session)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/insights.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            host=host,
            insights=insights,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.post("/app/e/{event_key}/rules/custom")
async def host_add_custom_rule(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return RedirectResponse(url="/app/login", status_code=303)
    form = await request.form()
    function_id = str(form.get("function_id") or "")
    rule_name = str(form.get("rule_name") or "custom_rule").strip()
    forbid_tag = str(form.get("forbid_tag") or "").strip().lower()
    if function_id and forbid_tag:
        await save_event_rule(
            session,
            event_key=event_key,
            function_id=function_id,
            rule_id=f"custom_{rule_name}",
            forbid=[forbid_tag],
            origin="host_edit",
        )
        await session.commit()
    return RedirectResponse(url=f"/app/e/{event_key}", status_code=303)


@router.post("/app/e/{event_key}/budget/preference")
async def host_record_budget_preference(
    event_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    host = await _require_page_host(request)
    if host is None:
        return Response("Unauthorized", status_code=401)
    data = await request.json()
    winner = str(data.get("winner") or "").strip()
    loser = str(data.get("loser") or "").strip()
    if winner and loser:
        await record_preference_comparison(
            session,
            event_key=event_key,
            winner=winner,
            loser=loser,
            source="drag_rank",
        )
        await session.commit()
    return Response(json.dumps({"ok": True}), media_type="application/json")
