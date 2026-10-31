from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hostora.data.loader import get_data
from hostora.db.models import (
    Event,
    EventLog,
    Fn,
    Household,
    KnowledgeFact,
    PriceObservation,
    Purchase,
)
from hostora.db.models import PlanModel as PlanRow
from hostora.engine.codes import random_key
from hostora.errors import ProblemError
from hostora.planner.redact import redact_brief
from hostora.schemas.plan import Plan, PlanItem, PlanRequest, RemovedItem
from hostora.security.session import require_event_access

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.security.firebase import HostIdentity


async def create_plan_run(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
    body: PlanRequest,
) -> dict[str, str]:
    async with session.begin():
        await require_event_access(session, event_key, host, need="edit")
        fn = (
            await session.execute(
                select(Fn).where(Fn.id == body.function_id, Fn.event_key == event_key)
            )
        ).scalar_one_or_none()
        if fn is None:
            raise ProblemError(422, "Function does not belong to this event", field="functionId")

        hh_names = list(
            (
                await session.scalars(
                    select(Household.display_name).where(Household.event_key == event_key)
                )
            ).all()
        )
        redacted = redact_brief(body.brief, forbidden_terms=hh_names)

        plan_id = f"pl_{random_key(10)}"
        row = PlanRow(
            id=plan_id,
            function_id=body.function_id,
            status="running",
            request={"brief": redacted, "days_left": body.days_left},
            body=None,
            route="groq",
            data_version=get_data().version,
            created_at=datetime.now(UTC),
        )
        session.add(row)

    return {"planId": plan_id, "streamUrl": f"/v1/plans/{plan_id}/stream"}


async def verify_plan_access(
    session: AsyncSession,
    plan_id: str,
    host: HostIdentity,
    *,
    need: Literal["view", "edit"] = "view",
) -> tuple[PlanRow, Fn]:
    row = await session.get(PlanRow, plan_id)
    if row is None:
        raise ProblemError(404, "Plan not found")
    fn = await session.get(Fn, row.function_id)
    if fn is None:
        raise ProblemError(404, "Plan not found")
    try:
        await require_event_access(session, fn.event_key, host, need=need)
    except ProblemError as err:
        raise ProblemError(404, "Plan not found") from err
    return row, fn


async def get_plan_for_host(
    session: AsyncSession,
    plan_id: str,
    host: HostIdentity,
) -> tuple[PlanRow, Fn, Plan]:
    async with session.begin():
        row, fn = await verify_plan_access(session, plan_id, host, need="view")
        if row.body is None:
            raise ProblemError(404, "Plan is not ready")
        plan_obj = Plan.model_validate(row.body)
        return row, fn, plan_obj


async def update_plan_item_purchase(
    session: AsyncSession,
    plan_id: str,
    item_id: str,
    host: HostIdentity,
    *,
    bought: bool,
    actual_inr: int | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with session.begin():
        row, fn = await verify_plan_access(session, plan_id, host, need="edit")
        if row.body is None:
            raise ProblemError(404, "Plan not found")

        plan_obj = Plan.model_validate(row.body)
        matching_item = next((it for it in plan_obj.items if it.item_id == item_id), None)
        if matching_item is None:
            matching_item = next(
                (it for it in plan_obj.needs_confirmation if it.item_id == item_id), None
            )
        if matching_item is None:
            raise ProblemError(404, "Item not in plan")

        stmt = pg_insert(Purchase).values(
            plan_id=plan_id,
            item_id=item_id,
            bought=bought,
            actual_inr=actual_inr,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Purchase.plan_id, Purchase.item_id],
            set_={
                "bought": bought,
                "actual_inr": actual_inr,
                "updated_at": now,
            },
        )
        await session.execute(stmt)

        # Learning loop: Record into price_observation
        if bought and actual_inr is not None and actual_inr > 0 and matching_item.buy_total > 0:
            inr_per_u = actual_inr / matching_item.buy_total
            evt = await session.get(Event, fn.event_key)
            region = (
                "West Bengal / Kolkata"
                if (evt and evt.pincode and evt.pincode.startswith("7"))
                else "India"
            )
            price_obs = PriceObservation(
                item_norm=matching_item.name.lower(),
                unit=matching_item.unit,
                inr_per_unit=inr_per_u,
                region=region,
                source="host_reported",
                observed_on=date.today(),
            )
            session.add(price_obs)

    return {
        "planId": plan_id,
        "itemId": item_id,
        "bought": bought,
        "actualInr": actual_inr,
    }


