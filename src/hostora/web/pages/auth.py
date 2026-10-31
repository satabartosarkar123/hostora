import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.db.models import Host
from hostora.db.session import get_session
from hostora.errors import ProblemError
from hostora.schemas.common import ApiModel
from hostora.security.csrf import ensure_csrf_token, verify_csrf
from hostora.security.firebase import verify_firebase_token
from hostora.web.deps import get_settings_dep
from hostora.web.i18n import resolve_language
from hostora.web.templating import template_context

router = APIRouter(tags=["auth"])


class SessionLoginInput(ApiModel):
    id_token: Annotated[str, Field(min_length=1)]


async def _upsert_host(session: AsyncSession, uid: str, display_name: str | None) -> None:
    now = datetime.now(UTC)
    async with session.begin():
        stmt = pg_insert(Host).values(uid=uid, display_name=display_name, created_at=now)
        if display_name:
            stmt = stmt.on_conflict_do_update(
                index_elements=[Host.uid],
                set_={"display_name": display_name},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=[Host.uid])
        await session.execute(stmt)


@router.get("/app/login")
async def login_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    if request.session.get("uid"):
        return RedirectResponse(url="/app", status_code=303)
    lang = resolve_language(request)
    templates = request.app.state.templates
    resp: Response = templates.TemplateResponse(
        request,
        "host/login.html",
        template_context(
            request,
            lang=lang,
            include_csrf=True,
            include_manifest=True,
            firebase_config={
                "apiKey": settings.firebase_api_key,
                "authDomain": settings.firebase_auth_domain,
                "projectId": settings.firebase_project_id,
                "appId": settings.firebase_app_id,
            },
        ),
    )
    return resp


@router.post("/auth/session", status_code=204)
async def create_session(
    body: SessionLoginInput,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    identity = await verify_firebase_token(body.id_token, settings.firebase_project_id)
    await _upsert_host(session, identity.uid, identity.name)
    request.session.clear()
    request.session["uid"] = identity.uid
    request.session["name"] = identity.name or "Host"
    request.session["csrf"] = secrets.token_urlsafe(24)
    return Response(status_code=204)


@router.post("/auth/demo")
async def create_demo_session(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    if not settings.demo_mode:
        raise ProblemError(404, "Not found")
    await _upsert_host(session, "demo-host", "Arjun & Sara")
    request.session.clear()
    request.session["uid"] = "demo-host"
    request.session["name"] = "Arjun & Sara"
    ensure_csrf_token(request)
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return Response(status_code=204)
    return RedirectResponse(url="/app", status_code=303)


@router.post("/auth/logout")
async def logout_session(request: Request) -> Response:
    if request.session.get("uid"):
        await verify_csrf(request)
    request.session.clear()
    return RedirectResponse(url="/app/login", status_code=303)
