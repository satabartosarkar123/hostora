from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from hostora.db.models import Event, EventLog, Household
from hostora.engine.prediction import ReplySeats, predict_arrivals

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.db


async def test_health_readyz_and_public_config(client: httpx.AsyncClient, db: AsyncSession) -> None:
    _ = db
    r_health = await client.get("/healthz")
    assert r_health.status_code == 200
    assert r_health.json() == {"ok": True}
    assert r_health.headers.get("referrer-policy") == "no-referrer"
    assert r_health.headers.get("x-content-type-options") == "nosniff"

    r_ready = await client.get("/readyz")
    assert r_ready.status_code == 200
    assert r_ready.json()["ok"] is True
    assert len(r_ready.json()["dataVersion"]) == 64

    r_v1 = await client.get("/v1/health")
    assert r_v1.status_code == 200
    assert r_v1.json()["ok"] is True

    r_cfg = await client.get("/v1/config/public")
    assert r_cfg.status_code == 200
    cfg = r_cfg.json()
    assert "halal" in cfg["rules"]
    assert "interfaith" in cfg["traditions"]


async def test_create_event_add_3_households_and_summary(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    """Test 1: Create an event, add 3 households -> summary shows 3 invited, 0 replied,
    prediction from no_reply seats only."""
    _ = db
    create_resp = await client.post(
        "/v1/events",
        headers=host_headers,
        json={
            "title": "Rohan & Priya Reception",
            "type": "wedding",
            "tradition": "interfaith",
            "venue": {"label": "Lotus Hall", "lat": 12.97, "lng": 77.59, "pincode": "560001"},
            "budgetInr": 120000,
            "functions": [
                {
                    "name": "Reception",
                    "startsAt": "2026-12-15T19:00:00+05:30",
                    "meal": "dinner",
                    "rules": ["halal"],
                    "gateMode": "soft",
                }
            ],
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    event_key = created["eventKey"]
    fn_id = created["functions"][0]["id"]

    hh_resp = await client.post(
        f"/v1/events/{event_key}/households:batch",
        headers=host_headers,
        json={
            "households": [
                {
                    "displayName": "Sharma Family",
                    "phone": "+919876543210",
                    "seatCap": 4,
                    "functionIds": [fn_id],
                    "language": "en",
                },
                {
                    "displayName": "Khan Family",
                    "phone": "9876543211",
                    "seatCap": 3,
                    "functionIds": [fn_id],
                    "language": "en",
                },
                {
                    "displayName": "Iyer Family",
                    "seatCap": 2,
                    "functionIds": [fn_id],
                    "language": "hi",
                },
            ]
        },
    )
    assert hh_resp.status_code == 200
    assert len(hh_resp.json()["households"]) == 3

    list_hh = await client.get(f"/v1/events/{event_key}/households", headers=host_headers)
    assert list_hh.status_code == 200
    hhs = list_hh.json()["households"]
    by_name = {h["displayName"]: h["phoneMasked"] for h in hhs}
    assert by_name["Sharma Family"] == "+91•••••10"
    assert by_name["Khan Family"] == "+91•••••11"
    assert by_name["Iyer Family"] is None
    assert "9876543210" not in json.dumps(hhs)

    summary_resp = await client.get(f"/v1/events/{event_key}/summary", headers=host_headers)
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    fn_sum = summary["functions"][0]
    assert fn_sum["invitedHouseholds"] == 3
    assert fn_sum["repliedHouseholds"] == 0
    assert fn_sum["seats"] == {"yes": 0, "maybe": 0, "noHouseholds": 0, "noReply": 9}
    expected_pred = predict_arrivals(ReplySeats(yes=0, maybe=0, no_reply=9))
    assert fn_sum["prediction"] == {
        "low": expected_pred.low,
        "likely": expected_pred.likely,
        "high": expected_pred.high,
        "p80": expected_pred.p80,
    }
    assert fn_sum["recommendedPlates"] == expected_pred.p80

    list_ev = await client.get("/v1/events", headers=host_headers)
    assert list_ev.status_code == 200
    assert any(e["eventKey"] == event_key for e in list_ev.json()["events"])

    detail_ev = await client.get(f"/v1/events/{event_key}", headers=host_headers)
    assert detail_ev.status_code == 200
    assert detail_ev.json()["title"] == "Rohan & Priya Reception"

    outcome_resp = await client.post(
        f"/v1/events/{event_key}/outcome",
        headers=host_headers,
        json={
            "functionId": fn_id,
            "actualCount": 8,
            "ranShort": [],
            "leftover": ["veg_biryani"],
        },
    )
    assert outcome_resp.status_code == 200
    assert outcome_resp.json()["ok"] is True

    prep_resp = await client.post(
        f"/v1/events/{event_key}/invites:prepare",
        headers=host_headers,
    )
    assert prep_resp.status_code == 200
    tok = prep_resp.json()[0]["link"].rsplit("/", 1)[-1]
    inv_resp = await client.get(f"/v1/invites/{event_key}/{tok}")
    assert inv_resp.status_code == 200
    assert inv_resp.json()["eventTitle"] == "Rohan & Priya Reception"


async def test_account_deletion_cascades_and_preserves_event_log_without_names(
    client: httpx.AsyncClient,
    db: AsyncSession,
    fake_firebase: Callable[[str, str | None], str],
) -> None:
    """Test 14: DELETE /v1/me removes the host's events and cascades;
    event_log rows remain and contain no names."""
    token = fake_firebase("uid-del-1", "Secret Host Name")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/v1/events",
        headers=headers,
        json={
            "title": "Private Birthday",
            "type": "birthday",
            "venue": {"label": "Home", "lat": 19.07, "lng": 72.87},
            "budgetInr": 25000,
            "functions": [
                {
                    "name": "Dinner",
                    "startsAt": "2026-12-20T20:00:00+05:30",
                    "meal": "dinner",
                }
            ],
        },
    )
    assert create_resp.status_code == 201
    event_key = create_resp.json()["eventKey"]
    fn_id = create_resp.json()["functions"][0]["id"]

    await client.post(
        f"/v1/events/{event_key}/households:batch",
        headers=headers,
        json={
            "households": [
                {
                    "displayName": "Confidential Guest Name",
                    "phone": "+919999988888",
                    "seatCap": 4,
                    "functionIds": [fn_id],
                }
            ]
        },
    )

    del_resp = await client.delete("/v1/me", headers=headers)
    assert del_resp.status_code == 204

    events_left = (await db.scalars(select(Event).where(Event.event_key == event_key))).all()
    hhs_left = (await db.scalars(select(Household).where(Household.event_key == event_key))).all()
    assert events_left == []
    assert hhs_left == []

    logs = (await db.scalars(select(EventLog).where(EventLog.event_key == event_key))).all()
    assert len(logs) >= 1
    serialized_logs = json.dumps([log.payload for log in logs])
    assert "Confidential Guest Name" not in serialized_logs
    assert "Secret Host Name" not in serialized_logs
    assert "9999988888" not in serialized_logs


async def test_demo_seed_and_join(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    _ = db
    seed_resp = await client.post("/v1/demo/seed", headers=host_headers)
    assert seed_resp.status_code == 200
    data = seed_resp.json()
    assert data["eventKey"] == "DEM01"
    assert len(data["inviteLinks"]) == 5

    join_resp = await client.get("/demo/join", follow_redirects=False)
    assert join_resp.status_code == 303
    assert join_resp.headers["location"].startswith("/i/DEM01/")
