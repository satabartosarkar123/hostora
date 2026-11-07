from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.e2e, pytest.mark.db]


def test_planner_flow_streams_categories_and_removes_pork_under_halal(
    live_server: str, browser_name: str
) -> None:
    headers = {"Authorization": "Demo demo-host"}
    seed_resp = httpx.post(f"{live_server}/v1/demo/seed", headers=headers, timeout=20.0)
    assert seed_resp.status_code == 200
    event_key = seed_resp.json()["eventKey"]

    ev_resp = httpx.get(f"{live_server}/v1/events/{event_key}", headers=headers, timeout=20.0)
    reception_fn = next(f for f in ev_resp.json()["functions"] if f["name"] == "Reception")
    fn_id = reception_fn["id"]

    with sync_playwright() as p:
        launcher = getattr(p, browser_name, p.chromium)
        browser = launcher.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        # 1. Demo login
        page.goto(f"{live_server}/app/login")
        page.locator("#try-demo-btn").click(force=True)
        page.wait_for_url(f"{live_server}/app")

        # 2. Party in 2 hours (120 minutes) flow on Reception (halal + no_beef)
        page.goto(f"{live_server}/app/e/{event_key}/plan/{fn_id}")
        page.fill("#plan-brief", "interfaith reception, halal, ₹60,000, add pork ribs")
        page.select_option("#plan-minutes-left", "120")
        page.click("#generate-plan-btn")

        # 3. Wait for streamed plan completion
        expect(page.locator("#plan-stream-root")).to_be_visible(timeout=15000)
        expect(page.locator("#plan-stream-root #plan-done-summary")).to_be_visible(timeout=30000)
        expect(page.locator("#urgent-mode-badge")).to_be_visible()

        # 4. Pork ribs appears under Removed, and NOT in plan item cards
        removed_panel = page.locator("#removed-items-panel")
        expect(removed_panel).to_be_visible()
        expect(removed_panel).to_contain_text("Pork ribs")
        expect(removed_panel).to_contain_text("forbidden:pork")
        assert page.locator(".plan-item-card[data-item-id='pork_ribs']").count() == 0

        # 5. At 120 minutes, only quick/food channels appear — NEVER Amazon or Flipkart
        all_offers_120 = page.locator(
            "#plan-stream-root .plan-item-card a.offer-link"
        ).all_text_contents()
        assert len(all_offers_120) >= 3
        assert any(
            any(q in o.lower() for q in ("blinkit", "zepto", "instamart", "swiggy", "zomato"))
            for o in all_offers_120
        )
        assert not any(any(e in o.lower() for e in ("amazon", "flipkart")) for o in all_offers_120)

        # 6. Build Carts summary shows arrival times and checkout links
        carts_panel = page.locator("#panel-build-carts")
        expect(carts_panel).to_be_visible()
        expect(page.locator("#carts-arrival-summary")).to_contain_text("Everything arrives by")
        assert page.locator("#panel-build-carts a.checkout-link").count() >= 1

        # 7. "Won't arrive in time" panel lists unfulfillable multi-day items with alternatives
        wont_arrive = page.locator("#panel-wont-arrive")
        expect(wont_arrive).to_be_visible()
        expect(wont_arrive).to_contain_text("Grounded alternatives that can arrive in time")
        assert page.locator("#panel-wont-arrive a.unfulfillable-alt-link").count() >= 1

        # 8. Next week (10,080 minutes) makes multi-day ecommerce (Amazon, Flipkart) feasible
        page.goto(f"{live_server}/app/e/{event_key}/plan/{fn_id}")
        page.fill("#plan-brief", "interfaith reception, halal, ₹60,000, add mineral water")
        page.select_option("#plan-minutes-left", "10080")
        page.click("#generate-plan-btn")
        expect(page.locator("#plan-stream-root")).to_be_visible(timeout=15000)
        expect(page.locator("#plan-stream-root #plan-done-summary")).to_be_visible(timeout=30000)
        all_offers_7d = page.locator(
            "#plan-stream-root .plan-item-card a.offer-link"
        ).all_text_contents()
        assert any(any(e in o.lower() for e in ("amazon", "flipkart")) for o in all_offers_7d)

        ctx.close()
        browser.close()
