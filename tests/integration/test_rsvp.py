from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from hostora.db.models import EventLog, Rsvp

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.db


async def _setup_event_with_household(
    client: httpx.AsyncClient, host_headers: dict[str, str]
) -> tuple[str, str, str, str]:
    c_resp = await client.post(
        "/v1/events",
        headers=host_headers,
        json={
            "title": "Test Wedding",
            "type": "wedding",
            "venue": {"label": "Grand Hall", "lat": 28.61, "lng": 77.20},
            "budgetInr": 50000,
            "functions": [
                {
                    "name": "Lunch",
                    "startsAt": "2026-12-10T12:30:00+05:30",
                    "meal": "lunch",
                    "gateMode": "soft",
                },
                {
                    "name": "Dinner",
                    "startsAt": "2026-12-10T19:30:00+05:30",
                    "meal": "dinner",
                    "gateMode": "off",
                },
            ],
        },
    )
    assert c_resp.status_code == 201
    event_key = c_resp.json()["eventKey"]
    fn1_id = c_resp.json()["functions"][0]["id"]
    fn2_id = c_resp.json()["functions"][1]["id"]

    await client.post(
        f"/v1/events/{event_key}/households:batch",
        headers=host_headers,
        json={
            "households": [
                {
                    "displayName": "Mehta Household",
                    "phone": "+919811122233",
                    "seatCap": 4,
                    "functionIds": [fn1_id],
                    "language": "en",
                }
            ]
        },
    )
    prep = await client.post(
        f"/v1/events/{event_key}/invites:prepare",
        headers=host_headers,
    )
    assert prep.status_code == 200
    invite = prep.json()[0]
    token = invite["link"].rsplit("/", 1)[-1]
    return event_key, fn1_id, fn2_id, token


