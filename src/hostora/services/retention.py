from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete, func, select, text, update

from hostora.db.models import (
    Attempt,
    Event,
    EventLog,
    Household,
    Idempotency,
    Invitation,
    LlmCache,
    Pass,
    PlanModel,
    Rsvp,
    Scan,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def run_retention_job(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    ts = now or datetime.now(UTC)
    events_purged = 0
    households_deleted = 0
    rsvps_deleted = 0
    passes_deleted = 0
    scans_deleted = 0
    idempotency_deleted = 0

    async with session.begin():
        due_events = (
            await session.scalars(
                select(Event).where(Event.purge_after < ts, Event.purged_at.is_(None))
            )
        ).all()

        for ev in due_events:
            hh_ids = list(
                (
                    await session.scalars(
                        select(Household.id).where(Household.event_key == ev.event_key)
                    )
                ).all()
            )
            inv_count = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Invitation)
                        .where(Invitation.household_id.in_(hh_ids))
                    )
                )
                or 0
                if hh_ids
                else 0
            )
            rsvp_count = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(Rsvp).where(Rsvp.household_id.in_(hh_ids))
                    )
                )
                or 0
                if hh_ids
                else 0
            )
            pass_count = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(Pass).where(Pass.event_key == ev.event_key)
                    )
                )
                or 0
            )
            scan_count = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(Scan).where(Scan.event_key == ev.event_key)
                    )
                )
                or 0
            )

            if hh_ids:
                scopes = [f"rsvp:{hid}" for hid in hh_ids]
                idem_res = await session.execute(
                    delete(Idempotency).where(Idempotency.scope.in_(scopes))
                )
                idempotency_deleted += int(idem_res.rowcount or 0)  # type: ignore[attr-defined]

            await session.execute(delete(Scan).where(Scan.event_key == ev.event_key))
            await session.execute(delete(Household).where(Household.event_key == ev.event_key))
            ev.purged_at = ts

            session.add(
                EventLog(
                    event_key=ev.event_key,
                    kind="event.purged",
                    payload={
                        "households": len(hh_ids),
                        "invitations": inv_count,
                        "rsvps": rsvp_count,
                        "passes": pass_count,
                        "scans": scan_count,
                    },
                    created_at=ts,
                )
            )
            events_purged += 1
            households_deleted += len(hh_ids)
            rsvps_deleted += rsvp_count
            passes_deleted += pass_count
            scans_deleted += scan_count

    counts = {
        "eventsPurged": events_purged,
        "householdsDeleted": households_deleted,
        "rsvpsDeleted": rsvps_deleted,
        "passesDeleted": passes_deleted,
        "scansDeleted": scans_deleted,
        "idempotencyDeleted": idempotency_deleted,
    }
    logger.info("cron_retention_completed", **counts)
    return counts


async def run_cleanup_job(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    ts = now or datetime.now(UTC)
    attempt_cutoff = ts - timedelta(days=1)
    cache_cutoff = ts - timedelta(days=7)
    idem_cutoff = ts - timedelta(days=30)
    plan_cutoff = ts - timedelta(minutes=15)

    async with session.begin():
        att_res = await session.execute(
            delete(Attempt).where(Attempt.window_start < attempt_cutoff)
        )
        cache_res = await session.execute(
            delete(LlmCache).where(LlmCache.created_at < cache_cutoff)
        )
        idem_res = await session.execute(
            delete(Idempotency).where(Idempotency.created_at < idem_cutoff)
        )
        plan_res = await session.execute(
            update(PlanModel)
            .where(PlanModel.status == "running", PlanModel.created_at < plan_cutoff)
            .values(status="failed", finished_at=ts)
        )

    counts = {
        "attemptsDeleted": int(att_res.rowcount or 0),  # type: ignore[attr-defined]
        "llmCacheDeleted": int(cache_res.rowcount or 0),  # type: ignore[attr-defined]
        "idempotencyDeleted": int(idem_res.rowcount or 0),  # type: ignore[attr-defined]
        "plansFailed": int(plan_res.rowcount or 0),  # type: ignore[attr-defined]
    }
    logger.info("cron_cleanup_completed", **counts)
    return counts


async def run_warm_job(session: AsyncSession) -> dict[str, int]:
    async with session.begin():
        await session.execute(text("SELECT 1"))
    counts = {"ok": 1}
    logger.info("cron_warm_completed", **counts)
    return counts
