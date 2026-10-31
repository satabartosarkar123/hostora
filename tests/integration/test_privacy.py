import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.adapters.llm.mock import MockLlmClient
from hostora.adapters.llm.router import LlmRouter
from hostora.db.models import Household


@pytest.mark.db
async def test_privacy_no_household_names_phones_or_digits_in_llm_prompts(
    app: FastAPI,
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    spy_llm = MockLlmClient()
    app.state.llm_router = LlmRouter(
        [spy_llm],
        cache=None,
        data_version=app.state.data.version,
    )

    seed = await client.post("/v1/demo/seed", headers=host_headers)
    assert seed.status_code == 200
    event_key = seed.json()["eventKey"]

    async with db.begin():
        hh_names = list(
            (
                await db.scalars(
                    select(Household.display_name).where(Household.event_key == event_key)
                )
            ).all()
        )
    assert len(hh_names) == 60

    ev = await client.get(f"/v1/events/{event_key}", headers=host_headers)
    fn_id = ev.json()["functions"][1]["id"]

    # Include a household name, a phone number, and an email in the brief
    sample_name = hh_names[0]
    raw_brief = (
        f"Please seat {sample_name} near the stage and call +91 98765 43210 "
        "or email catering@example.com for spicy biryani."
    )

    create_plan = await client.post(
        f"/v1/events/{event_key}/plans",
        headers=host_headers,
        json={"functionId": fn_id, "brief": raw_brief},
    )
    assert create_plan.status_code == 202
    stream_url = create_plan.json()["streamUrl"]

    async with client.stream("GET", f"{stream_url}?format=json", headers=host_headers) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass

    assert len(spy_llm.recorded_prompts) >= 1
    for sys_prompt, user_prompt in spy_llm.recorded_prompts:
        combined = f"{sys_prompt}\n{user_prompt}"
        assert "98765" not in combined
        assert "43210" not in combined
        assert "catering@example.com" not in combined
        for name in hh_names:
            assert name.lower() not in combined.lower()
