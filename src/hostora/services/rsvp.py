from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hostora.db.models import EventLog, Household, Idempotency, Invitation, Rsvp
from hostora.errors import ProblemError
from hostora.schemas.common import Attending
from hostora.schemas.rsvp import Meals, RsvpInput, RsvpResult
from hostora.services.passes import issue_passes

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.realtime.broadcaster import Broadcaster


async def submit_rsvp(
    session: AsyncSession,
    broadcaster: Broadcaster,
    *,
    event_key: str,
    household_key: str,
    body: RsvpInput,
    idempotency_key: str,
    signing_key_hex: str,
    now: datetime,
) -> RsvpResult:
    request_hash = hashlib.sha256(body.model_dump_json(by_alias=True).encode()).hexdigest()

    async with session.begin():
        hh = (
            await session.execute(
                select(Household)
                .where(Household.event_key == event_key, Household.key == household_key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if hh is None:
            raise ProblemError(404, "Invitation not found")

        scope = f"rsvp:{hh.id}"
        prior = await session.get(Idempotency, (scope, idempotency_key))
        if prior is not None:
            if prior.request_hash != request_hash:
                raise ProblemError(
                    422,
                    "Idempotency-Key was used with a different reply",
                    field="idempotencyKey",
                )
            return RsvpResult.model_validate(prior.response)

        invited = set(
            (
                await session.scalars(
                    select(Invitation.function_id).where(Invitation.household_id == hh.id)
                )
            ).all()
        )
        seen: set[str] = set()
        for f in body.functions:
            if f.function_id not in invited or f.function_id in seen:
                raise ProblemError(422, "Not invited to this function", field="functions")
            seen.add(f.function_id)
            if f.attending is Attending.NO:
                continue
            seats = f.adults + f.children
            if seats < 1:
                raise ProblemError(422, "At least one guest is needed", field="adults")
            if seats > hh.seat_cap:
                raise ProblemError(422, "More guests than this invitation allows", field="adults")
            if f.meals.veg + f.meals.jain + f.meals.non_veg != seats:
                raise ProblemError(
                    422,
                    "Meal choices must add up to the number of guests",
                    field="meals",
                )

        hh.language = body.language.value
        for f in body.functions:
            yes = f.attending is not Attending.NO
            values = {
                "household_id": hh.id,
                "function_id": f.function_id,
                "status": f.attending.value,
                "adults": f.adults if yes else 0,
                "children": f.children if yes else 0,
                "meals": (f.meals if yes else Meals()).model_dump(),
                "dietary_rules": [r.value for r in f.dietary_rules],
                "allergies": f.allergies,
                "arrival_window": f.arrival_window,
                "first_replied_at": now,
                "updated_at": now,
            }
            stmt = pg_insert(Rsvp).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Rsvp.household_id, Rsvp.function_id],
                set_={
                    k: stmt.excluded[k]
                    for k in values
                    if k not in {"household_id", "function_id", "first_replied_at"}
                }
                | {"version": Rsvp.version + 1},
            )
            await session.execute(stmt)

        session.add(
            EventLog(
                event_key=event_key,
                kind="rsvp.submitted",
                payload={
                    "household_id": hh.id,
                    "functions": [
                        {
                            "function_id": f.function_id,
                            "status": f.attending.value,
                            "seats": 0 if f.attending is Attending.NO else f.adults + f.children,
                        }
                        for f in body.functions
                    ],
                },
            )
        )
        passes = await issue_passes(session, hh, signing_key_hex, now)
        result = RsvpResult(
            ok=True,
            functions=[f.function_id for f in body.functions],
            passes=passes,
        )
        session.add(
            Idempotency(
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=result.model_dump(mode="json", by_alias=True),
            )
        )

    await broadcaster.publish(event_key, "summary")
    return result
