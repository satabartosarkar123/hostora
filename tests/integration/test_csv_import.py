from collections.abc import Callable

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.db
async def test_csv_import_and_host_pages(
    client: httpx.AsyncClient,
    db: AsyncSession,
    fake_firebase: Callable[[str, str | None], str],
) -> None:
    _ = db
    # 1. Unauthenticated /app redirects to /app/login
    unauth = await client.get("/app", follow_redirects=False)
    assert unauth.status_code == 303
    assert unauth.headers["location"] == "/app/login"

    # 2. Login page renders
    login_page = await client.get("/app/login")
    assert login_page.status_code == 200
    assert "Sign in with Google" in login_page.text
    assert "Try demo" in login_page.text

    # 3. Firebase token login via POST /auth/session
    id_token = fake_firebase("firebase-uid-1", "Priya Host")
    sess_resp = await client.post("/auth/session", json={"idToken": id_token})
    assert sess_resp.status_code == 204

    # 4. Demo login via POST /auth/demo
    demo_resp = await client.post("/auth/demo", follow_redirects=False)
    assert demo_resp.status_code == 303
    assert demo_resp.headers["location"] == "/app"

    # 5. Extract CSRF token from /app/events/new
    new_ev_page = await client.get("/app/events/new")
    assert new_ev_page.status_code == 200
    csrf = new_ev_page.text.split('name="csrf_token" value="')[1].split('"')[0]

    # 6. Create an event with 2 functions via form POST
    create_resp = await client.post(
        "/app/events/new",
        data={
            "csrf_token": csrf,
            "title": "Test Host Event",
            "type": "wedding",
            "tradition": "interfaith",
            "fn_name": ["Lunch", "Reception", ""],
            "fn_starts_at": ["2026-12-01T12:00", "2026-12-01T19:00", ""],
            "fn_meal": ["lunch", "dinner", "dinner"],
            "fn_gate_mode": ["off", "soft", "off"],
            "fn_rules_1": ["halal", "no_beef"],
            "venue_label": "Grand Lawn, Mumbai",
            "venue_lat": "19.0760",
            "venue_lng": "72.8777",
            "pincode": "400001",
            "budget_inr": "80000",
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 303
    event_key = create_resp.headers["location"].rstrip("/").split("/")[-1]

    # 7. Dashboard renders
    dash_resp = await client.get(f"/app/e/{event_key}")
    assert dash_resp.status_code == 200
    assert "Test Host Event" in dash_resp.text
    assert "Tell your caterer:" in dash_resp.text

    # 8. Preview invalid CSV shows per-row errors and does not save
    bad_csv = (
        "name,phone,seats,functions\n"
        ",+910000011111,4,Lunch\n"
        "Bad Seats,+910000011112,99,Lunch\n"
        "Bad Function,+910000011113,3,UnknownFn\n"
    )
    bad_resp = await client.post(
        f"/app/e/{event_key}/guests/csv",
        data={"csrf_token": csrf, "action": "preview", "csv_text": bad_csv},
    )
    assert bad_resp.status_code == 422
    assert "Name is required" in bad_resp.text
    assert "Seats must be between 1 and 20" in bad_resp.text
    assert "Unknown function" in bad_resp.text

    # 9. Save valid 5 households from CSV
    good_csv = (
        "name,phone,seats,functions\n"
        "Sharma Family,+910000000001,4,Lunch;Reception\n"
        "Khan Family,+910000000002,3,Reception\n"
        "Iyer Family,,2,Lunch\n"
        "Fernandes Family,+910000000004,5,Lunch;Reception\n"
        "Gill Family,+910000000005,4,Reception\n"
    )
    save_resp = await client.post(
        f"/app/e/{event_key}/guests/csv",
        data={"csrf_token": csrf, "action": "save", "csv_text": good_csv},
        follow_redirects=False,
    )
    assert save_resp.status_code == 303

    guests_page = await client.get(f"/app/e/{event_key}/guests")
    assert guests_page.status_code == 200
    assert "Sharma Family" in guests_page.text
    assert "Gill Family" in guests_page.text

    # 10. Invite page & mark sent
    inv_page = await client.get(f"/app/e/{event_key}/invite")
    assert inv_page.status_code == 200
    assert "0 of 5 sent" in inv_page.text
    assert "https://wa.me/" in inv_page.text

    hh_id = inv_page.text.split('data-household-id="')[1].split('"')[0]
    sent_resp = await client.post(
        f"/app/e/{event_key}/invite/{hh_id}/sent",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert sent_resp.status_code == 200
    assert "Sent" in sent_resp.text

    # 11. Settings & account deletion
    settings_page = await client.get("/app/settings")
    assert settings_page.status_code == 200
    del_resp = await client.post(
        "/app/settings/delete",
        data={"csrf_token": csrf, "confirm": "DELETE"},
        follow_redirects=False,
    )
    assert del_resp.status_code == 303
    assert del_resp.headers["location"] == "/"
