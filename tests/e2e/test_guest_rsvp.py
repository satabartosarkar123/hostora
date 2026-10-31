from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.e2e, pytest.mark.db]


def test_guest_rsvp_mobile_and_no_js(live_server: str, browser_name: str) -> None:
    headers = {"Authorization": "Demo demo-host"}
    c_resp = httpx.post(
        f"{live_server}/v1/events",
        headers=headers,
        json={
            "title": "E2E Guest Wedding",
            "type": "wedding",
            "tradition": "interfaith",
            "venue": {"label": "Banyan Lawn", "lat": 12.9716, "lng": 77.5946},
            "budgetInr": 60000,
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
    assert c_resp.status_code == 201
    event_key = c_resp.json()["eventKey"]
    fn_id = c_resp.json()["functions"][0]["id"]

    httpx.post(
        f"{live_server}/v1/events/{event_key}/households:batch",
        headers=headers,
        json={
            "households": [
                {
                    "displayName": "Sharma Family",
                    "seatCap": 6,
                    "functionIds": [fn_id],
                    "language": "en",
                },
                {
                    "displayName": "NoJS Family",
                    "seatCap": 4,
                    "functionIds": [fn_id],
                    "language": "en",
                },
            ]
        },
    ).raise_for_status()

    invites = httpx.post(
        f"{live_server}/v1/events/{event_key}/invites:prepare",
        headers=headers,
    ).json()
    sharma_inv = next(i for i in invites if i["displayName"] == "Sharma Family")
    nojs_inv = next(i for i in invites if i["displayName"] == "NoJS Family")

    with sync_playwright() as p:
        launcher = getattr(p, browser_name, p.chromium)
        browser = launcher.launch(headless=True)

        # Hindi language check: ?lang=hi shows Hindi
        hi_ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        hi_page = hi_ctx.new_page()
        hi_page.goto(f"{sharma_inv['link']}?lang=hi")
        assert "निमंत्रण" in hi_page.content()
        hi_ctx.close()

        # 1. Mobile viewport with JS enabled: reply 3 adults + 1 child, meals 2 veg + 2 non-veg
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        page = ctx.new_page()
        page.goto(sharma_inv["link"])
        assert "Sharma Family" in page.content()

        page.fill(f"#adults_{fn_id}", "3")
        page.fill(f"#children_{fn_id}", "1")
        page.fill(f"#veg_{fn_id}", "2")
        page.fill(f"#jain_{fn_id}", "0")
        page.fill(f"#non_veg_{fn_id}", "2")
        page.click("button[type='submit']")
        page.wait_for_url("**/done")
        assert "Thank you! Your reply is saved." in page.content()

        # 2. Change reply flow
        page.click("a:has-text('Change my reply')")
        page.wait_for_url(f"**/i/{event_key}/*")
        assert page.input_value(f"#adults_{fn_id}") == "3"
        assert page.input_value(f"#children_{fn_id}") == "1"
        page.fill(f"#adults_{fn_id}", "2")
        page.fill(f"#children_{fn_id}", "0")
        page.fill(f"#veg_{fn_id}", "2")
        page.fill(f"#non_veg_{fn_id}", "0")
        page.click("button[type='submit']")
        page.wait_for_url("**/done")
        assert "Thank you! Your reply is saved." in page.content()

        # 3. Typed-code flow from /r
        page.goto(f"{live_server}/r")
        page.fill("#resolve-code-input", sharma_inv["code"].lower().replace("-", " "))
        page.click("button[type='submit']")
        page.wait_for_url(f"**/i/{event_key}/*")
        assert "Sharma Family" in page.content()
        ctx.close()

        # 4. Works with JavaScript disabled
        nojs_ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            java_script_enabled=False,
        )
        nojs_page = nojs_ctx.new_page()
        nojs_page.goto(nojs_inv["link"])
        nojs_page.fill(f"#adults_{fn_id}", "2")
        nojs_page.fill(f"#children_{fn_id}", "0")
        nojs_page.fill(f"#veg_{fn_id}", "1")
        nojs_page.fill(f"#jain_{fn_id}", "0")
        nojs_page.fill(f"#non_veg_{fn_id}", "1")
        nojs_page.click("button[type='submit']")
        nojs_page.wait_for_url("**/done")
        assert "Thank you! Your reply is saved." in nojs_page.content()
        nojs_ctx.close()

        browser.close()
