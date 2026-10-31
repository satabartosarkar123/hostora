from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from hostora.db.models import Fn, Household, Invitation, Rsvp
from hostora.engine.codes import format_household_code, random_key
from hostora.errors import ProblemError
from hostora.security.crypto import decrypt_phone, encrypt_phone, mask_phone, phone_hash
from hostora.security.session import require_event_access

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.config import Settings
    from hostora.schemas.events import HouseholdBatch
    from hostora.security.firebase import HostIdentity


async def add_households_batch(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
    batch: HouseholdBatch,
    settings: Settings,
) -> list[dict[str, Any]]:  # Any: JSON response dicts
    now = datetime.now(UTC)
    created: list[dict[str, Any]] = []

    async with session.begin():
        await require_event_access(session, event_key, host, need="edit")
        valid_fns = set(
            (await session.scalars(select(Fn.id).where(Fn.event_key == event_key))).all()
        )
        for h_in in batch.households:
            for fid in h_in.function_ids:
                if fid not in valid_fns:
                    raise ProblemError(
                        422,
                        "Function does not belong to this event",
                        field="functionIds",
                    )

        used_keys = set(
            (
                await session.scalars(select(Household.key).where(Household.event_key == event_key))
            ).all()
        )

        for h_in in batch.households:
            hh_key = random_key(5)
            while hh_key in used_keys:
                hh_key = random_key(5)
            used_keys.add(hh_key)

            hh_id = f"hh_{random_key(10)}"
            enc_phone: str | None = None
            hashed_phone: str | None = None
            if h_in.phone:
                enc_phone = encrypt_phone(h_in.phone, settings.phone_enc_key.get_secret_value())
                hashed_phone = phone_hash(h_in.phone, settings.token_hmac_secret.get_secret_value())

            hh = Household(
                id=hh_id,
                event_key=event_key,
                key=hh_key,
                display_name=h_in.display_name,
                phone_enc=enc_phone,
                phone_hash=hashed_phone,
                seat_cap=h_in.seat_cap,
                language=h_in.language.value,
                created_at=now,
            )
            session.add(hh)

            unique_fn_ids = list(dict.fromkeys(h_in.function_ids))
            for fid in unique_fn_ids:
                session.add(Invitation(household_id=hh_id, function_id=fid))

            created.append(
                {
                    "id": hh_id,
                    "displayName": h_in.display_name,
                    "seatCap": h_in.seat_cap,
                    "language": h_in.language.value,
                    "functionIds": unique_fn_ids,
                    "code": format_household_code(event_key, hh_key),
                }
            )

    return created


async def list_households(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
    settings: Settings,
) -> list[dict[str, Any]]:  # Any: JSON response dicts
    async def _run() -> list[dict[str, Any]]:
        await require_event_access(session, event_key, host, need="view")

        households = (
            await session.scalars(
                select(Household)
                .where(Household.event_key == event_key)
                .order_by(Household.created_at, Household.id)
            )
        ).all()
        if not households:
            return []

        hh_ids = [h.id for h in households]
        inv_rows = (
            (await session.execute(select(Invitation).where(Invitation.household_id.in_(hh_ids))))
            .scalars()
            .all()
        )
        rsvp_rows = (
            (await session.execute(select(Rsvp).where(Rsvp.household_id.in_(hh_ids))))
            .scalars()
            .all()
        )

        invited_by_hh: dict[str, list[str]] = {h.id: [] for h in households}
        for inv in inv_rows:
            invited_by_hh.setdefault(inv.household_id, []).append(inv.function_id)

        rsvps_by_hh: dict[str, dict[str, dict[str, Any]]] = {h.id: {} for h in households}
        for r in rsvp_rows:
            rsvps_by_hh.setdefault(r.household_id, {})[r.function_id] = {
                "status": r.status,
                "adults": r.adults,
                "children": r.children,
                "meals": r.meals,
                "updatedAt": r.updated_at.isoformat(),
            }

        out: list[dict[str, Any]] = []
        for h in households:
            masked: str | None = None
            if h.phone_enc:
                masked = mask_phone(
                    decrypt_phone(h.phone_enc, settings.phone_enc_key.get_secret_value())
                )
            out.append(
                {
                    "id": h.id,
                    "displayName": h.display_name,
                    "phoneMasked": masked,
                    "seatCap": h.seat_cap,
                    "language": h.language,
                    "code": format_household_code(event_key, h.key),
                    "inviteSentAt": h.invite_sent_at.isoformat() if h.invite_sent_at else None,
                    "functionIds": invited_by_hh.get(h.id, []),
                    "replies": rsvps_by_hh.get(h.id, {}),
                }
            )
        return out

    if session.in_transaction():
        return await _run()
    async with session.begin():
        return await _run()
