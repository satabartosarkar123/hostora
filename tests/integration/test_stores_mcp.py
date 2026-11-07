"""Integration tests for Milestone M5-R with fake MCP servers, 120-minute feasibility,
one-tap cart building, OAuth token refresh/disconnect, privacy spy, delivery learning,
and optional live Swiggy MCP verification."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from hostora.adapters.stores import (
    LiveStoreOffer,
    StoreContractError,
    StoreTokenData,
    SwiggyStoreAdapter,
    disconnect_store,
    get_store_connection_status,
    load_store_token,
    save_store_token,
)
from hostora.db.models import StoreConnection
from hostora.services.demo import seed_demo_event
from hostora.services.learning import (
    get_effective_delivery_safety_minutes,
    get_learned_channel_etas,
    run_nightly_learning,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.config import Settings

pytestmark = [pytest.mark.db]


def make_fake_swiggy_mcp_servers() -> tuple[MCPServer, MCPServer, list[tuple[str, dict[str, Any]]]]:
    """Create in-memory MCP servers for Swiggy Instamart and Swiggy Food with a call spy."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    im_srv = MCPServer("fake-swiggy-instamart")

    @im_srv.tool()
    def search_products(query: str, lat: float = 22.57, lng: float = 88.36) -> dict[str, Any]:
        """Search Swiggy Instamart grocery and supply products."""
        call_log.append(("im:search_products", {"query": query, "lat": lat, "lng": lng}))
        return {
            "items": [
                {
                    "name": f"Instamart {query.title()} Pack",
                    "pack_size": 1,
                    "unit": "piece",
                    "price_inr": 145.0,
                    "eta_min": 12,
                    "eta_max": 22,
                    "available": True,
                    "url": "https://www.swiggy.com/instamart/item/im_1",
                }
            ]
        }

    @im_srv.tool()
    def add_to_cart(
        items: list[dict[str, Any]], lat: float = 22.57, lng: float = 88.36
    ) -> dict[str, Any]:
        """Add items to the user's Swiggy Instamart cart without placing an order."""
        call_log.append(("im:add_to_cart", {"items": items, "lat": lat, "lng": lng}))
        total_qty = sum(int(x.get("quantity", 1)) for x in items)
        return {
            "added_items": len(items),
            "total_inr": 145.0 * total_qty,
            "eta_min": 12,
            "eta_max": 22,
            "checkout_url": "https://www.swiggy.com/instamart/checkout",
        }

    @im_srv.tool()
    def place_instamart_order() -> dict[str, Any]:
        """Forbidden order-placement tool — must NEVER be called by Hostora."""
        call_log.append(("im:place_instamart_order", {}))
        raise AssertionError("place_instamart_order must never be called!")

    food_srv = MCPServer("fake-swiggy-food")

    @food_srv.tool()
    def search_restaurants(query: str, lat: float = 22.57, lng: float = 88.36) -> dict[str, Any]:
        """Search Swiggy Food restaurants and ready-to-eat dishes."""
        call_log.append(("food:search_restaurants", {"query": query, "lat": lat, "lng": lng}))
        return {
            "items": [
                {
                    "name": f"Royal {query.title()} Platter",
                    "pack_size": 1000,
                    "unit": "g",
                    "price_inr": 650.0,
                    "eta_min": 28,
                    "eta_max": 42,
                    "available": True,
                    "url": "https://www.swiggy.com/restaurants/royal-kitchen",
                }
            ]
        }

    @food_srv.tool()
    def update_food_cart(
        items: list[dict[str, Any]], lat: float = 22.57, lng: float = 88.36
    ) -> dict[str, Any]:
        """Add dishes to the user's Swiggy Food cart without placing an order."""
        call_log.append(("food:update_food_cart", {"items": items, "lat": lat, "lng": lng}))
        return {
            "added_items": len(items),
            "total_inr": 1950.0,
            "eta_min": 28,
            "eta_max": 42,
            "checkout_url": "https://www.swiggy.com/checkout",
        }

    @food_srv.tool()
    def place_food_order() -> dict[str, Any]:
        """Forbidden order-placement tool — must NEVER be called by Hostora."""
        call_log.append(("food:place_food_order", {}))
        raise AssertionError("place_food_order must never be called!")

    return im_srv, food_srv, call_log