async def test_rsvp_idempotency_and_conflicting_payload(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    """Test 2: Same Idempotency-Key twice -> one rsvp row per function, identical responses,
    one rsvp.submitted log row. Same key with a different body -> 422 field: 'idempotencyKey'."""
    event_key, fn1_id, _, token = await _setup_event_with_household(client, host_headers)

    payload = {
        "functions": [
            {
                "functionId": fn1_id,
                "attending": "yes",
                "adults": 2,
                "children": 1,
                "meals": {"veg": 2, "jain": 0, "nonVeg": 1},
            }
        ],
        "language": "en",
    }
    headers = {"Idempotency-Key": "idem-key-0001"}
    r1 = await client.put(f"/v1/invites/{event_key}/{token}/rsvp", json=payload, headers=headers)
    r2 = await client.put(f"/v1/invites/{event_key}/{token}/rsvp", json=payload, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    assert len(r1.json()["passes"]) == 1  # gateMode is soft on fn1

    rsvp_rows = (await db.scalars(select(Rsvp).where(Rsvp.function_id == fn1_id))).all()
    assert len(rsvp_rows) == 1
    assert rsvp_rows[0].version == 1

    log_rows = (
        await db.scalars(
            select(EventLog).where(
                EventLog.event_key == event_key, EventLog.kind == "rsvp.submitted"
            )
        )
    ).all()
    assert len(log_rows) == 1

    diff_payload = {
        "functions": [
            {
                "functionId": fn1_id,
                "attending": "yes",
                "adults": 3,
                "children": 0,
                "meals": {"veg": 3, "jain": 0, "nonVeg": 0},
            }
        ],
        "language": "en",
    }
    r_conflict = await client.put(
        f"/v1/invites/{event_key}/{token}/rsvp", json=diff_payload, headers=headers
    )
    assert r_conflict.status_code == 422
    assert r_conflict.json()["field"] == "idempotencyKey"


async def test_rsvp_cross_field_validation_errors(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    """Test 3: Seats over the cap -> 422 field: 'adults'; meals not adding up -> 422 field: 'meals';
    function not invited -> 422 field: 'functions'."""
    _ = db
    event_key, fn1_id, fn2_id, token = await _setup_event_with_household(client, host_headers)

    r_over = await client.put(
        f"/v1/invites/{event_key}/{token}/rsvp",
        headers={"Idempotency-Key": "key-over-cap"},
        json={
            "functions": [
                {
                    "functionId": fn1_id,
                    "attending": "yes",
                    "adults": 4,
                    "children": 1,
                    "meals": {"veg": 5, "jain": 0, "nonVeg": 0},
                }
            ]
        },
    )
    assert r_over.status_code == 422
    assert r_over.json()["field"] == "adults"

    r_meals = await client.put(
        f"/v1/invites/{event_key}/{token}/rsvp",
        headers={"Idempotency-Key": "key-bad-meals"},
        json={
            "functions": [
                {
                    "functionId": fn1_id,
                    "attending": "yes",
                    "adults": 2,
                    "children": 1,
                    "meals": {"veg": 1, "jain": 0, "nonVeg": 1},
                }
            ]
        },
    )
    assert r_meals.status_code == 422
    assert r_meals.json()["field"] == "meals"

    r_uninvited = await client.put(
        f"/v1/invites/{event_key}/{token}/rsvp",
        headers={"Idempotency-Key": "key-uninvited"},
        json={
            "functions": [
                {
                    "functionId": fn2_id,
                    "attending": "yes",
                    "adults": 2,
                    "children": 0,
                    "meals": {"veg": 2, "jain": 0, "nonVeg": 0},
                }
            ]
        },
    )
    assert r_uninvited.status_code == 422
    assert r_uninvited.json()["field"] == "functions"


async def test_identical_404_for_tampered_token_unknown_event_and_unknown_household(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    """Test 4: Tampered link token, unknown event and unknown household -> the same 404 body."""
    _ = db
    event_key, _, _, token = await _setup_event_with_household(client, host_headers)
    tampered = ("A" if token[0] != "A" else "B") + token[1:]

    r_tampered = await client.get(f"/v1/invites/{event_key}/{tampered}")
    r_unknown_event = await client.get(f"/v1/invites/ZZZZZ/{token}")

    empty_event_resp = await client.post(
        "/v1/events",
        headers=host_headers,
        json={
            "title": "Empty Event",
            "type": "other",
            "venue": {"label": "Hall", "lat": 12.0, "lng": 77.0},
            "budgetInr": 1000,
            "functions": [
                {"name": "Main", "startsAt": "2026-12-12T18:00:00+05:30", "meal": "dinner"}
            ],
        },
    )
    empty_key = empty_event_resp.json()["eventKey"]
    r_unknown_hh = await client.get(f"/v1/invites/{empty_key}/{token}")

    assert r_tampered.status_code == 404
    assert r_unknown_event.status_code == 404
    assert r_unknown_hh.status_code == 404
    assert r_tampered.json() == r_unknown_event.json() == r_unknown_hh.json()


async def test_concurrent_20_rsvp_requests_different_and_same_keys(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    """Test 10: Concurrency: 20 concurrent RSVP requests for one household with different keys
    -> rsvp.version == 20 and no errors; 20 concurrent requests with the same key ->
    exactly one rsvp.submitted log row, 20 identical responses."""
    event_key, fn1_id, _, token = await _setup_event_with_household(client, host_headers)

    payload = {
        "functions": [
            {
                "functionId": fn1_id,
                "attending": "yes",
                "adults": 2,
                "children": 0,
                "meals": {"veg": 2, "jain": 0, "nonVeg": 0},
            }
        ],
        "language": "en",
    }

    same_key_responses = await asyncio.gather(
        *[
            client.put(
                f"/v1/invites/{event_key}/{token}/rsvp",
                json=payload,
                headers={"Idempotency-Key": "concurrent-same-key"},
            )
            for _ in range(20)
        ]
    )
    assert all(r.status_code == 200 for r in same_key_responses)
    first_body = same_key_responses[0].json()
    assert all(r.json() == first_body for r in same_key_responses)

    logs_after_same = (
        await db.scalars(
            select(EventLog).where(
                EventLog.event_key == event_key, EventLog.kind == "rsvp.submitted"
            )
        )
    ).all()
    assert len(logs_after_same) == 1

    diff_key_responses = await asyncio.gather(
        *[
            client.put(
                f"/v1/invites/{event_key}/{token}/rsvp",
                json=payload,
                headers={"Idempotency-Key": f"concurrent-diff-key-{i:02d}"},
            )
            for i in range(19)
        ]
    )
    assert all(r.status_code == 200 for r in diff_key_responses)

    rsvp_row = (await db.scalars(select(Rsvp).where(Rsvp.function_id == fn1_id))).one()
    assert rsvp_row.version == 20
