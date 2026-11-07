from __future__ import annotations

import httpx
import pytest
from axe_playwright_python.sync_playwright import Axe  # type: ignore[import-untyped]
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.e2e, pytest.mark.db]


def _assert_no_serious_or_critical_a11y_violations(page_name: str, results: object) -> None:
    response = getattr(results, "response", {})
    violations = response.get("violations", []) if isinstance(response, dict) else []
    bad = [
        {
            "id": v.get("id"),
            "impact": v.get("impact"),
            "description": v.get("description"),
            "nodes": [n.get("html") for n in v.get("nodes", [])[:3]],
        }
        for v in violations
        if v.get("impact") in ("serious", "critical")
    ]
    assert not bad, f"Serious/critical axe violations on {page_name}: {bad}"


def test_axe_accessibility_on_invite_done_dashboard_and_gate(
    live_server: str, browser_name: str
) -> None:
    seed_res = httpx.post(
        f"{live_server}/v1/demo/seed",
        headers={"Authorization": "Demo demo-host"},
        timeout=15.0,
    )
    assert seed_res.status_code == 200
    seed_data = seed_res.json()
    invite_url = seed_data["inviteLinks"][0]

    ev_res = httpx.get(
        f"{live_server}/v1/events/DEM01",
        headers={"Authorization": "Demo demo-host"},
        timeout=10.0,
    )
    assert ev_res.status_code == 200
    fn_id = next(f["id"] for f in ev_res.json()["functions"] if f["name"] == "Reception")

    gate_link_res = httpx.post(
        f"{live_server}/v1/events/DEM01/gate-links",
        headers={"Authorization": "Demo demo-host"},
        json={"functionId": fn_id, "hours": 12},
        timeout=10.0,
    )
    assert gate_link_res.status_code == 200
    gate_url = gate_link_res.json()["url"]

    axe = Axe()
    with sync_playwright() as p:
        launcher = getattr(p, browser_name, p.chromium)
        browser = launcher.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()

        # 1. Guest invite page
        page.goto(invite_url, wait_until="networkidle")
        res_invite = axe.run(page)
        _assert_no_serious_or_critical_a11y_violations("invite", res_invite)

        # Submit RSVP to reach the Done page
        page.locator("button[type='submit']").click()
        page.wait_for_url("**/done**")
        res_done = axe.run(page)
        _assert_no_serious_or_critical_a11y_violations("done", res_done)

        # 2. Gate page (before dashboard SSE stream)
        page.goto(gate_url, wait_until="domcontentloaded")
        expect(page.locator("#gate-app")).to_have_attribute("data-ready", "true", timeout=10000)
        res_gate = axe.run(page)
        _assert_no_serious_or_critical_a11y_violations("gate", res_gate)

        # 3. Host dashboard page
        page.goto(f"{live_server}/app/login", wait_until="domcontentloaded")
        page.locator("form[action='/auth/demo'] button").click()
        page.wait_for_url(f"{live_server}/app")
        page.goto(f"{live_server}/app/e/DEM01", wait_until="domcontentloaded")
        res_dashboard = axe.run(page)
        _assert_no_serious_or_critical_a11y_violations("dashboard", res_dashboard)

        context.close()
        browser.close()
