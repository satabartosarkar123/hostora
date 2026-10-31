from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.e2e, pytest.mark.db]


def test_gate_offline_scan_verification_and_sync(live_server: str, browser_name: str) -> None:
    headers = {"Authorization": "Demo demo-host"}
    seed_resp = httpx.post(f"{live_server}/v1/demo/seed", headers=headers, timeout=20.0)
    assert seed_resp.status_code == 200
    event_key = seed_resp.json()["eventKey"]

    ev_resp = httpx.get(f"{live_server}/v1/events/{event_key}", headers=headers, timeout=20.0)
    reception_fn = next(f for f in ev_resp.json()["functions"] if f["name"] == "Reception")
    fn_id = reception_fn["id"]

    # Prepare invites and fetch 3 valid signed passes via the guest invite API
    invites = httpx.post(
        f"{live_server}/v1/events/{event_key}/invites:prepare",
        headers=headers,
        timeout=20.0,
    ).json()

    valid_passes: list[dict[str, object]] = []
    for inv in invites:
        token = str(inv["link"]).rstrip("/").split("/")[-1]
        guest_inv = httpx.get(f"{live_server}/v1/invites/{event_key}/{token}", timeout=20.0).json()
        for p_item in guest_inv.get("passes", []):
            if p_item["functionId"] == fn_id:
                valid_passes.append(p_item)
                break
        if len(valid_passes) == 3:
            break

    assert len(valid_passes) == 3
    expected_seats = sum(int(str(p_item["seats"])) for p_item in valid_passes)

    link_resp = httpx.post(
        f"{live_server}/v1/events/{event_key}/gate-links",
        headers=headers,
        json={"functionId": fn_id, "hours": 24},
    )
    assert link_resp.status_code == 200
    gate_url = link_resp.json()["url"]

    with sync_playwright() as p:
        launcher = getattr(p, browser_name, p.chromium)
        browser = launcher.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 412, "height": 915})
        page = ctx.new_page()

        # 1. Open gate page online and wait for manifest & test hook
        page.goto(gate_url)
        expect(page.locator("#gate-app")).to_have_attribute("data-ready", "true", timeout=5000)

        # 2. Switch browser context to offline (airplane mode)
        ctx.set_offline(True)

        # 3. Scan 3 valid passes offline through window.__hostoraScan and see green
        for valid_pass in valid_passes:
            tok_str = str(valid_pass["token"])
            seats_int = int(str(valid_pass["seats"]))
            res = page.evaluate("(tok) => window.__hostoraScan(tok)", tok_str)
            assert res["status"] == "green"
            expect(page.locator("#gate-result-banner")).to_have_attribute("data-status", "green")
            expect(page.locator("#gate-result-banner")).to_contain_text(
                f"Welcome — {seats_int} seats"
            )

        expect(page.locator("#gate-local-arrivals")).to_have_text(str(expected_seats))
        expect(page.locator("#gate-unsent-count")).to_have_text("3")

        # 4. Scan a forged pass offline and see red
        forged = str(valid_passes[0]["token"])[:-6] + "AAAAAA"
        forged_res = page.evaluate("(tok) => window.__hostoraScan(tok)", forged)
        assert forged_res["status"] == "red"
        expect(page.locator("#gate-result-banner")).to_have_attribute("data-status", "red")

        # 5. Go back online and wait for sync
        ctx.set_offline(False)
        page.evaluate("() => window.__hostoraSync()")
        expect(page.locator("#gate-unsent-count")).to_have_text("0", timeout=5000)

        # 6. Verify dashboard arrivals equal the sum of seats
        summary_resp = httpx.get(f"{live_server}/v1/events/{event_key}/summary", headers=headers)
        assert summary_resp.status_code == 200
        rec_summary = next(f for f in summary_resp.json()["functions"] if f["functionId"] == fn_id)
        assert rec_summary["arrivals"] == expected_seats

        ctx.close()
        browser.close()
