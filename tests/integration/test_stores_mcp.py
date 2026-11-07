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
    """Create in-memory MCP servers for Swiggy Instamart and Swiggy Food matching the real
    Swiggy Builders Club MCP tool names (`get_addresses`, `search_products`, `update_cart`,
    `get_cart`, `checkout` on `/im`; `search_restaurants`, `search_menu`, `get_restaurant_menu`,
    `update_food_cart`, `get_food_cart`, `place_food_order` on `/food`) with a call spy."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    im_srv = MCPServer("fake-swiggy-instamart")

    @im_srv.tool()
    def get_addresses(page: int = 1, pageSize: int = 10) -> dict[str, Any]:  # noqa: N803
        """Swiggy (Instamart/Food): Get saved delivery addresses in recommended display order."""
        call_log.append(("im:get_addresses", {"page": page, "pageSize": pageSize}))
        return {
            "addresses": [
                {
                    "id": "addr_demo_1",
                    "annotation": "Home",
                    "city": "Kolkata",
                }
            ]
        }

    @im_srv.tool()
    def search_products(
        query: str,
        addressId: str = "addr_demo_1",  # noqa: N803
        latitude: float = 22.57,
        longitude: float = 88.36,
        lat: float = 22.57,
        lng: float = 88.36,
    ) -> dict[str, Any]:
        """Search for products available at an address or guest coordinates."""
        call_log.append(
            (
                "im:search_products",
                {"query": query, "addressId": addressId, "lat": lat, "lng": lng},
            )
        )
        return {
            "items": [
                {
                    "name": f"Instamart {query.title()} Pack",
                    "spinId": f"SPIN_{query.upper().replace(' ', '_')}",
                    "skuId": f"SKU_{query.upper().replace(' ', '_')}",
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
    def update_cart(
        items: list[dict[str, Any]],
        selectedAddressId: str = "addr_demo_1",  # noqa: N803
        lat: float = 22.57,
        lng: float = 88.36,
    ) -> dict[str, Any]:
        """Swiggy Instamart (Grocery): Update grocery items in the cart without placing an order."""
        call_log.append(
            (
                "im:update_cart",
                {"items": items, "selectedAddressId": selectedAddressId, "lat": lat, "lng": lng},
            )
        )
        total_qty = sum(int(x.get("quantity", 1)) for x in items)
        return {
            "added_items": len(items),
            "total_inr": 145.0 * total_qty,
            "eta_min": 12,
            "eta_max": 22,
            "checkout_url": "https://www.swiggy.com/instamart/checkout",
        }

    @im_srv.tool()
    def get_cart() -> dict[str, Any]:
        """Swiggy Instamart (Grocery): Get current Swiggy Instamart grocery cart."""
        call_log.append(("im:get_cart", {}))
        return {"cartTotalAmount": "₹290", "items": []}

    @im_srv.tool()
    def checkout(addressId: str = "addr_demo_1") -> dict[str, Any]:  # noqa: N803
        """Forbidden order-placement tool — must NEVER be called by Hostora."""
        call_log.append(("im:checkout", {"addressId": addressId}))
        raise AssertionError("checkout must never be called!")

    food_srv = MCPServer("fake-swiggy-food")

    @food_srv.tool()
    def search_restaurants(
        query: str,
        addressId: str = "addr_demo_1",  # noqa: N803
        lat: float = 22.57,
        lng: float = 88.36,
    ) -> dict[str, Any]:
        """Search and order food from restaurants for delivery."""
        call_log.append(
            (
                "food:search_restaurants",
                {"query": query, "addressId": addressId, "lat": lat, "lng": lng},
            )
        )
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
    def get_restaurant_menu(restaurantId: str, addressId: str = "addr_demo_1") -> dict[str, Any]:  # noqa: N803
        """Get the complete menu of a restaurant, paginated by category."""
        call_log.append(
            ("food:get_restaurant_menu", {"restaurantId": restaurantId, "addressId": addressId})
        )
        return {"categories": []}

    @food_srv.tool()
    def update_food_cart(
        items: list[dict[str, Any]] | None = None,
        cartItems: list[dict[str, Any]] | None = None,  # noqa: N803
        restaurantId: str = "rest_1",  # noqa: N803
        lat: float = 22.57,
        lng: float = 88.36,
    ) -> dict[str, Any]:
        """Add items to food delivery cart or update cart contents without placing an order."""
        resolved = items or cartItems or []
        call_log.append(
            (
                "food:update_food_cart",
                {"items": resolved, "restaurantId": restaurantId, "lat": lat, "lng": lng},
            )
        )
        return {
            "added_items": len(resolved),
            "total_inr": 1950.0,
            "eta_min": 28,
            "eta_max": 42,
            "checkout_url": "https://www.swiggy.com/checkout",
        }

    @food_srv.tool()
    def place_food_order(addressId: str = "addr_demo_1") -> dict[str, Any]:  # noqa: N803
        """Forbidden order-placement tool — must NEVER be called by Hostora."""
        call_log.append(("food:place_food_order", {"addressId": addressId}))
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

        cart_calls = [c for c in call_log if c[0] in ("im:update_cart", "im:add_to_cart")]
        assert len(cart_calls) == 1
        assert cart_calls[0][1]["items"] == [
            {"item_id": "mineral_water", "quantity": 3},
            {"item_id": "paper_plates", "quantity": 2},
        ]
        # Never called any order-placement or payment tool
        assert not any(
            "order" in name or "pay" in name or "checkout" in name for name, _ in call_log
        )

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


async def test_knowledge_services_and_host_planner_html_routes(
    client: AsyncClient,
    db: AsyncSession,
    settings: Settings,
    host_headers: dict[str, str],
) -> None:
    import httpx
    import respx

    from hostora.adapters.knowledge.cache import cache_evidence, get_cached_evidence
    from hostora.engine.grounding import Evidence
    from hostora.security.firebase import HostIdentity
    from hostora.services.knowledge import (
        calibrate_grounding,
        explain_dish,
        get_runtime_vocabulary,
        refresh_knowledge,
    )
    from hostora.services.plans import (
        confirm_plan_item,
        remove_plan_item,
        replace_plan_item,
        update_plan_item_purchase,
    )

    # 1. Knowledge cache and services
    await cache_evidence(
        "paneer tikka",
        "dish_ingredients",
        Evidence(
            source="knowledge_base",
            ingredients=("paneer", "spices"),
            tags=frozenset({"dairy"}),
            citation="https://www.wikidata.org/wiki/Q123",
        ),
        db,
        host_uid="demo-host",
    )
    cached = await get_cached_evidence("paneer tikka", db, host_uid="demo-host")
    assert len(cached) >= 1

    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://static.openfoodfacts.org/data/taxonomies/ingredients.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "en:paneer": {
                        "name": {"en": "Paneer", "hi": "पनीर"},
                        "synonyms": {"en": ["cottage cheese"]},
                        "parents": ["en:dairy"],
                    }
                },
            )
        )
        mock.get("https://world.openfoodfacts.org/api/v2/search").mock(
            return_value=httpx.Response(200, json={"products": []})
        )
        mock.get(url__startswith="https://www.wikidata.org/w/api.php").mock(
            return_value=httpx.Response(200, json={"search": []})
        )
        counts = await refresh_knowledge(db)
        assert counts["concepts"] >= 1
        vocab = await get_runtime_vocabulary(db)
        assert vocab is not None

        explained = await explain_dish("Paneer Tikka", ["pure_veg"], session=db)
        assert explained.name == "Paneer Tikka"

    thresh, risk, n = await calibrate_grounding(0.01, session=db)
    assert n == 150
    assert risk <= 0.05
    assert thresh > 0

    # 2. Plan item mutations and HTML pages (host + planner)
    await seed_demo_event(db, settings)
    ev_resp = await client.get("/v1/events/DEM01", headers=host_headers)
    fn_id = ev_resp.json()["functions"][0]["id"]

    # Log in via demo session so HTML routes are authenticated
    login_resp = await client.get("/app/login")
    assert login_resp.status_code == 200
    demo_login = await client.post("/auth/demo")
    assert demo_login.status_code in (200, 204, 303)
    csrf_cookie = client.cookies.get("hostora_csrf", "")

    # Visit host HTML pages
    for path in (
        "/app",
        "/app/events/new",
        "/app/e/DEM01",
        "/app/e/DEM01/guests",
        "/app/e/DEM01/invite",
        "/app/e/DEM01/budget",
        "/app/settings",
        f"/app/e/DEM01/plan/{fn_id}",
    ):
        r = await client.get(path)
        assert r.status_code == 200, f"Expected 200 for {path}, got {r.status_code}"

    # Trigger HTML plan generation via HTMX POST
    import re

    settings_html = (await client.get("/app/settings")).text
    m_csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', settings_html)
    csrf_cookie = m_csrf.group(1) if m_csrf else ""
    gen_r = await client.post(
        f"/app/e/DEM01/plan/{fn_id}",
        data={"brief": "Quick party", "minutes_left": "120", "csrf_token": csrf_cookie},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_cookie},
    )
    assert gen_r.status_code == 200
    assert "plan-stream-root" in gen_r.text

    # Generate plan via API and test item operations + HTML planner routes
    create_resp = await client.post(
        "/v1/events/DEM01/plans",
        headers=host_headers,
        json={"functionId": fn_id, "brief": "Quick party", "minutesLeft": 120},
    )
    plan_id = create_resp.json()["planId"]
    await client.get(f"/v1/plans/{plan_id}/stream?format=json", headers=host_headers)

    plan_resp = await client.get(f"/v1/plans/{plan_id}", headers=host_headers)
    plan_json = plan_resp.json()
    first_item_id = plan_json["items"][0]["itemId"]

    host = HostIdentity(uid="demo-host", name="Demo Host")
    await update_plan_item_purchase(db, plan_id, first_item_id, host, bought=True, actual_inr=500)
    await confirm_plan_item(db, plan_id, first_item_id, host, confirmations=["composition"])
    replaced_plan = await replace_plan_item(db, plan_id, first_item_id, host, name="Jeera Rice")
    rep_id = next(it.item_id for it in replaced_plan.items if it.name == "Jeera Rice")
    await remove_plan_item(db, plan_id, rep_id, host)

    # Test planner HTML endpoints (toggle purchase, delivery report, budget page with plan)
    rem_item_id = plan_json["items"][1]["itemId"]
    tog_r = await client.post(
        f"/app/e/DEM01/plan/{plan_id}/items/{rem_item_id}/toggle",
        data={"bought": "true", "actual_inr": "450", "csrf_token": csrf_cookie},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_cookie},
    )
    assert tog_r.status_code == 200

    deliv_r = await client.post(
        f"/app/e/DEM01/plan/{plan_id}/delivery-report",
        data={
            "channel": "instamart",
            "item_kind": "supply",
            "promised_max": "30",
            "actual_minutes": "28",
            "csrf_token": csrf_cookie,
        },
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_cookie},
    )
    assert deliv_r.status_code == 200

    bud_r = await client.get("/app/e/DEM01/budget")
    assert bud_r.status_code == 200


@pytest.mark.store_live
async def test_swiggy_instamart_live_search_and_cart_without_checkout() -> None:
    import httpx2
    import mcp
    from mcp.client.streamable_http import streamable_http_client

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker

    token = os.environ.get("SWIGGY_LIVE_ACCESS_TOKEN", "").strip()
    if not token:
        real_settings = get_settings()
        engine = make_engine(real_settings)
        maker = make_sessionmaker(engine)
        try:
            async with maker() as real_sess:
                loaded = await load_store_token(
                    real_sess, real_settings, "demo-host", "swiggy", auto_refresh=True
                )
                if loaded is not None:
                    token = loaded.access_token
        finally:
            await engine.dispose()

    if not token:
        pytest.skip(
            "No Swiggy OAuth token found in SWIGGY_LIVE_ACCESS_TOKEN or store_connection; "
            "connect Swiggy at http://localhost:8000/app/settings first"
        )

    headers = {"Authorization": f"Bearer {token}"}

    # 1. Inspect and print real list_tools from Swiggy Instamart MCP and Swiggy Food MCP
    im_tools_dump: list[dict[str, Any]] = []
    food_tools_dump: list[dict[str, Any]] = []

    async with (
        httpx2.AsyncClient(headers=headers, timeout=20.0) as http_client,
        mcp.Client(
            streamable_http_client("https://mcp.swiggy.com/im", http_client=http_client)
        ) as im_client,
    ):
        im_res = await im_client.list_tools()
        for t in im_res.tools:
            im_tools_dump.append(
                {
                    "name": t.name,
                    "description": getattr(t, "description", ""),
                    "inputSchema": getattr(t, "input_schema", None)
                    or getattr(t, "inputSchema", None)
                    or {},
                }
            )
        print("\n=== REAL SWIGGY INSTAMART MCP list_tools ===")
        print(json.dumps(im_tools_dump, indent=2))

        # Discover user's saved addresses if an address tool exists
        addr_tool = next(
            (
                t["name"]
                for t in im_tools_dump
                if "address" in t["name"].lower() or "location" in t["name"].lower()
            ),
            None,
        )
        addr_res: Any = None
        if addr_tool:
            try:
                addr_res = await im_client.call_tool(addr_tool, {})
                for blk in addr_res.content:
                    txt = getattr(blk, "text", None)
                    if isinstance(txt, str) and txt.strip():
                        print(f"\n=== REAL SWIGGY ADDRESSES ({addr_tool}) ===")
                        print(txt[:1000])
            except Exception as exc:
                print(f"Address lookup note: {exc}")

        # Search 3 Instamart items at the user's address and add to cart (NO CHECKOUT)
        search_tool = next(
            (
                t["name"]
                for t in im_tools_dump
                if "search" in t["name"].lower() or "product" in t["name"].lower()
            ),
            im_tools_dump[0]["name"] if im_tools_dump else "search_products",
        )

        # Extract all saved addressIds from get_addresses text `(ID: <addressId>)`
        import re as _re

        addr_text_full = (
            "\n".join(getattr(b, "text", "") for b in addr_res.content if getattr(b, "text", None))
            if addr_res is not None
            else ""
        )
        all_address_ids = _re.findall(r"\(ID:\s*([A-Za-z0-9_-]+)\)", addr_text_full)
        print(f"\n=== ALL SAVED ADDRESS IDS: {all_address_ids} ===")
        assert all_address_ids, "Expected at least one saved Swiggy addressId from get_addresses"

        found_items: list[dict[str, Any]] = []
        cart_payload_items: list[dict[str, Any]] = []
        cart_text = ""
        selected_address_id = all_address_ids[0]

        for candidate_addr_id in all_address_ids:
            temp_found: list[dict[str, Any]] = []
            temp_cart: list[dict[str, Any]] = []
            for q in ("paneer", "basmati rice", "paper plates"):
                call_args: dict[str, Any] = {"query": q, "addressId": candidate_addr_id}
                s_res = await im_client.call_tool(search_tool, call_args)
                raw_text = "\n".join(
                    getattr(b, "text", "") for b in s_res.content if getattr(b, "text", None)
                )
                if not raw_text.strip() or "addressId is required" in raw_text:
                    break
                m_spin = _re.search(r'"spinId"\s*:\s*"([A-Za-z0-9_-]+)"', raw_text)
                m_sku = _re.search(r'"skuId"\s*:\s*"([A-Za-z0-9_-]+)"', raw_text)
                spin_id = m_spin.group(1) if m_spin else None
                sku_id = m_sku.group(1) if m_sku else None
                if spin_id:
                    item_entry: dict[str, Any] = {"spinId": spin_id, "quantity": 1}
                    if sku_id:
                        item_entry["skuId"] = sku_id
                    temp_cart.append(item_entry)
                    temp_found.append({"query": q, "raw": raw_text})

            if len(temp_cart) == 3:
                c_res = await im_client.call_tool(
                    "update_cart",
                    {
                        "selectedAddressId": candidate_addr_id,
                        "items": temp_cart,
                    },
                )
                c_text = "\n".join(
                    getattr(b, "text", "") for b in c_res.content if getattr(b, "text", None)
                )
                if "not serviceable" not in c_text.lower():
                    selected_address_id = candidate_addr_id
                    found_items = temp_found
                    cart_payload_items = temp_cart
                    cart_text = c_text
                    break
                if not found_items:
                    selected_address_id = candidate_addr_id
                    found_items = temp_found
                    cart_payload_items = temp_cart
                    cart_text = c_text

        print(f"\n=== SELECTED ADDRESS ID: {selected_address_id} ===")
        for item in found_items:
            print(f"\n=== REAL INSTAMART SEARCH ({item['query']}) AT {selected_address_id} ===")
            print(item["raw"][:2200])

        assert len(found_items) == 3
        assert len(cart_payload_items) == 3
        print(f"\n=== CALLING REAL update_cart WITH 3 ITEMS: {cart_payload_items} ===")
        print("\n=== REAL SWIGGY INSTAMART update_cart OUTPUT ===")
        print(cart_text[:3000])

        get_cart_res = await im_client.call_tool("get_cart", {})
        get_cart_text = "\n".join(
            getattr(b, "text", "") for b in get_cart_res.content if getattr(b, "text", None)
        )
        print("\n=== REAL SWIGGY INSTAMART get_cart OUTPUT ===")
        print(get_cart_text[:3000])

    async with (
        httpx2.AsyncClient(headers=headers, timeout=20.0) as http_client,
        mcp.Client(
            streamable_http_client("https://mcp.swiggy.com/food", http_client=http_client)
        ) as food_client,
    ):
        food_res = await food_client.list_tools()
        for t in food_res.tools:
            food_tools_dump.append(
                {
                    "name": t.name,
                    "description": getattr(t, "description", ""),
                    "inputSchema": getattr(t, "input_schema", None)
                    or getattr(t, "inputSchema", None)
                    or {},
                }
            )
        print("\n=== REAL SWIGGY FOOD MCP list_tools ===")
        print(json.dumps(food_tools_dump, indent=2))