async def confirm_plan_item(
    session: AsyncSession,
    plan_id: str,
    item_id: str,
    host: HostIdentity,
    confirmations: list[str],
) -> Plan:
    """Record host confirmation and move item to allowed items list."""
    now = datetime.now(UTC)
    async with session.begin():
        row, fn = await verify_plan_access(session, plan_id, host, need="edit")
        if row.body is None:
            raise ProblemError(404, "Plan not found")

        plan_obj = Plan.model_validate(row.body)

        # Find in needs_confirmation
        item_idx = next(
            (i for i, it in enumerate(plan_obj.needs_confirmation) if it.item_id == item_id), None
        )
        if item_idx is not None:
            target_item = plan_obj.needs_confirmation.pop(item_idx)
            # Update status
            approved_item = target_item.model_copy(update={"status": "allowed"})
            plan_obj.items.append(approved_item)
        else:
            # Check in items
            found_item = next((it for it in plan_obj.items if it.item_id == item_id), None)
            if found_item is None:
                raise ProblemError(404, "Item not found in plan")
            target_item = found_item

        # Record host fact
        fact = KnowledgeFact(
            subject_norm=target_item.name.lower(),
            kind="dish_ingredients",
            source="host",
            payload={"tags": [f"confirmed:{c}" for c in confirmations], "ingredients": []},
            citation=f"host:{host.uid}",
            host_uid=host.uid,
            retrieved_at=now,
            expires_at=now + timedelta(days=365),
        )
        session.add(fact)

        # Update plan row
        row.body = plan_obj.model_dump(by_alias=True, mode="json")

        session.add(
            EventLog(
                event_key=fn.event_key,
                kind="plan.item_confirmed",
                payload={
                    "plan_id": plan_id,
                    "item_id": item_id,
                    "confirmations": confirmations,
                },
                created_at=now,
            )
        )

    return plan_obj


async def replace_plan_item(
    session: AsyncSession,
    plan_id: str,
    item_id: str,
    host: HostIdentity,
    alternative_index: int | None = None,
    name: str | None = None,
) -> Plan:
    """Replace an item with an alternative or a new named item."""
    now = datetime.now(UTC)
    async with session.begin():
        row, fn = await verify_plan_access(session, plan_id, host, need="edit")
        if row.body is None:
            raise ProblemError(404, "Plan not found")

        plan_obj = Plan.model_validate(row.body)
        target_list = (
            plan_obj.items
            if any(it.item_id == item_id for it in plan_obj.items)
            else plan_obj.needs_confirmation
        )
        item_idx = next((i for i, it in enumerate(target_list) if it.item_id == item_id), None)
        if item_idx is None:
            raise ProblemError(404, "Item not in plan")

        old_item = target_list[item_idx]
        new_name = name or (
            old_item.alternatives[alternative_index].get("name", "Replacement")
            if alternative_index is not None and alternative_index < len(old_item.alternatives)
            else "Replacement"
        )

        replaced = old_item.model_copy(
            update={
                "name": str(new_name),
                "item_id": f"rep_{uuid4().hex[:8]}",
                "status": "allowed",
            }
        )
        target_list[item_idx] = replaced
        row.body = plan_obj.model_dump(by_alias=True, mode="json")

        session.add(
            EventLog(
                event_key=fn.event_key,
                kind="plan.item_replaced",
                payload={"plan_id": plan_id, "old_item": item_id, "new_item": str(new_name)},
                created_at=now,
            )
        )

    return plan_obj


async def remove_plan_item(
    session: AsyncSession,
    plan_id: str,
    item_id: str,
    host: HostIdentity,
) -> Plan:
    """Remove item from plan and move to removed items."""
    now = datetime.now(UTC)
    async with session.begin():
        row, fn = await verify_plan_access(session, plan_id, host, need="edit")
        if row.body is None:
            raise ProblemError(404, "Plan not found")

        plan_obj = Plan.model_validate(row.body)
        found_item: PlanItem | None = None

        if any(it.item_id == item_id for it in plan_obj.items):
            idx = next(i for i, it in enumerate(plan_obj.items) if it.item_id == item_id)
            found_item = plan_obj.items.pop(idx)
        elif any(it.item_id == item_id for it in plan_obj.needs_confirmation):
            idx = next(
                i for i, it in enumerate(plan_obj.needs_confirmation) if it.item_id == item_id
            )
            found_item = plan_obj.needs_confirmation.pop(idx)

        if found_item is None:
            raise ProblemError(404, "Item not in plan")

        plan_obj.removed.append(
            RemovedItem(
                item_id=found_item.item_id,
                name=found_item.name,
                reason="Removed by host",
                reasons=["Removed by host"],
                citations=found_item.grounding.citations,
            )
        )

        # Recalculate total budget
        new_total = sum(it.est_cost_inr for it in plan_obj.items)
        plan_obj.budget.total_inr = new_total
        plan_obj.budget.fits = new_total <= plan_obj.budget.limit_inr

        row.body = plan_obj.model_dump(by_alias=True, mode="json")

        session.add(
            EventLog(
                event_key=fn.event_key,
                kind="plan.item_removed",
                payload={"plan_id": plan_id, "item_id": item_id},
                created_at=now,
            )
        )

    return plan_obj


async def get_latest_plan_for_function(
    session: AsyncSession,
    function_id: str,
) -> tuple[Plan | None, dict[str, Purchase]]:
    async with session.begin():
        row = (
            await session.scalars(
                select(PlanRow)
                .where(PlanRow.function_id == function_id, PlanRow.status == "done")
                .order_by(PlanRow.created_at.desc())
                .limit(1)
            )
        ).first()
        if row is None or row.body is None:
            return None, {}
        purchases = (
            await session.scalars(select(Purchase).where(Purchase.plan_id == row.id))
        ).all()
        return Plan.model_validate(row.body), {p.item_id: p for p in purchases}
