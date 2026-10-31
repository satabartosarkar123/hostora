from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from hostora.main import create_app

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.config import Settings


@pytest.mark.db
async def test_access_control_host_b_cannot_read_or_modify_host_a(
    client: httpx.AsyncClient,
    db: AsyncSession,
    fake_firebase: Callable[[str, str | None], str],
    host_headers: dict[str, str],
) -> None:
    _ = db
    # Host A (demo-host) creates an event, adds a household, and creates a plan run
    ev_resp = await client.post(
        "/v1/events",
        headers=host_headers,
        json={
            "title": "Host A Private Event",
            "type": "wedding",
            "venue": {"label": "Venue A", "lat": 28.6139, "lng": 77.2090},
            "budgetInr": 50000,
            "functions": [
                {
                    "name": "Dinner",
                    "startsAt": "2026-12-20T18:00:00Z",
                    "meal": "dinner",
                    "rules": ["pure_veg"],
                    "gateMode": "soft",
                }
            ],
        },
    )
    assert ev_resp.status_code == 201
    ev_key = ev_resp.json()["eventKey"]
    fn_id = ev_resp.json()["functions"][0]["id"]

    plan_resp = await client.post(
        f"/v1/events/{ev_key}/plans",
        headers=host_headers,
        json={"functionId": fn_id, "brief": "veg buffet", "daysLeft": 5},
    )
    assert plan_resp.status_code == 202
    plan_id = plan_resp.json()["planId"]

    # Host B authenticates via Firebase bearer token
    token_b = fake_firebase("host-b-uid", "Host B")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Compare against an unknown event / plan to ensure identical 404 body
    unknown_ev = await client.get("/v1/events/UNKN0", headers=headers_b)
    assert unknown_ev.status_code == 404
    expected_ev_404 = unknown_ev.json()

    unknown_plan = await client.get("/v1/plans/pl_UNKNOWN000", headers=headers_b)
    assert unknown_plan.status_code == 404
    expected_plan_404 = unknown_plan.json()

    for get_url in (
        f"/v1/events/{ev_key}",
        f"/v1/events/{ev_key}/households",
        f"/v1/events/{ev_key}/summary",
    ):
        r = await client.get(get_url, headers=headers_b)
        assert r.status_code == 404, get_url
        assert r.json() == expected_ev_404

    r_plan = await client.get(f"/v1/plans/{plan_id}", headers=headers_b)
    assert r_plan.status_code == 404
    assert r_plan.json() == expected_plan_404

    r_plan_create = await client.post(
        f"/v1/events/{ev_key}/plans",
        headers=headers_b,
        json={"functionId": fn_id, "brief": "veg", "daysLeft": 3},
    )
    assert r_plan_create.status_code == 404
    assert r_plan_create.json() == expected_ev_404

    r_gate = await client.post(
        f"/v1/events/{ev_key}/gate-links",
        headers=headers_b,
        json={"functionId": fn_id, "hours": 24},
    )
    assert r_gate.status_code == 404
    assert r_gate.json() == expected_ev_404


@pytest.mark.db
async def test_csrf_required_for_cookie_authenticated_mutating_methods(
    client: httpx.AsyncClient,
    db: AsyncSession,
) -> None:
    _ = db
    login_resp = await client.post("/auth/demo", headers={"Accept": "application/json"})
    assert login_resp.status_code == 204
    assert "hostora_session" in client.cookies

    # Without X-CSRF-Token -> 403 on POST, PUT, PATCH, DELETE
    r_post = await client.post(
        "/v1/events",
        json={
            "title": "CSRF Test",
            "type": "party",
            "venue": {"label": "Hall", "lat": 28.6139, "lng": 77.2090},
            "budgetInr": 20000,
            "functions": [
                {
                    "name": "Party",
                    "startsAt": "2026-12-20T18:00:00Z",
                    "meal": "dinner",
                }
            ],
        },
    )
    assert r_post.status_code == 403

    r_patch = await client.patch(
        "/v1/plans/pl_FAKE/items/item_1",
        json={"bought": True},
    )
    assert r_patch.status_code == 403

    r_delete = await client.delete("/v1/me")
    assert r_delete.status_code == 403


