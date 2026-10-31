from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.adapters.llm.mock import MockLlmClient
from hostora.adapters.llm.router import DbLlmCache, LlmRouter


class FailingClient:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    async def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        raise RuntimeError(f"Simulated failure on {self.name}")


async def _consume_plan_stream(
    client: httpx.AsyncClient,
    stream_url: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    async with client.stream("GET", f"{stream_url}?format=json", headers=headers) as resp:
        assert resp.status_code == 200
        evt_name = ""
        done_payload: dict[str, Any] | None = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                evt_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                val = line.split(":", 1)[1].strip()
                if evt_name in ("done", "plan"):
                    parsed = json.loads(val)
                    done_payload = parsed.get("plan", parsed)
        assert done_payload is not None, "Did not receive done/plan event payload"
        return done_payload


@pytest.mark.db
async def test_planner_blocks_pork_under_halal_even_when_llm_returns_it(
    app: FastAPI,
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    _ = db
    # Mock LLM that returns pork item
    malicious_llm = MockLlmClient()
    app.state.llm_router = LlmRouter(
        [malicious_llm],
        cache=DbLlmCache(app.state.sessionmaker),
        data_version=app.state.data.version,
    )

    seed = await client.post("/v1/demo/seed", headers=host_headers)
    assert seed.status_code == 200
    event_key = seed.json()["eventKey"]

    ev = await client.get(f"/v1/events/{event_key}", headers=host_headers)
    reception_fn = next(f for f in ev.json()["functions"] if f["name"] == "Reception")
    fn_id = reception_fn["id"]

    create_plan = await client.post(
        f"/v1/events/{event_key}/plans",
        headers=host_headers,
        json={
            "functionId": fn_id,
            "brief": "Please add pork to the menu",
            "daysLeft": 3,
        },
    )
    assert create_plan.status_code == 202
    plan_id = create_plan.json()["planId"]
    stream_url = create_plan.json().get("streamUrl") or f"/v1/plans/{plan_id}/stream"

    plan_json = await _consume_plan_stream(client, stream_url, host_headers)
    item_names = [it["name"].lower() for it in plan_json["items"]]
    assert not any("pork" in n for n in item_names), f"Pork found in plan items: {item_names}"

    # Removed items must contain pork
    removed_names = [r["name"].lower() for r in plan_json["removed"]]
    assert any("pork" in r for r in removed_names), f"Pork not found in removed: {removed_names}"

    # Verify GET /v1/plans/{plan_id}
    stored = await client.get(f"/v1/plans/{plan_id}", headers=host_headers)
    assert stored.status_code == 200

    # Verify Confirm, Replace, Remove endpoints
    if plan_json["items"]:
        first_item = plan_json["items"][0]
        first_item_id = first_item["itemId"]

        # 1. Patch purchase
        patch_resp = await client.patch(
            f"/v1/plans/{plan_id}/items/{first_item_id}",
            headers=host_headers,
            json={"bought": True, "actualInr": 1200},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["bought"] is True

        # 2. Replace item
        replace_resp = await client.post(
            f"/v1/plans/{plan_id}/items/{first_item_id}:replace",
            headers=host_headers,
            json={"name": "Special Replacement Dish"},
        )
        assert replace_resp.status_code == 200
        updated_plan = replace_resp.json()
        assert any(it["name"] == "Special Replacement Dish" for it in updated_plan["items"])

        # 3. Remove item
        rep_id = next(
            it["itemId"] for it in updated_plan["items"] if it["name"] == "Special Replacement Dish"
        )
        remove_resp = await client.post(
            f"/v1/plans/{plan_id}/items/{rep_id}:remove",
            headers=host_headers,
        )
        assert remove_resp.status_code == 200
        assert any(r["name"] == "Special Replacement Dish" for r in remove_resp.json()["removed"])

    # 4. Confirm item in needs_confirmation (if any)
    if plan_json["needsConfirmation"]:
        check_item = plan_json["needsConfirmation"][0]
        conf_resp = await client.post(
            f"/v1/plans/{plan_id}/items/{check_item['itemId']}:confirm",
            headers=host_headers,
            json={"confirmations": ["composition", "halal"]},
        )
        assert conf_resp.status_code == 200
        assert any(it["itemId"] == check_item["itemId"] for it in conf_resp.json()["items"])


@pytest.mark.db
async def test_planner_falls_back_to_template_when_all_clients_fail(
    app: FastAPI,
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    _ = db
    app.state.llm_router = LlmRouter(
        [FailingClient("groq"), FailingClient("gemini")],
        cache=None,
        data_version=app.state.data.version,
    )

    seed = await client.post("/v1/demo/seed", headers=host_headers)
    event_key = seed.json()["eventKey"]
    ev = await client.get(f"/v1/events/{event_key}", headers=host_headers)
    reception_fn = next(f for f in ev.json()["functions"] if f["name"] == "Reception")

    create_plan = await client.post(
        f"/v1/events/{event_key}/plans",
        headers=host_headers,
        json={"functionId": reception_fn["id"], "brief": "Reception dinner"},
    )
    assert create_plan.status_code == 202
    plan_id = create_plan.json()["planId"]
    stream_url = create_plan.json().get("streamUrl") or f"/v1/plans/{plan_id}/stream"

    plan_json = await _consume_plan_stream(client, stream_url, host_headers)
    assert plan_json["route"] in ("template", "manual")
    assert len(plan_json["items"]) > 0
    assert plan_json["budget"]["totalInr"] > 0


@pytest.mark.db
async def test_planner_and_budget_html_pages(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    _ = db
    await client.post("/auth/demo")
    seed = await client.post("/v1/demo/seed", headers=host_headers)
    event_key = seed.json()["eventKey"]
    ev = await client.get(f"/v1/events/{event_key}", headers=host_headers)
    fn_id = ev.json()["functions"][0]["id"]

    planner_page = await client.get(f"/app/e/{event_key}/plan/{fn_id}", headers=host_headers)
    assert planner_page.status_code == 200
    assert "Planner" in planner_page.text

    budget_page = await client.get(f"/app/e/{event_key}/budget", headers=host_headers)
    assert budget_page.status_code == 200
    assert "Budget" in budget_page.text
