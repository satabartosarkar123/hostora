from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.db


async def test_typed_code_resolve_rate_limit_per_ip(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    """Test 5: Typed-code resolve: the 11th attempt in a minute from one IP -> 429;
    another IP is unaffected."""
    _ = db
    c_resp = await client.post(
        "/v1/events",
        headers=host_headers,
        json={
            "title": "Code Resolve Event",
            "type": "festival",
            "venue": {"label": "Lawn", "lat": 12.9, "lng": 77.5},
            "budgetInr": 30000,
            "functions": [
                {"name": "Dinner", "startsAt": "2026-12-01T19:00:00+05:30", "meal": "dinner"}
            ],
        },
    )
    event_key = c_resp.json()["eventKey"]
    fn_id = c_resp.json()["functions"][0]["id"]

    hh_resp = await client.post(
        f"/v1/events/{event_key}/households:batch",
        headers=host_headers,
        json={
            "households": [
                {
                    "displayName": "Gill Family",
                    "seatCap": 4,
                    "functionIds": [fn_id],
                }
            ]
        },
    )
    valid_code = hh_resp.json()["households"][0]["code"]

    ip1_headers = {"X-Forwarded-For": "203.0.113.50"}
    for _ in range(10):
        res = await client.post(
            "/v1/invites:resolve",
            json={"code": valid_code},
            headers=ip1_headers,
        )
        assert res.status_code == 200
        assert res.json()["eventKey"] == event_key

    res_11 = await client.post(
        "/v1/invites:resolve",
        json={"code": valid_code},
        headers=ip1_headers,
    )
    assert res_11.status_code == 429
    assert res_11.headers.get("retry-after") == "60"

    ip2_headers = {"X-Forwarded-For": "203.0.113.51"}
    res_other_ip = await client.post(
        "/v1/invites:resolve",
        json={"code": valid_code},
        headers=ip2_headers,
    )
    assert res_other_ip.status_code == 200
    assert res_other_ip.json()["eventKey"] == event_key