@pytest.mark.db
async def test_session_cookie_flags_in_prod_and_security_headers(
    settings: Settings,
    db: AsyncSession,
    fake_firebase: Callable[[str, str | None], str],
) -> None:
    _ = db
    prod_settings = settings.model_copy(update={"env": "prod", "demo_mode": False})
    prod_app = create_app(prod_settings)
    try:
        transport = ASGITransport(app=prod_app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as prod_client:
            id_token = fake_firebase("prod-uid", "Prod Host")
            r_sess = await prod_client.post("/auth/session", json={"idToken": id_token})
            assert r_sess.status_code == 204
            set_cookie = r_sess.headers.get("set-cookie", "").lower()
            assert "httponly" in set_cookie
            assert "secure" in set_cookie
            assert "samesite=lax" in set_cookie

            # Clear session cookie so unauthenticated checks below test unauthenticated / Demo auth
            prod_client.cookies.clear()

            # Check security headers on HTML page, JSON response, 404, and static file
            for path in ("/", "/healthz", "/v1/events/NOPE0", "/static/css/app.css"):
                resp = await prod_client.get(path)
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                assert "content-security-policy" in hdrs, path
                assert hdrs.get("x-content-type-options") == "nosniff", path
                assert hdrs.get("referrer-policy") == "no-referrer", path
                assert hdrs.get("x-frame-options") == "DENY", path
                assert "max-age=31536000" in hdrs.get("strict-transport-security", ""), path

            # Verify demo endpoints are completely off when DEMO_MODE=false
            assert (await prod_client.post("/v1/demo/seed")).status_code in (401, 404)
            assert (
                await prod_client.post("/v1/demo/seed", headers={"Authorization": "Demo demo-host"})
            ).status_code == 401
            assert (await prod_client.get("/demo/join")).status_code == 404
            assert (await prod_client.post("/auth/demo")).status_code == 404
            assert (
                await prod_client.get("/v1/events", headers={"Authorization": "Demo demo-host"})
            ).status_code == 401
    finally:
        if getattr(prod_app.state, "engine", None) is not None:
            await prod_app.state.engine.dispose()


@pytest.mark.db
async def test_xss_escaping_sql_injection_phone_encryption_and_gate_privacy(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
) -> None:
    # Create event
    ev_resp = await client.post(
        "/v1/events",
        headers=host_headers,
        json={
            "title": "Security Suite Event",
            "type": "wedding",
            "venue": {"label": "Grand Hall", "lat": 28.6139, "lng": 77.2090},
            "budgetInr": 80000,
            "functions": [
                {
                    "name": "Reception",
                    "startsAt": "2026-12-20T18:00:00Z",
                    "meal": "dinner",
                    "rules": ["halal", "no_alcohol"],
                    "gateMode": "soft",
                }
            ],
        },
    )
    assert ev_resp.status_code == 201
    ev_key = ev_resp.json()["eventKey"]
    fn_id = ev_resp.json()["functions"][0]["id"]

    xss_name_1 = "<script>alert(1)</script>"
    xss_name_2 = '"><img src=x onerror=alert(1)>'
    secret_phone = "9876543219"

    batch_resp = await client.post(
        f"/v1/events/{ev_key}/households:batch",
        headers=host_headers,
        json={
            "households": [
                {
                    "displayName": xss_name_1,
                    "phone": secret_phone,
                    "seatCap": 4,
                    "functionIds": [fn_id],
                    "language": "en",
                },
                {
                    "displayName": xss_name_2,
                    "seatCap": 3,
                    "functionIds": [fn_id],
                    "language": "en",
                },
            ]
        },
    )
    assert batch_resp.status_code == 200

    # Phone at rest check: read raw household table columns
    raw_rows = (
        await db.execute(
            text("SELECT key, display_name, phone_enc::text FROM household WHERE event_key = :ek"),
            {"ek": ev_key},
        )
    ).fetchall()
    assert len(raw_rows) == 2
    for row in raw_rows:
        for col in row:
            if col is not None:
                assert secret_phone not in str(col)

    # Prepare invites and submit RSVP so household appears on gate manifest
    prep_resp = await client.post(
        f"/v1/events/{ev_key}/invites:prepare",
        headers=host_headers,
    )
    assert prep_resp.status_code == 200
    invites = prep_resp.json()
    inv_1 = next(inv for inv in invites if inv["displayName"] == xss_name_1)
    token_1 = inv_1["link"].rstrip("/").split("/")[-1]

    rsvp_resp = await client.put(
        f"/v1/invites/{ev_key}/{token_1}/rsvp",
        headers={"Idempotency-Key": "sec-idem-1"},
        json={
            "language": "en",
            "functions": [
                {
                    "functionId": fn_id,
                    "attending": "yes",
                    "adults": 2,
                    "children": 0,
                    "meals": {"veg": 1, "nonVeg": 1, "jain": 0},
                    "dietaryRules": ["halal", "jain"],
                    "allergies": ["peanuts", "shellfish"],
                }
            ],
        },
    )
    assert rsvp_resp.status_code == 200

    # Check XSS escaping on invite HTML page
    inv_html = (await client.get(f"/i/{ev_key}/{token_1}")).text
    assert "<script>alert(1)</script>" not in inv_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in inv_html

    # Login as demo host via session cookie to check host HTML pages (dashboard, guests)
    await client.post("/auth/demo", headers={"Accept": "application/json"})
    dash_html = (await client.get(f"/app/e/{ev_key}")).text
    assert "<script>alert(1)</script>" not in dash_html
    assert '"><img src=x onerror=alert(1)>' not in dash_html

    guests_html = (await client.get(f"/app/e/{ev_key}/guests")).text
    assert "<script>alert(1)</script>" not in guests_html
    assert '"><img src=x onerror=alert(1)>' not in guests_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in guests_html

    # Check gate manifest privacy: only passId, displayName, seats
    gate_link_resp = await client.post(
        f"/v1/events/{ev_key}/gate-links",
        headers=host_headers,
        json={"functionId": fn_id, "hours": 24},
    )
    assert gate_link_resp.status_code == 200
    gate_token = gate_link_resp.json()["url"].split("?t=")[1]
    manifest_resp = await client.get(f"/v1/gate/{ev_key}/manifest?t={gate_token}")
    assert manifest_resp.status_code == 200
    manifest_data = manifest_resp.json()
    manifest_raw = manifest_resp.text.lower()
    assert len(manifest_data["passes"]) == 1
    for p in manifest_data["passes"]:
        assert set(p.keys()) == {"passId", "displayName", "seats"}
    for forbidden in ("halal", "jain", "peanut", "shellfish", secret_phone):
        assert forbidden not in manifest_raw

    # SQL injection attempts
    sqli_resolve = await client.post("/v1/invites/resolve", json={"code": "' OR 1=1 --"})
    assert sqli_resolve.status_code in (404, 422)
    sqli_event = await client.get(
        "/v1/events/DEM01'; DROP TABLE event;--",
        headers=host_headers,
    )
    assert sqli_event.status_code in (404, 422)
    # Confirm event table is intact
    ev_check = await client.get(f"/v1/events/{ev_key}", headers=host_headers)
    assert ev_check.status_code == 200


@pytest.mark.db
async def test_logs_contain_no_pii_and_body_limits_enforced(
    client: httpx.AsyncClient,
    db: AsyncSession,
    host_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ = db
    caplog.set_level(logging.INFO)

    ev_resp = await client.post(
        "/v1/events",
        headers=host_headers,
        json={
            "title": "Log & Limit Event",
            "type": "birthday",
            "venue": {"label": "Roof", "lat": 28.6139, "lng": 77.2090},
            "budgetInr": 40000,
            "functions": [
                {
                    "name": "Dinner",
                    "startsAt": "2026-12-20T18:00:00Z",
                    "meal": "dinner",
                    "rules": ["pure_veg"],
                }
            ],
        },
    )
    assert ev_resp.status_code == 201
    ev_key = ev_resp.json()["eventKey"]
    fn_id = ev_resp.json()["functions"][0]["id"]

    hh_name = "UniqueSecretHouseholdNameXYZ"
    hh_phone = "9123456780"
    secret_brief = "UniqueSecretBriefMarker999"

    await client.post(
        f"/v1/events/{ev_key}/households:batch",
        headers=host_headers,
        json={
            "households": [
                {
                    "displayName": hh_name,
                    "phone": hh_phone,
                    "seatCap": 4,
                    "functionIds": [fn_id],
                    "language": "en",
                }
            ]
        },
    )
    prep = (await client.post(f"/v1/events/{ev_key}/invites:prepare", headers=host_headers)).json()
    inv_token = prep[0]["link"].rstrip("/").split("/")[-1]

    await client.put(
        f"/v1/invites/{ev_key}/{inv_token}/rsvp",
        headers={"Idempotency-Key": "log-idem-1"},
        json={
            "language": "en",
            "functions": [
                {
                    "functionId": fn_id,
                    "attending": "yes",
                    "adults": 2,
                    "children": 0,
                    "meals": {"veg": 2, "nonVeg": 0, "jain": 0},
                    "dietaryRules": ["pure_veg"],
                    "allergies": [],
                }
            ],
        },
    )

    plan_create = await client.post(
        f"/v1/events/{ev_key}/plans",
        headers=host_headers,
        json={"functionId": fn_id, "brief": secret_brief, "daysLeft": 4},
    )
    plan_id = plan_create.json()["planId"]
    await client.get(f"/v1/plans/{plan_id}/stream?format=json", headers=host_headers)

    log_blob = "\n".join(r.getMessage() for r in caplog.records)
    for sensitive in (hh_name, hh_phone, secret_brief, inv_token, "hostora_session"):
        assert sensitive not in log_blob

    # Body limits: 1,001-char brief -> 422
    r_brief = await client.post(
        f"/v1/events/{ev_key}/plans",
        headers=host_headers,
        json={"functionId": fn_id, "brief": "a" * 1001, "daysLeft": 4},
    )
    assert r_brief.status_code == 422

    # 501 households in a batch -> 422
    r_501 = await client.post(
        f"/v1/events/{ev_key}/households:batch",
        headers=host_headers,
        json={
            "households": [
                {
                    "displayName": f"Guest {i}",
                    "seatCap": 2,
                    "functionIds": [fn_id],
                    "language": "en",
                }
                for i in range(501)
            ]
        },
    )
    assert r_501.status_code == 422

    # 21 seats -> 422
    r_21 = await client.post(
        f"/v1/events/{ev_key}/households:batch",
        headers=host_headers,
        json={
            "households": [
                {
                    "displayName": "Too Many Seats",
                    "seatCap": 21,
                    "functionIds": [fn_id],
                    "language": "en",
                }
            ]
        },
    )
    assert r_21.status_code == 422
