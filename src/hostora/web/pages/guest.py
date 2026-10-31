import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import segno
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.data.loader import get_data
from hostora.db.session import get_session
from hostora.engine.codes import verify_link_token
from hostora.errors import ProblemError
from hostora.realtime.broadcaster import Broadcaster
from hostora.schemas.common import Attending, Language, RuleId
from hostora.schemas.rsvp import Meals, RsvpFunction, RsvpInput
from hostora.security.ratelimit import client_ip
from hostora.services.invites import (
    get_guest_invitation,
    resolve_guest_code,
)
from hostora.services.rsvp import submit_rsvp
from hostora.web.deps import get_broadcaster_dep, get_settings_dep
from hostora.web.i18n import resolve_language, translate
from hostora.web.templating import template_context

router = APIRouter(tags=["guest-pages"])


def _apply_lang_cookie(request: Request, response: Response, lang: str) -> Response:
    if request.query_params.get("lang") in ("en", "hi"):
        response.set_cookie("lang", lang, max_age=30 * 24 * 3600, samesite="lax")
    return response


def _int_val(raw: object, default: int = 0) -> int:
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return default
    return default


@router.get("/")
async def landing_page(request: Request) -> Response:
    lang = resolve_language(request)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "guest/landing.html",
        template_context(request, lang=lang),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.get("/r")
async def enter_code_page(request: Request) -> Response:
    lang = resolve_language(request)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "guest/code.html",
        template_context(request, lang=lang, error_message=None, code_value=""),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.post("/r")
async def submit_code_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    lang = resolve_language(request)
    templates = request.app.state.templates
    form = await request.form()
    raw_code = str(form.get("code") or "").strip()

    try:
        resolved = await resolve_guest_code(
            session,
            raw_code,
            client_ip(request),
            settings,
            datetime.now(UTC),
        )
    except ProblemError as exc:
        if exc.status == 429:
            msg = translate("code.error.rate_limit", lang=lang)
            status_code = 429
        else:
            msg = translate("code.error.not_found", lang=lang)
            status_code = 404
        resp: Response = templates.TemplateResponse(
            request,
            "guest/code.html",
            template_context(request, lang=lang, error_message=msg, code_value=raw_code),
            status_code=status_code,
        )
        return resp

    return RedirectResponse(
        url=f"/i/{resolved['eventKey']}/{resolved['token']}",
        status_code=303,
    )


