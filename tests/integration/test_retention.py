from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.db.models import (
    Event,
    EventLog,
    Fn,
    Household,
    Idempotency,
    Invitation,
    Outcome,
    Pass,
    PlanModel,
    Rsvp,
    Scan,
)
from hostora.security.tokens import sign_cron


@pytest.mark.db
async def test_retention_purges_pii_and_keeps_functions_plans_and_outcomes(
    client: httpx.AsyncClient,
    db: AsyncSession,
    settings: Settings,
    host_headers: dict[str, str],
) -> None:
    seed = await client.post("/v1/demo/seed", headers=host_headers)
    assert seed.status_code == 200
    event_key = seed.json()["eventKey"]

    ev = await client.get(f"/v1/events/{event_key}", headers=host_headers)
    fn_id = ev.json()["functions"][1]["id"]

    # Record an outcome and a plan row, and add a scan
    await client.post(
        f"/v1/events/{event_key}/outcome",
        headers=host_headers,
        json={"functionId": fn_id, "actualCount": 140, "ranShort": [], "leftover": ["jeera_rice"]},
    )
    await client.post(
        f"/v1/events/{event_key}/plans",
        headers=host_headers,
        json={"functionId": fn_id, "brief": "dinner"},
    )
    link_resp = await client.post(
        f"/v1/events/{event_key}/gate-links",
        headers=host_headers,
        json={"functionId": fn_id, "hours": 24},
    )
    gate_token = link_resp.json()["url"].split("?t=")[1]
    await client.post(
        f"/v1/gate/{event_key}/scans:batch",
        params={"t": gate_token},
        json={
            "functionId": fn_id,
            "scans": [
                {
                    "deviceId": "dev_1",
                    "seq": 1,
                    "passId": "UNKNOWN1",
                    "seats": 2,
                    "at": 1761900000000,
                }
            ],
        },
    )

    # Move event purge_after into the past
    past = datetime.now(UTC) - timedelta(days=2)
    async with db.begin():
        await db.execute(update(Event).where(Event.event_key == event_key).values(purge_after=past))

    # Call signed retention cron endpoint
    ts = int(datetime.now(UTC).timestamp())
    sig = sign_cron(settings.cron_secret.get_secret_value(), ts, "retention")
    cron_resp = await client.post(
        "/internal/cron/retention",
        headers={"X-Cron-Timestamp": str(ts), "X-Cron-Signature": sig},
    )
    assert cron_resp.status_code == 200
    counts = cron_resp.json()["counts"]
    assert counts["eventsPurged"] == 1
    assert counts["householdsDeleted"] == 60

    async with db.begin():
        assert (
            await db.scalar(
                select(func.count()).select_from(Household).where(Household.event_key == event_key)
            )
            == 0
        )
        assert await db.scalar(select(func.count()).select_from(Invitation)) == 0
        assert await db.scalar(select(func.count()).select_from(Rsvp)) == 0
        assert (
            await db.scalar(
                select(func.count()).select_from(Pass).where(Pass.event_key == event_key)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(Scan).where(Scan.event_key == event_key)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Idempotency)
                .where(Idempotency.scope.like("rsvp:hh_%"))
            )
            == 0
        )

        # fn, plan, outcome remain
        assert (
            await db.scalar(select(func.count()).select_from(Fn).where(Fn.event_key == event_key))
            == 2
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(PlanModel).where(PlanModel.function_id == fn_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(Outcome).where(Outcome.function_id == fn_id)
            )
            == 1
        )

        # One event.purged log row with aggregate counts
        purged_logs = (
            await db.scalars(
                select(EventLog).where(
                    EventLog.event_key == event_key, EventLog.kind == "event.purged"
                )
            )
        ).all()
        assert len(purged_logs) == 1
        assert purged_logs[0].payload["households"] == 60
