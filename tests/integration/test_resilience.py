from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from httpx import ASGITransport

from hostora.config import Settings
from hostora.db.models import PlanModel
from hostora.main import create_app
from hostora.planner.pipeline import run_planner_stream
from hostora.schemas import EventType
from hostora.schemas.events import CreateEventInput, FunctionInput, Venue
from hostora.schemas.plan import Plan, PlanRequest
from hostora.security.firebase import HostIdentity
from hostora.services.events import create_event
from hostora.services.plans import create_plan_run
from hostora.services.retention import run_cleanup_job

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_resilience_bad_db_url_healthz_and_readyz(settings: Settings) -> None:
    bad_settings = settings.model_copy(
        update={"database_url": "postgresql+asyncpg://invalid:invalid@127.0.0.1:54321/nonexistent"}
    )
    bad_app = create_app(bad_settings)
    transport = ASGITransport(app=bad_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # /healthz returns 200 without touching DB
        hz = await client.get("/healthz")
        assert hz.status_code == 200
        assert hz.json() == {"ok": True}

        # /readyz returns 503 when DB connection fails
        rz = await client.get("/readyz")
        assert rz.status_code == 503
        assert rz.json() == {"ok": False}


@pytest.mark.db
@pytest.mark.asyncio
async def test_resilience_groq_429_or_timeout_falls_back_to_template(
    db: AsyncSession,
    settings: Settings,
) -> None:
    # 1. Create an event and function in the DB
    host = HostIdentity(uid="resilience-host-01", name="Resilience Host")
    now = datetime.now(UTC)
    evt_input = CreateEventInput(
        title="Resilience Test Wedding",
        type=EventType.WEDDING,
        venue=Venue(label="Grand Ballroom", lat=28.6, lng=77.2),
        budget_inr=500000,
        functions=[
            FunctionInput(
                name="Sangeet Dinner",
                meal="dinner",
                starts_at=now + timedelta(days=5),
                rules=["no_beef"],
            )
        ],
    )
    event_row = await create_event(db, host, evt_input, now=now)
    fn_id = event_row.functions[0].id

    # 2. Mock Groq with 429 rate limit
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "rate_limited"})
        )
        plan_ref = await create_plan_run(
            db,
            event_row.event_key,
            host,
            PlanRequest(function_id=fn_id, days_left=5, brief="Simple food"),
        )
        plan_id = plan_ref["planId"]

        app = create_app(settings)
        final_plan: Plan | None = None
        async for ev_type, payload in run_planner_stream(
            db,
            plan_id=plan_id,
            router=app.state.llm_router,
        ):
            if ev_type == "done" and isinstance(payload, Plan):
                final_plan = payload

        assert final_plan is not None
        assert final_plan.route == "template"
        assert len(final_plan.items) > 0
        assert final_plan.budget.total_inr > 0


@pytest.mark.db
@pytest.mark.asyncio
async def test_resilience_sse_client_disconnect_subscriber_cleanup(
    live_server: str,
    host_headers: dict[str, str],
) -> None:
    async with httpx.AsyncClient(base_url=live_server, timeout=10.0) as client:
        seed_resp = await client.post("/v1/demo/seed", headers=host_headers)
        assert seed_resp.status_code == 200
        event_key = seed_resp.json()["eventKey"]

        # Connect to live SSE and disconnect mid-stream
        async with client.stream(
            "GET",
            f"/v1/events/{event_key}/live?format=json",
            headers=host_headers,
        ) as stream:
            assert stream.status_code == 200
            # Connection open, immediately exit context to simulate disconnect

        # Server should remain completely healthy and responsive
        h_resp = await client.get("/healthz")
        assert h_resp.status_code == 200


@pytest.mark.db
@pytest.mark.asyncio
async def test_resilience_killed_planner_run_cleanup_marks_failed(
    db: AsyncSession,
) -> None:
    host = HostIdentity(uid="resilience-host-02", name="Resilience Host 2")
    now = datetime.now(UTC)
    evt_input = CreateEventInput(
        title="Resilience Cleanup Event",
        type=EventType.BIRTHDAY,
        venue=Venue(label="Garden Lawn", lat=28.6, lng=77.2),
        budget_inr=300000,
        functions=[
            FunctionInput(
                name="Lunch",
                meal="lunch",
                starts_at=now + timedelta(days=2),
                rules=[],
            )
        ],
    )
    event_row = await create_event(db, host, evt_input, now=now)
    fn_id = event_row.functions[0].id

    stale_time = now - timedelta(minutes=20)
    fake_plan_id = f"plan-killed-{uuid.uuid4().hex[:8]}"
    plan_row = PlanModel(
        id=fake_plan_id,
        function_id=fn_id,
        status="running",
        request={"functionId": fn_id, "daysLeft": 2},
        data_version="0.1.0",
        created_at=stale_time,
    )
    async with db.begin():
        db.add(plan_row)

    result = await run_cleanup_job(db, now=now)
    assert result["plansFailed"] >= 1

    async with db.begin():
        refetched = await db.get(PlanModel, fake_plan_id)
        assert refetched is not None
        await db.refresh(refetched)
        assert refetched.status == "failed"
        assert refetched.finished_at is not None