@router.get("/i/{event_key}/{token}")
async def guest_invite_page(
    event_key: str,
    token: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    invitation = await get_guest_invitation(session, event_key, token, settings)
    hh_lang = str(invitation["household"].get("language") or "en")
    lang = resolve_language(request, fallback=hh_lang)
    data = get_data()
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "guest/invite.html",
        template_context(
            request,
            lang=lang,
            invitation=invitation,
            token=token,
            all_rules=data.rules,
            idempotency_key=str(uuid.uuid4()),
            error_message=None,
            error_field=None,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.post("/i/{event_key}/{token}")
async def guest_submit_rsvp_page(
    event_key: str,
    token: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    broadcaster: Annotated[Broadcaster, Depends(get_broadcaster_dep)],
) -> Response:
    household_key = verify_link_token(
        settings.token_hmac_secret.get_secret_value(), event_key, token
    )
    if household_key is None:
        raise ProblemError(404, "Invitation not found")
    form = await request.form()
    lang_raw = str(form.get("language") or "en")
    lang = lang_raw if lang_raw in ("en", "hi") else "en"
    idem_key = str(form.get("idempotency_key") or uuid.uuid4())
    if len(idem_key) < 8:
        idem_key = f"form-{uuid.uuid4()}"

    fn_ids = [str(v) for v in form.getlist("function_ids") if str(v)]
    rsvp_fns: list[RsvpFunction] = []
    form_replies: dict[str, dict[str, Any]] = {}  # Any: template repopulate state

    try:
        for fid in fn_ids:
            att_str = str(form.get(f"attending_{fid}") or "yes")
            adults = _int_val(form.get(f"adults_{fid}"), 0)
            children = _int_val(form.get(f"children_{fid}"), 0)
            veg = _int_val(form.get(f"veg_{fid}"), 0)
            jain = _int_val(form.get(f"jain_{fid}"), 0)
            non_veg = _int_val(form.get(f"non_veg_{fid}"), 0)
            rules_raw = [str(r) for r in form.getlist(f"rules_{fid}") if str(r)]
            allergies_str = str(form.get(f"allergies_{fid}") or "")
            allergies = [a.strip()[:30] for a in allergies_str.split(",") if a.strip()][:10]
            arrival = str(form.get(f"arrival_{fid}") or "").strip() or None

            form_replies[fid] = {
                "functionId": fid,
                "attending": att_str,
                "adults": adults,
                "children": children,
                "meals": {"veg": veg, "jain": jain, "nonVeg": non_veg},
                "dietaryRules": rules_raw,
                "allergies": allergies,
                "arrivalWindow": arrival or "on_time",
            }

            rsvp_fns.append(
                RsvpFunction(
                    function_id=fid,
                    attending=Attending(att_str),
                    adults=adults,
                    children=children,
                    meals=Meals(veg=veg, jain=jain, non_veg=non_veg),
                    dietary_rules=[RuleId(r) for r in rules_raw],
                    allergies=allergies,
                    arrival_window=arrival,
                )
            )

        rsvp_input = RsvpInput(functions=rsvp_fns, language=Language(lang))
        await submit_rsvp(
            session,
            broadcaster,
            event_key=event_key,
            household_key=household_key,
            body=rsvp_input,
            idempotency_key=idem_key[:64],
            signing_key_hex=settings.pass_signing_key.get_secret_value(),
            now=datetime.now(UTC),
        )
    except (ProblemError, ValidationError, ValueError) as exc:
        if isinstance(exc, ProblemError) and exc.status == 404:
            raise
        error_msg = exc.title if isinstance(exc, ProblemError) else "Please check your entries"
        error_field = exc.field if isinstance(exc, ProblemError) else None
        invitation = await get_guest_invitation(session, event_key, token, settings)
        data = get_data()
        templates = request.app.state.templates
        err_resp: Response = templates.TemplateResponse(
            request,
            "guest/invite.html",
            template_context(
                request,
                lang=lang,
                invitation=invitation,
                token=token,
                all_rules=data.rules,
                idempotency_key=str(uuid.uuid4()),
                error_message=error_msg,
                error_field=error_field,
                form_replies=form_replies,
            ),
            status_code=422,
        )
        return err_resp

    return RedirectResponse(url=f"/i/{event_key}/{token}/done", status_code=303)


@router.get("/i/{event_key}/{token}/done")
async def guest_done_page(
    event_key: str,
    token: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    invitation = await get_guest_invitation(session, event_key, token, settings)
    hh_lang = str(invitation["household"].get("language") or "en")
    lang = resolve_language(request, fallback=hh_lang)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "guest/done.html",
        template_context(
            request,
            lang=lang,
            invitation=invitation,
            token=token,
        ),
    )
    return _apply_lang_cookie(request, resp, lang)


@router.get("/i/{event_key}/{token}/calendar.ics")
async def guest_calendar_ics(
    event_key: str,
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    fn: Annotated[str | None, Query()] = None,
) -> Response:
    invitation = await get_guest_invitation(session, event_key, token, settings)
    functions: list[dict[str, Any]] = invitation["functions"]  # Any: function dict
    target_fn = next((f for f in functions if f["id"] == fn), None)
    if target_fn is None and functions:
        target_fn = functions[0]
    if target_fn is None:
        raise ProblemError(404, "Function not found")

    starts_dt = datetime.fromisoformat(str(target_fn["startsAt"])).astimezone(UTC)
    ends_dt = starts_dt + timedelta(hours=3)
    dt_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dt_start = starts_dt.strftime("%Y%m%dT%H%M%SZ")
    dt_end = ends_dt.strftime("%Y%m%dT%H%M%SZ")
    summary_txt = f"{invitation['eventTitle']} — {target_fn['name']}"
    loc_txt = str(invitation["venue"]["label"])
    uid_txt = f"{event_key}-{target_fn['id']}@hostora"

    ics = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Hostora//Event Invitation//EN",
            "CALSCALE:GREGORIAN",
            "BEGIN:VEVENT",
            f"UID:{uid_txt}",
            f"DTSTAMP:{dt_stamp}",
            f"DTSTART:{dt_start}",
            f"DTEND:{dt_end}",
            f"SUMMARY:{summary_txt}",
            f"LOCATION:{loc_txt}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
    fname = f"hostora-{event_key}-{target_fn['id']}.ics"
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/i/{event_key}/{token}/pass/{pass_id}.svg")
async def guest_pass_svg(
    event_key: str,
    token: str,
    pass_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    invitation = await get_guest_invitation(session, event_key, token, settings)
    passes: list[dict[str, Any]] = invitation["passes"]  # Any: pass dict
    pass_obj = next((p for p in passes if p["passId"] == pass_id), None)
    if pass_obj is None:
        raise ProblemError(404, "Invitation not found")

    qr = segno.make(str(pass_obj["token"]), error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, dark="#000000", light="#ffffff")
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@router.get("/privacy")
async def privacy_page(request: Request) -> Response:
    lang = resolve_language(request)
    privacy_path = Path("docs/privacy.md")
    if privacy_path.is_file():
        privacy_text = privacy_path.read_text(encoding="utf-8")
    else:
        privacy_text = "Hostora non-commercial beta privacy notice."
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "guest/privacy.html",
        template_context(request, lang=lang, privacy_text=privacy_text),
    )
    return _apply_lang_cookie(request, resp, lang)