async def test_120_minute_plan_live_offers_build_carts_and_privacy_spy(
    client: AsyncClient,
    db: AsyncSession,
    settings: Settings,
    host_headers: dict[str, str],
) -> None:
    await seed_demo_event(db, settings)
    im_srv, food_srv, call_log = make_fake_swiggy_mcp_servers()

    # 1. Connect Swiggy for demo-host
    await save_store_token(
        db,
        settings,
        "demo-host",
        "swiggy",
        StoreTokenData(
            access_token="swiggy_live_tok_1",
            refresh_token="swiggy_ref_tok_1",
            scopes=["mcp:tools", "cart:write"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    await db.commit()

    # 2. Verify live offers from fake MCP server and check that no household PII is sent
    ev_resp = await client.get("/v1/events/DEM01", headers=host_headers)
    assert ev_resp.status_code == 200
    ev_data = ev_resp.json()
    reception_fn = next(f for f in ev_data["functions"] if f["name"] == "Reception")
    fn_id = reception_fn["id"]

    hh_resp = await client.get("/v1/events/DEM01/households", headers=host_headers)
    hh_payload = hh_resp.json()
    hh_list = hh_payload["households"] if isinstance(hh_payload, dict) else hh_payload
    hh_names = [h["displayName"] for h in hh_list]
    assert len(hh_names) > 0

    adapter = SwiggyStoreAdapter(
        access_token="swiggy_live_tok_1",
        instamart_server_override=im_srv,
        food_server_override=food_srv,
        forbidden_terms=hh_names,
    )
    im_offers = await adapter.search_live_offers(
        item_id="mineral_water",
        query="mineral water",
        category="beverage",
    )
    food_offers = await adapter.search_live_offers(
        item_id="vegetable_pulao",
        query="vegetable pulao",
        category="food",
    )
    assert len(im_offers) == 1
    assert im_offers[0].channel == "instamart"
    assert im_offers[0].price_inr == 145.0
    assert len(food_offers) == 1
    assert food_offers[0].channel == "swiggy_food"

    # Inject live offers and fake MCP servers into app.state for the planner stream and cart build
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.live_store_offers = {
        "mineral_water": im_offers,
        "vegetable_pulao": food_offers,
    }
    app.state.swiggy_instamart_mcp = im_srv
    app.state.swiggy_food_mcp = food_srv

    try:
        # 3. Create a plan with 120 minutes left (party in 2 hours!)
        create_resp = await client.post(
            "/v1/events/DEM01/plans",
            headers=host_headers,
            json={
                "functionId": fn_id,
                "brief": (
                    "Reception dinner for Sharma Family call +91 98765 43210, add fairy lights"
                ),
                "minutesLeft": 120,
            },
        )
        assert create_resp.status_code == 202
        plan_id = create_resp.json()["planId"]

        stream_resp = await client.get(
            f"/v1/plans/{plan_id}/stream?format=json",
            headers=host_headers,
        )
        assert stream_resp.status_code == 200

        plan_resp = await client.get(f"/v1/plans/{plan_id}", headers=host_headers)
        assert plan_resp.status_code == 200
        plan_json = plan_resp.json()

        assert plan_json["minutesLeft"] == 120
        assert plan_json["urgentMode"] is True

        # 4. Assert NO multi-day shop (Amazon, Flipkart) appears anywhere in the 120-minute plan!
        for item in plan_json["items"] + plan_json["needsConfirmation"]:
            for off in item["offers"]:
                assert off["channel"] not in ("amazon", "flipkart")
                assert off["kind"] in ("quick", "food")
            for opt in item["options"]:
                assert opt["channel"] not in ("amazon", "flipkart")

        for cart in plan_json["cartPlan"]["carts"]:
            assert cart["channel"] not in ("amazon", "flipkart")
            assert cart["arrivesBy"] != ""

        # 5. Assert unfulfillable items (multi-day decor) are listed with grounded alternatives
        assert len(plan_json["unfulfillable"]) >= 1
        unf_0 = plan_json["unfulfillable"][0]
        assert unf_0["alternatives"]
        assert all(a["channel"] not in ("amazon", "flipkart") for a in unf_0["alternatives"])

        # 6. Call "Build carts": must call cart_add once per store with confirmed quantities
        # and NEVER call any order-placing or payment tool
        call_log.clear()
        cart_resp = await client.post(
            f"/v1/plans/{plan_id}/carts:build",
            headers=host_headers,
            json={
                "channel": "instamart",
                "quantities": {"mineral_water": 3, "paper_plates": 2},
            },
        )
        assert cart_resp.status_code == 200
        cart_data = cart_resp.json()
        assert cart_data["store"] == "swiggy"
        assert cart_data["channel"] == "instamart"
        assert cart_data["addedItems"] == 2
        assert cart_data["checkoutUrl"] == "https://www.swiggy.com/instamart/checkout"

        cart_calls = [c for c in call_log if c[0] == "im:add_to_cart"]
        assert len(cart_calls) == 1
        assert cart_calls[0][1]["items"] == [
            {"item_id": "mineral_water", "quantity": 3},
            {"item_id": "paper_plates", "quantity": 2},
        ]
        # Never called any order-placement or payment tool
        assert not any("order" in name or "pay" in name for name, _ in call_log)

        # 7. Spy assertion: no household name or phone number in any MCP request
        serialized_calls = json.dumps(call_log)
        for hh_name in hh_names:
            assert hh_name not in serialized_calls
        assert "98765" not in serialized_calls
    finally:
        app.state.live_store_offers = None
        app.state.swiggy_instamart_mcp = None
        app.state.swiggy_food_mcp = None


async def test_expired_token_refresh_disconnect_and_not_approved_fallback(
    db: AsyncSession,
    settings: Settings,
) -> None:
    await seed_demo_event(db, settings)

    # 1. Store an expired token for demo-host
    expired_tok = StoreTokenData(
        access_token="expired_access_token",
        refresh_token="valid_refresh_token_123",
        scopes=["mcp:tools"],
        expires_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    await save_store_token(db, settings, "demo-host", "swiggy", expired_tok)
    await db.commit()

    refresh_calls: list[tuple[str, str]] = []

    async def _fake_refresh(store: str, rt: str) -> StoreTokenData:
        refresh_calls.append((store, rt))
        return StoreTokenData(
            access_token="freshly_minted_access_token",
            refresh_token="fresh_refresh_token_456",
            scopes=["mcp:tools", "cart:write"],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )

    loaded = await load_store_token(
        db,
        settings,
        "demo-host",
        "swiggy",
        auto_refresh=True,
        refresh_fn=_fake_refresh,
    )
    await db.commit()

    assert loaded is not None
    assert loaded.access_token == "freshly_minted_access_token"
    assert refresh_calls == [("swiggy", "valid_refresh_token_123")]

    # 2. Disconnect deletes the token from store_connection
    deleted = await disconnect_store(db, "demo-host", "swiggy")
    await db.commit()
    assert deleted is True

    row = (
        await db.execute(
            select(StoreConnection).where(
                StoreConnection.host_uid == "demo-host",
                StoreConnection.store == "swiggy",
            )
        )
    ).scalar_one_or_none()
    assert row is None

    # 3. Zomato is disabled by default in sources.yaml -> status is not_approved_yet
    zomato_status = await get_store_connection_status(db, settings, "demo-host", "zomato")
    assert zomato_status == "not_approved_yet"


async def test_missing_tool_in_mcp_server_fails_contract_loudly() -> None:
    broken_srv = MCPServer("broken-swiggy")

    @broken_srv.tool()
    def search_products(query: str) -> dict[str, Any]:
        """Search catalog items by keyword."""
        return {"items": []}

    adapter = SwiggyStoreAdapter(
        access_token="tok",
        instamart_server_override=broken_srv,
    )
    with pytest.raises(StoreContractError, match="missing required capability 'cart_add'"):
        await adapter.build_cart(
            channel="instamart",
            quantities={"mineral_water": 1},
        )


async def test_delivery_report_learns_eta_and_safety_margin(
    client: AsyncClient,
    db: AsyncSession,
    settings: Settings,
    host_headers: dict[str, str],
) -> None:
    await seed_demo_event(db, settings)
    ev_resp = await client.get("/v1/events/DEM01", headers=host_headers)
    fn_id = ev_resp.json()["functions"][0]["id"]

    create_resp = await client.post(
        "/v1/events/DEM01/plans",
        headers=host_headers,
        json={"functionId": fn_id, "brief": "Simple lunch", "minutesLeft": 360},
    )
    plan_id = create_resp.json()["planId"]
    await client.get(f"/v1/plans/{plan_id}/stream?format=json", headers=host_headers)

    # Submit 6 delivery reports for instamart where promised_max=30 but actual was 35..50 min
    for actual in (35, 38, 40, 42, 46, 50):
        rep_resp = await client.post(
            f"/v1/plans/{plan_id}/delivery:report",
            headers=host_headers,
            json={
                "channel": "instamart",
                "itemKind": "supply",
                "promisedMax": 30.0,
                "actualMinutes": float(actual),
            },
        )
        assert rep_resp.status_code == 200

    etas = await get_learned_channel_etas(db, "West Bengal / Kolkata")
    assert etas["instamart"].source == "learned"
    assert etas["instamart"].max_minutes >= 46.0

    # Nightly learning job raises delivery_safety_minutes upward from prior 30.0
    await run_nightly_learning(db)
    learned_safety = await get_effective_delivery_safety_minutes(db)
    assert learned_safety > 30.0


@pytest.mark.store_live
async def test_swiggy_instamart_live_search_and_cart_without_checkout() -> None:
    token = os.environ.get("SWIGGY_LIVE_ACCESS_TOKEN", "").strip()
    if not token:
        pytest.skip("SWIGGY_LIVE_ACCESS_TOKEN not set; skipping live Swiggy MCP test")

    adapter = SwiggyStoreAdapter(access_token=token)
    found: dict[str, LiveStoreOffer] = {}
    for q in ("paneer", "basmati rice", "paper plates"):
        offers = await adapter.search_instamart_offers(item_id=q, query=q)
        assert offers, f"Expected live Instamart offers for {q}"
        found[q] = offers[0]

    # Fill cart without checking out
    cart_res = await adapter.build_cart(
        channel="instamart",
        quantities=dict.fromkeys(found, 1),
    )
    assert cart_res.added_items == 3
    assert cart_res.checkout_url.startswith("https://")
