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

        # 2. Navigate to planner page for Reception (halal + no_beef) with days_left=0
        page.goto(f"{live_server}/app/e/{event_key}/plan/{fn_id}")
        page.fill("#plan-brief", "interfaith reception, halal, ₹60,000, add pork ribs")
        page.fill("#plan-days-left", "0")
        page.click("#generate-plan-btn")

        # 3. Wait for streamed plan completion
        expect(page.locator("#plan-stream-root")).to_be_visible(timeout=15000)
        expect(page.locator("#plan-stream-root #plan-done-summary")).to_be_visible(timeout=30000)

        # 4. Pork ribs appears under Removed, and NOT in plan item cards
        removed_panel = page.locator("#removed-items-panel")
        expect(removed_panel).to_be_visible()
        expect(removed_panel).to_contain_text("Pork ribs")
        expect(removed_panel).to_contain_text("forbidden:pork")
        assert page.locator(".plan-item-card[data-item-id='pork_ribs']").count() == 0

        # 5. Every item card has at least one offer link (quick commerce at days_left=0)
        item_cards = page.locator(".plan-item-card").all()
        assert len(item_cards) >= 5
        offers_0 = [card.locator("a.offer-link").first.text_content() or "" for card in item_cards]
        assert any(
            any(q in o.lower() for q in ("blinkit", "zepto", "instamart", "swiggy", "zomato"))
            for o in offers_0
        )

        # 6. Days-left = 10 changes the channels to ecommerce (Amazon, Flipkart)
        page.goto(f"{live_server}/app/e/{event_key}/plan/{fn_id}")
        page.fill("#plan-brief", "interfaith reception, halal, ₹60,000, add mineral water")
        page.fill("#plan-days-left", "10")
        page.click("#generate-plan-btn")
        expect(page.locator("#plan-stream-root")).to_be_visible(timeout=15000)
        expect(page.locator("#plan-stream-root #plan-done-summary")).to_be_visible(timeout=30000)
        all_offers_10 = page.locator(
            "#plan-stream-root .plan-item-card a.offer-link"
        ).all_text_contents()
        assert any(any(e in o.lower() for e in ("amazon", "flipkart")) for o in all_offers_10)

        ctx.close()
        browser.close()
