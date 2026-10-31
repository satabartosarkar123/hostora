from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.security.tokens import sign_cron


@pytest.mark.db
async def test_cron_auth_rejects_missing_wrong_or_stale_and_accepts_valid(
    client: httpx.AsyncClient,
    db: AsyncSession,
    settings: Settings,
) -> None:
    _ = db
    secret = settings.cron_secret.get_secret_value()
    now_ts = int(datetime.now(UTC).timestamp())

    # 1. Missing headers -> 401
    r_missing = await client.post("/internal/cron/warm")
    assert r_missing.status_code == 401

    # 2. Wrong signature -> 401
    r_wrong = await client.post(
        "/internal/cron/warm",
        headers={"X-Cron-Timestamp": str(now_ts), "X-Cron-Signature": "deadbeef" * 8},
    )
    assert r_wrong.status_code == 401

    # 3. 6-minute-old signature (360s > 300s) -> 401
    stale_ts = now_ts - 360
    stale_sig = sign_cron(secret, stale_ts, "warm")
    r_stale = await client.post(
        "/internal/cron/warm",
        headers={"X-Cron-Timestamp": str(stale_ts), "X-Cron-Signature": stale_sig},
    )
    assert r_stale.status_code == 401

    # 4. Valid signature -> 200 for warm and cleanup
    valid_sig = sign_cron(secret, now_ts, "warm")
    r_ok = await client.post(
        "/internal/cron/warm",
        headers={"X-Cron-Timestamp": str(now_ts), "X-Cron-Signature": valid_sig},
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["job"] == "warm"

    cleanup_sig = sign_cron(secret, now_ts, "cleanup")
    r_clean = await client.post(
        "/internal/cron/cleanup",
        headers={"X-Cron-Timestamp": str(now_ts), "X-Cron-Signature": cleanup_sig},
    )
    assert r_clean.status_code == 200
    assert r_clean.json()["job"] == "cleanup"


@pytest.mark.db
async def test_logging_never_leaks_names_phones_or_tokens(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ = db
    import logging

    with caplog.at_level(logging.INFO):
        seed = await client.post("/v1/demo/seed", headers=host_headers)
        assert seed.status_code == 200
        event_key = seed.json()["eventKey"]

        join = await client.get("/demo/join", follow_redirects=False)
        token = join.headers["location"].rstrip("/").split("/")[-1]

        inv = await client.get(f"/v1/invites/{event_key}/{token}")
        hh_name = inv.json()["household"]["displayName"]
        fn_id = inv.json()["functions"][0]["id"]

        await client.put(
            f"/v1/invites/{event_key}/{token}/rsvp",
            headers={"Idempotency-Key": "log-privacy-check-01"},
            json={
                "functions": [
                    {
                        "functionId": fn_id,
                        "attending": "yes",
                        "adults": 1,
                        "children": 0,
                        "meals": {"veg": 1, "jain": 0, "nonVeg": 0},
                    }
                ]
            },
        )

    log_text = caplog.text
    assert hh_name not in log_text
    assert "+9100000" not in log_text
    assert token not in log_text
