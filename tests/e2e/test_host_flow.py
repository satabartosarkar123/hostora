from __future__ import annotations

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.e2e, pytest.mark.db]


def test_host_flow_live_sse_updates_dashboard(live_server: str, browser_name: str) -> None:
    with sync_playwright() as p:
        launcher = getattr(p, browser_name, p.chromium)
        browser = launcher.launch(headless=True)
        host_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        host_page = host_ctx.new_page()

        # 1. Demo login
        host_page.goto(f"{live_server}/app/login")
        host_page.locator("#try-demo-btn").click(force=True)
        host_page.wait_for_url(f"{live_server}/app")

        # 2. Create an event with 2 functions
        host_page.click("a:has-text('Create Event')")
        host_page.wait_for_url(f"{live_server}/app/events/new")
        host_page.fill("#event-title", "Arjun & Sara E2E Host Flow")
        host_page.fill("#fn-name-0", "Lunch")
        host_page.fill("#fn-name-1", "Reception")
        host_page.click("#create-event-submit")
        host_page.wait_for_url("**/app/e/*")
        dashboard_url = host_page.url

        # 3. Import 5 households from CSV
        host_page.click("a:has-text('Guests')")
        host_page.wait_for_url("**/guests")
        csv_data = (
            "name,phone,seats,functions\n"
            "Sharma Family,+910000000001,4,Lunch;Reception\n"
            "Khan Family,+910000000002,3,Lunch;Reception\n"
            "Iyer Family,+910000000003,2,Lunch\n"
            "Fernandes Family,+910000000004,5,Reception\n"
            "Gill Family,+910000000005,4,Lunch;Reception\n"
        )
        host_page.fill("#csv-text", csv_data)
        host_page.click("#csv-save-btn")
        host_page.wait_for_url("**/guests")
        assert "Sharma Family" in host_page.content()
        assert "Gill Family" in host_page.content()

        # 4. Get Sharma Family's invite link from the invite page
        host_page.click("a:has-text('Send Invites')")
        host_page.wait_for_url("**/invite")
        first_invite_link = host_page.locator(
            "tr:has-text('Sharma Family') a.invite-link"
        ).first.get_attribute("href")
        assert first_invite_link is not None

        # 5. Open dashboard and wait for SSE connection to be open
        host_page.goto(dashboard_url)
        host_page.wait_for_function(
            "() => document.getElementById('live-summary-container')"
            "?.__eventSource?.readyState === 1",
            timeout=10000,
        )
        expect(
            host_page.locator(".function-summary-card").first.locator(".replied-households")
        ).to_have_text("0")

        # 6. Open the invite link in a second browser context and reply
        guest_ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        guest_page = guest_ctx.new_page()
        guest_page.goto(first_invite_link)
        guest_page.wait_for_selector(".function-card")

        # Fill reply for both functions invited (Sharma Family -> Lunch & Reception)
        cards = guest_page.locator(".function-card").all()
        assert len(cards) >= 1
        for card in cards:
            fn_id = card.get_attribute("data-function-id")
            assert fn_id is not None
            guest_page.fill(f"#adults_{fn_id}", "3")
            guest_page.fill(f"#children_{fn_id}", "0")
            guest_page.fill(f"#veg_{fn_id}", "2")
            guest_page.fill(f"#jain_{fn_id}", "0")
            guest_page.fill(f"#non_veg_{fn_id}", "1")

        guest_page.click("button[type='submit']")
        guest_page.wait_for_url("**/done")

        # 7. Dashboard count updates without reload via SSE
        expect(
            host_page.locator(".function-summary-card").first.locator(".replied-households")
        ).to_have_text("1", timeout=15000)
        expect(
            host_page.locator(".function-summary-card").first.locator(".yes-seats")
        ).to_have_text("3", timeout=15000)

        guest_ctx.close()
        host_ctx.close()
        browser.close()
