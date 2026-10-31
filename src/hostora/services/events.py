from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from hostora.db.models import Cohost, Event, EventLog, Fn, Host, Outcome, PortionOutcome
from hostora.engine.codes import random_key
from hostora.errors import ProblemError
from hostora.schemas.events import CreatedEvent, CreateEventInput, FunctionRef
from hostora.security.session import require_event_access
from hostora.services.summary import get_event_summary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.security.firebase import HostIdentity


async def create_event(
    session: AsyncSession,
    host: HostIdentity,
    body: CreateEventInput,
    *,
    now: datetime | None = None,
    fixed_event_key: str | None = None,
    is_demo: bool = False,
) -> CreatedEvent:
    ts = now or datetime.now(UTC)
    max_start = max(f.starts_at for f in body.functions)
    purge_after = max_start + timedelta(days=90)

    for attempt in range(5):
        event_key = fixed_event_key if fixed_event_key is not None else random_key(5)
        try:
            async with session.begin():
                host_stmt = pg_insert(Host).values(
                    uid=host.uid, display_name=host.name, created_at=ts
                )
                if host.name:
                    host_stmt = host_stmt.on_conflict_do_update(
                        index_elements=[Host.uid],
                        set_={"display_name": host.name},
                    )
                else:
                    host_stmt = host_stmt.on_conflict_do_nothing(index_elements=[Host.uid])
                await session.execute(host_stmt)

                event = Event(
                    event_key=event_key,
                    host_uid=host.uid,
                    title=body.title,
                    type=body.type.value,
                    tradition=body.tradition,
                    venue_label=body.venue.label,
                    venue_lat=body.venue.lat,
                    venue_lng=body.venue.lng,
                    pincode=body.venue.pincode,
                    budget_inr=body.budget_inr,
                    is_demo=is_demo,
                    purge_after=purge_after,
                    created_at=ts,
                )
                session.add(event)

                refs: list[FunctionRef] = []
                for pos, fn_in in enumerate(body.functions):
                    fn_id = f"fn_{random_key(8)}"
                    fn_row = Fn(
                        id=fn_id,
                        event_key=event_key,
                        name=fn_in.name,
                        starts_at=fn_in.starts_at,
                        meal=fn_in.meal.value,
                        rules=[r.value for r in fn_in.rules],
                        gate_mode=fn_in.gate_mode.value,
                        position=pos,
                    )
                    session.add(fn_row)
                    refs.append(FunctionRef(id=fn_id, name=fn_in.name))

                session.add(
                    EventLog(
                        event_key=event_key,
                        kind="event.created",
                        payload={
                            "type": body.type.value,
                            "functions": len(body.functions),
                        },
                        created_at=ts,
                    )
                )
            return CreatedEvent(event_key=event_key, functions=refs)
        except IntegrityError as exc:
            if fixed_event_key is not None or attempt == 4:
                raise ProblemError(409, "Could not allocate a unique event key") from exc
    raise ProblemError(409, "Could not allocate a unique event key")


async def list_host_events(
    session: AsyncSession,
    host: HostIdentity,
) -> list[dict[str, Any]]:  # Any: JSON response dicts
    cohost_subq = select(Cohost.event_key).where(Cohost.uid == host.uid)
    rows = (
        await session.scalars(
            select(Event)
            .where(
                Event.purged_at.is_(None),
                or_(Event.host_uid == host.uid, Event.event_key.in_(cohost_subq)),
            )
            .order_by(Event.created_at.desc())
        )
    ).all()
    return [
        {
            "eventKey": e.event_key,
            "title": e.title,
            "type": e.type,
            "tradition": e.tradition,
            "venueLabel": e.venue_label,
            "budgetInr": e.budget_inr,
            "isDemo": e.is_demo,
            "createdAt": e.created_at.isoformat(),
        }
        for e in rows
    ]


async def get_event_detail(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
) -> dict[str, Any]:  # Any: JSON response dict
    async with session.begin():
        event = await require_event_access(session, event_key, host, need="view")
        fns = (
            await session.scalars(select(Fn).where(Fn.event_key == event_key).order_by(Fn.position))
        ).all()
        summary = await get_event_summary(session, event_key)
        return {
            "eventKey": event.event_key,
            "title": event.title,
            "type": event.type,
            "tradition": event.tradition,
            "venue": {
                "label": event.venue_label,
                "lat": event.venue_lat,
                "lng": event.venue_lng,
                "pincode": event.pincode,
            },
            "budgetInr": event.budget_inr,
            "shortWasteSlider": event.short_waste_slider,
            "isDemo": event.is_demo,
            "functions": [
                {
                    "id": f.id,
                    "name": f.name,
                    "startsAt": f.starts_at.isoformat(),
                    "meal": f.meal,
                    "rules": f.rules,
                    "gateMode": f.gate_mode,
                    "position": f.position,
                }
                for f in fns
            ],
            "summary": summary.model_dump(mode="json", by_alias=True),
        }


async def record_outcome(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
    *,
    function_id: str,
    actual_count: int | None,
    ran_short: list[str],
    leftover: list[str],
    item_outcomes: list[dict[str, Any]] | None = None,
    now: datetime,
) -> dict[str, Any]:  # Any: JSON response dict
    async with session.begin():
        await require_event_access(session, event_key, host, need="edit")
        fn = (
            await session.execute(select(Fn).where(Fn.id == function_id, Fn.event_key == event_key))
        ).scalar_one_or_none()
        if fn is None:
            raise ProblemError(422, "Function does not belong to this event", field="functionId")

        stmt = pg_insert(Outcome).values(
            function_id=function_id,
            actual_count=actual_count,
            ran_short=ran_short,
            leftover=leftover,
            reported_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Outcome.function_id],
            set_={
                "actual_count": actual_count,
                "ran_short": ran_short,
                "leftover": leftover,
                "reported_at": now,
            },
        )
        await session.execute(stmt)

        evt = await session.get(Event, event_key)
        region = (
            "West Bengal / Kolkata"
            if (evt and evt.pincode and evt.pincode.startswith("7"))
            else "India"
        )

        # Record portion outcomes for items
        outcomes_to_record: list[tuple[str, str, float, float]] = []
        if item_outcomes:
            for it in item_outcomes:
                sk = str(it.get("serving_kind", "main_gravy"))
                oc = str(it.get("outcome", "about_right"))
                pl = float(it.get("planned_per_head", 250.0))
                lf = float(it.get("leftover_fraction", 0.0))
                outcomes_to_record.append((sk, oc, pl, lf))
        else:
            for _item_name in ran_short:
                outcomes_to_record.append(("main_gravy", "ran_short", 250.0, 0.0))
            for _item_name in leftover:
                outcomes_to_record.append(("main_gravy", "leftover", 250.0, 0.3))

        for sk, oc, pl, lf in outcomes_to_record:
            p_out = PortionOutcome(
                function_id=function_id,
                serving_kind=sk,
                region=region,
                planned_per_head=pl,
                outcome=oc,
                leftover_fraction=lf,
                reported_at=now,
            )
            session.add(p_out)

        session.add(
            EventLog(
                event_key=event_key,
                kind="outcome.reported",
                payload={
                    "function_id": function_id,
                    "actual_count": actual_count,
                },
                created_at=now,
            )
        )
    return {"ok": True, "functionId": function_id}
