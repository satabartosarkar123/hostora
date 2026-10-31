from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.db.session import get_session
from hostora.engine.codes import verify_link_token
from hostora.errors import ProblemError
from hostora.realtime.broadcaster import Broadcaster
from hostora.schemas.rsvp import ResolveCodeInput, RsvpInput, RsvpResult
from hostora.security.ratelimit import client_ip
from hostora.services.invites import (
    get_guest_invitation,
    resolve_guest_code,
)
from hostora.services.rsvp import submit_rsvp
from hostora.web.deps import get_broadcaster_dep, get_settings_dep

router = APIRouter(tags=["invites"])


@router.get("/v1/invites/{event_key}/{token}")
async def api_get_invitation(
    event_key: str,
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    return await get_guest_invitation(session, event_key, token, settings)


@router.post("/v1/invites:resolve")
async def api_resolve_code(
    body: ResolveCodeInput,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, str]:
    return await resolve_guest_code(
        session,
        body.code,
        client_ip(request),
        settings,
        datetime.now(UTC),
    )


@router.put("/v1/invites/{event_key}/{token}/rsvp", response_model=RsvpResult)
async def api_submit_rsvp(
    event_key: str,
    token: str,
    body: RsvpInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    broadcaster: Annotated[Broadcaster, Depends(get_broadcaster_dep)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RsvpResult:
    if not idempotency_key or not (8 <= len(idempotency_key) <= 64):
        raise ProblemError(
            422,
            "Idempotency-Key header (8-64 chars) is required",
            field="idempotencyKey",
        )
    household_key = verify_link_token(
        settings.token_hmac_secret.get_secret_value(), event_key, token
    )
    if household_key is None:
        raise ProblemError(404, "Invitation not found")
    return await submit_rsvp(
        session,
        broadcaster,
        event_key=event_key,
        household_key=household_key,
        body=body,
        idempotency_key=idempotency_key,
        signing_key_hex=settings.pass_signing_key.get_secret_value(),
        now=datetime.now(UTC),
    )
