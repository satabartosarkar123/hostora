from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from hostora.adapters.whatsapp import build_whatsapp_url
from hostora.db.models import Event, Fn, Host, Household, Invitation, Pass, Rsvp
from hostora.engine.codes import (
    format_household_code,
    make_link_token,
    split_household_code,
    verify_link_token,
)
from hostora.errors import ProblemError
from hostora.schemas.events import PreparedInvite
from hostora.security.crypto import decrypt_phone, ip_hash
from hostora.security.ratelimit import hit
from hostora.security.session import require_event_access

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.config import Settings
    from hostora.security.firebase import HostIdentity


async def prepare_invites(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
    settings: Settings,
) -> list[PreparedInvite]:
    async def _run() -> list[PreparedInvite]:
        event = await require_event_access(session, event_key, host, need="edit")
        host_row = await session.get(Host, event.host_uid)
        host_name = (host_row.display_name if host_row and host_row.display_name else None) or (
            host.name or "Your host"
        )
        households = (
            await session.scalars(
                select(Household)
                .where(Household.event_key == event_key)
                .order_by(Household.created_at, Household.id)
            )
        ).all()

        origin = settings.public_origin.rstrip("/")
        hmac_secret = settings.token_hmac_secret.get_secret_value()
        phone_key = settings.phone_enc_key.get_secret_value()
        prepared: list[PreparedInvite] = []
        for hh in households:
            token = make_link_token(hmac_secret, event_key, hh.key)
            link = f"{origin}/i/{event_key}/{token}"
            code = format_household_code(event_key, hh.key)
            phone_digits = decrypt_phone(hh.phone_enc, phone_key) if hh.phone_enc else None
            wa_url = build_whatsapp_url(
                phone_digits=phone_digits,
                language=hh.language,
                host_name=host_name,
                event_title=event.title,
                link=link,
                code=code,
            )
            prepared.append(
                PreparedInvite(
                    household_id=hh.id,
                    display_name=hh.display_name,
                    link=link,
                    code=code,
                    whatsapp_url=wa_url,
                )
            )
        return prepared

    if session.in_transaction():
        return await _run()
    async with session.begin():
        return await _run()


async def mark_invite_sent(
    session: AsyncSession,
    event_key: str,
    household_id: str,
    host: HostIdentity,
) -> None:
    async with session.begin():
        await require_event_access(session, event_key, host, need="edit")
        hh = (
            await session.execute(
                select(Household).where(
                    Household.id == household_id, Household.event_key == event_key
                )
            )
        ).scalar_one_or_none()
        if hh is None:
            raise ProblemError(404, "Household not found")
        hh.invite_sent_at = datetime.now(UTC)


async def resolve_household_by_token(
    session: AsyncSession,
    event_key: str,
    token: str,
    settings: Settings,
) -> tuple[Event, Household]:
    hmac_secret = settings.token_hmac_secret.get_secret_value()
    household_key = verify_link_token(hmac_secret, event_key, token)
    if household_key is None:
        raise ProblemError(404, "Invitation not found")

    event = (
        await session.execute(
            select(Event).where(Event.event_key == event_key, Event.purged_at.is_(None))
        )
    ).scalar_one_or_none()
    if event is None:
        raise ProblemError(404, "Invitation not found")

    hh = (
        await session.execute(
            select(Household).where(
                Household.event_key == event_key, Household.key == household_key
            )
        )
    ).scalar_one_or_none()
    if hh is None:
        raise ProblemError(404, "Invitation not found")
    return event, hh


async def get_guest_invitation(
    session: AsyncSession,
    event_key: str,
    token: str,
    settings: Settings,
) -> dict[str, Any]:  # Any: JSON response dict
    event, hh = await resolve_household_by_token(session, event_key, token, settings)

    invited_fn_ids = set(
        (
            await session.scalars(
                select(Invitation.function_id).where(Invitation.household_id == hh.id)
            )
        ).all()
    )
    fns = (
        await session.scalars(
            select(Fn)
            .where(Fn.event_key == event_key, Fn.id.in_(invited_fn_ids))
            .order_by(Fn.position)
        )
    ).all()
    rsvps = {
        r.function_id: r
        for r in (await session.scalars(select(Rsvp).where(Rsvp.household_id == hh.id))).all()
    }
    passes = (
        await session.scalars(
            select(Pass).where(Pass.household_id == hh.id, Pass.revoked.is_(False))
        )
    ).all()

    map_url = (
        f"https://www.openstreetmap.org/?mlat={event.venue_lat}"
        f"&mlon={event.venue_lng}#map=17/{event.venue_lat}/{event.venue_lng}"
    )

    return {
        "eventKey": event.event_key,
        "eventTitle": event.title,
        "venue": {
            "label": event.venue_label,
            "lat": event.venue_lat,
            "lng": event.venue_lng,
            "mapUrl": map_url,
        },
        "household": {
            "id": hh.id,
            "key": hh.key,
            "displayName": hh.display_name,
            "seatCap": hh.seat_cap,
            "language": hh.language,
            "code": format_household_code(event.event_key, hh.key),
        },
        "functions": [
            {
                "id": f.id,
                "name": f.name,
                "startsAt": f.starts_at.isoformat(),
                "meal": f.meal,
                "venueLabel": event.venue_label,
                "mapUrl": map_url,
            }
            for f in fns
        ],
        "replies": {
            fid: {
                "functionId": r.function_id,
                "attending": r.status,
                "adults": r.adults,
                "children": r.children,
                "meals": {
                    "veg": r.meals.get("veg", 0),
                    "jain": r.meals.get("jain", 0),
                    "nonVeg": r.meals.get("non_veg", 0),
                },
                "dietaryRules": r.dietary_rules,
                "allergies": r.allergies,
                "arrivalWindow": r.arrival_window,
            }
            for fid, r in rsvps.items()
        },
        "passes": [
            {
                "functionId": p.function_id,
                "passId": p.id,
                "seats": p.seats,
                "token": p.token,
            }
            for p in passes
        ],
    }


async def resolve_guest_code(
    session: AsyncSession,
    code: str,
    client_ip_addr: str,
    settings: Settings,
    now: datetime,
) -> dict[str, str]:
    hmac_secret = settings.token_hmac_secret.get_secret_value()
    hashed_ip = ip_hash(client_ip_addr, hmac_secret)
    await hit(session, f"resolve:{hashed_ip}", limit=10, window_s=60, now=now)

    parsed = split_household_code(code)
    if parsed is None:
        raise ProblemError(404, "Invitation not found")
    event_key, household_key = parsed

    row = (
        await session.execute(
            select(Household)
            .join(Event, Event.event_key == Household.event_key)
            .where(
                Household.event_key == event_key,
                Household.key == household_key,
                Event.purged_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProblemError(404, "Invitation not found")

    token = make_link_token(hmac_secret, event_key, household_key)
    return {"eventKey": event_key, "token": token}
