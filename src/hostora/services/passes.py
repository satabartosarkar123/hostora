from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from hostora.db.models import Fn, Household, Pass, Rsvp
from hostora.engine.codes import random_key
from hostora.engine.passes import PassClaims, sign_pass
from hostora.schemas.rsvp import IssuedPass

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def issue_passes(
    session: AsyncSession,
    hh: Household,
    signing_key_hex: str,
    now: datetime,
) -> list[IssuedPass]:
    fns = (
        await session.scalars(select(Fn).where(Fn.event_key == hh.event_key).order_by(Fn.position))
    ).all()
    rsvps = {
        r.function_id: r
        for r in (await session.scalars(select(Rsvp).where(Rsvp.household_id == hh.id))).all()
    }
    existing_passes = {
        p.function_id: p
        for p in (await session.scalars(select(Pass).where(Pass.household_id == hh.id))).all()
    }

    issued: list[IssuedPass] = []
    for fn in fns:
        rsvp = rsvps.get(fn.id)
        existing = existing_passes.get(fn.id)
        if rsvp is None:
            continue

        if rsvp.status in ("yes", "maybe") and fn.gate_mode != "off":
            seats = rsvp.adults + rsvp.children
            if seats < 1:
                continue
            exp = int((fn.starts_at + timedelta(hours=12)).timestamp())
            pass_id = existing.id if existing is not None else random_key(10)
            token = sign_pass(
                PassClaims(
                    event_key=hh.event_key,
                    pass_id=pass_id,
                    seats=seats,
                    exp=exp,
                ),
                signing_key_hex,
            )
            if existing is not None:
                existing.seats = seats
                existing.token = token
                existing.revoked = False
                existing.issued_at = now
            else:
                session.add(
                    Pass(
                        id=pass_id,
                        event_key=hh.event_key,
                        household_id=hh.id,
                        function_id=fn.id,
                        seats=seats,
                        token=token,
                        revoked=False,
                        issued_at=now,
                    )
                )
            issued.append(
                IssuedPass(
                    function_id=fn.id,
                    pass_id=pass_id,
                    seats=seats,
                    token=token,
                )
            )
        elif rsvp.status == "no" and existing is not None:
            existing.revoked = True

    return issued
