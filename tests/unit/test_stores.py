"""Unit tests for M5-R: STORE_TOKEN_KEY encryption round trip, MCP tool-capability mapping,
safety/privacy guards, and minutes_left calculation across midnight and timezones."""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from cryptography.exceptions import InvalidTag

from hostora.adapters.commerce import compute_minutes_left, format_arrival_clock
from hostora.adapters.stores import (
    StoreContractError,
    StorePrivacyError,
    StoreSafetyError,
    StoreTokenData,
    assert_not_order_or_payment_tool,
    assert_store_payload_clean,
    build_oauth_authorize_url,
    discover_store_capabilities,
    generate_pkce_pair,
)
from hostora.security.crypto import decrypt_store_token, encrypt_store_token

RECORDED_SWIGGY_LIST_TOOLS = [
    {
        "name": "search_products",
        "description": "Search Swiggy Instamart catalog products at a delivery location",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "lat": {"type": "number"}},
        },
    },
    {
        "name": "search_restaurants",
        "description": "Search Swiggy Food restaurants and dishes near venue",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    },
    {
        "name": "get_menu",
        "description": "Fetch restaurant menu items and prices",
        "inputSchema": {"type": "object", "properties": {"restaurant_id": {"type": "string"}}},
    },
    {
        "name": "get_addresses",
        "description": "Get saved delivery addresses and ETA",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_to_cart",
        "description": "Add products or dishes to the user's Swiggy cart without placing order",
        "inputSchema": {"type": "object", "properties": {"items": {"type": "array"}}},
    },
    {
        "name": "get_cart",
        "description": "View current Swiggy cart",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "place_food_order",
        "description": "Place a food order (FORBIDDEN by Hostora safety rules)",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

RECORDED_ZOMATO_LIST_TOOLS = [
    {
        "name": "get_restaurants_for_keyword",
        "description": "Search Zomato restaurants for a keyword",
        "inputSchema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
    },
    {
        "name": "get_menu_items_listing",
        "description": "List menu items for a Zomato restaurant",
        "inputSchema": {"type": "object", "properties": {"res_id": {"type": "string"}}},
    },
    {
        "name": "get_saved_addresses_for_user",
        "description": "Get user addresses and delivery ETA",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_cart",
        "description": "Create a Zomato cart with items",
        "inputSchema": {"type": "object", "properties": {"items": {"type": "array"}}},
    },
    {
        "name": "get_order_tracking_info",
        "description": "Track Zomato order status",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def test_store_token_encryption_round_trip_and_tamper_rejection() -> None:
    key_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    other_key_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    tok = StoreTokenData(
        access_token="swiggy_at_secret_12345",
        refresh_token="swiggy_rt_secret_67890",
        scopes=["mcp:tools", "cart:write"],
        expires_at=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
    )
    ciphertext = encrypt_store_token(tok.to_json(), key_b64)
    assert "swiggy_at_secret_12345" not in ciphertext

    decrypted = StoreTokenData.from_json(decrypt_store_token(ciphertext, key_b64))
    assert decrypted.access_token == tok.access_token
    assert decrypted.refresh_token == tok.refresh_token
    assert decrypted.scopes == ["mcp:tools", "cart:write"]
    assert decrypted.expires_at == tok.expires_at

    # Wrong key fails AES-GCM authentication
    with pytest.raises(InvalidTag):
        decrypt_store_token(ciphertext, other_key_b64)

    # Short key raises ValueError
    short_key = base64.b64encode(b"short").decode("ascii")
    with pytest.raises(ValueError, match="STORE_TOKEN_KEY"):
        encrypt_store_token("test", short_key)


def test_pkce_and_oauth_authorize_url() -> None:
    verifier, challenge = generate_pkce_pair()
    assert len(verifier) >= 43
    assert verifier != challenge
    url = build_oauth_authorize_url(
        "swiggy",
        client_id="hostora-test",
        redirect_uri="http://localhost:8000/app/settings/stores/swiggy/callback",
        state="st123",
        code_challenge=challenge,
    )
    assert "code_challenge_method=S256" in url
    assert f"code_challenge={challenge}" in url


def test_tool_capability_mapping_from_recorded_fixtures() -> None:
    swiggy_map = discover_store_capabilities(
        "swiggy",
        RECORDED_SWIGGY_LIST_TOOLS,
        required_capabilities=("search_products", "search_restaurants", "cart_add"),
    )
    assert swiggy_map["search_products"] == "search_products"
    assert swiggy_map["search_restaurants"] == "search_restaurants"
    assert swiggy_map["get_menu"] == "get_menu"
    assert swiggy_map["eta"] == "get_addresses"
    assert swiggy_map["cart_add"] == "add_to_cart"
    assert swiggy_map["cart_view"] == "get_cart"
    # Forbidden tool is never mapped to any capability
    assert "place_food_order" not in swiggy_map.values()

    zomato_map = discover_store_capabilities(
        "zomato",
        RECORDED_ZOMATO_LIST_TOOLS,
        required_capabilities=("search_restaurants", "cart_add"),
    )
    assert zomato_map["search_restaurants"] == "get_restaurants_for_keyword"
    assert zomato_map["get_menu"] == "get_menu_items_listing"
    assert zomato_map["eta"] == "get_saved_addresses_for_user"
    assert zomato_map["cart_add"] == "create_cart"
    assert zomato_map["order_status"] == "get_order_tracking_info"


def test_contract_fails_loudly_when_tool_missing_or_schema_invalid() -> None:
    incomplete_tools = [t for t in RECORDED_SWIGGY_LIST_TOOLS if t["name"] != "add_to_cart"]
    with pytest.raises(StoreContractError, match="missing required capability 'cart_add'"):
        discover_store_capabilities(
            "swiggy",
            incomplete_tools,
            required_capabilities=("search_products", "cart_add"),
        )

    bad_schema_tools = [
        {"name": "search_products", "description": "search", "inputSchema": {"type": "array"}}
    ]
    with pytest.raises(StoreContractError, match="invalid inputSchema"):
        discover_store_capabilities(
            "swiggy",
            bad_schema_tools,
            required_capabilities=("search_products",),
        )


def test_safety_and_privacy_guards() -> None:
    for forbidden in ("place_food_order", "place_order", "make_payment", "submit_order"):
        with pytest.raises(StoreSafetyError, match="Safety guard blocked"):
            assert_not_order_or_payment_tool("swiggy", forbidden)

    assert_not_order_or_payment_tool("swiggy", "add_to_cart")

    # Clean arguments pass
    assert_store_payload_clean(
        {"query": "paneer tikka", "lat": 22.57, "lng": 88.36},
        forbidden_terms=("Sharma Family", "Khan Household"),
    )

    # Household display name or phone number in MCP payload is blocked
    with pytest.raises(StorePrivacyError, match="Privacy guard blocked"):
        assert_store_payload_clean(
            {"query": "paneer for Sharma Family", "lat": 22.57},
            forbidden_terms=("Sharma Family",),
        )

    with pytest.raises(StorePrivacyError, match="Privacy guard blocked"):
        assert_store_payload_clean(
            {"query": "deliver to +91 98765 43210"},
            forbidden_terms=(),
        )


def test_minutes_left_calculation_across_midnight_and_timezones() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    utc = UTC
    ny = ZoneInfo("America/New_York")

    # 23:15 IST on Nov 7 -> 01:15 IST on Nov 8 (across midnight = 120 minutes)
    now_ist = datetime(2026, 11, 7, 23, 15, tzinfo=ist)
    starts_ist = datetime(2026, 11, 8, 1, 15, tzinfo=ist)
    assert compute_minutes_left(starts_ist, now_ist) == 120

    # Same instant expressed in UTC and America/New_York gives identical minutes_left
    now_utc = now_ist.astimezone(utc)
    starts_ny = starts_ist.astimezone(ny)
    assert compute_minutes_left(starts_ny, now_utc) == 120

    # Past start time clamps to 0
    assert compute_minutes_left(now_ist - timedelta(minutes=45), now_ist) == 0

    # Arrival clock formatting in IST across midnight
    assert format_arrival_clock(45, now_ist) == "00:00"
    assert format_arrival_clock(90, now_ist) == "00:45"


@pytest.mark.asyncio
async def test_zomato_adapter_live_offers_and_build_cart() -> None:
    from mcp.server.mcpserver import MCPServer

    from hostora.adapters.stores import ZomatoStoreAdapter

    srv = MCPServer("fake-zomato")

    @srv.tool()
    def get_restaurants_for_keyword(
        keyword: str, lat: float = 22.57, lng: float = 88.36
    ) -> dict[str, Any]:
        """Search Zomato restaurants and dishes for keyword."""
        return {
            "dishes": [
                {
                    "name": f"Zomato {keyword}",
                    "serves": 1,
                    "unit": "piece",
                    "price_inr": 210.0,
                    "eta_min": 25,
                    "eta_max": 40,
                    "available": True,
                }
            ]
        }

    @srv.tool()
    def create_cart(
        items: list[dict[str, Any]], lat: float = 22.57, lng: float = 88.36
    ) -> dict[str, Any]:
        """Create a Zomato cart with items."""
        return {
            "added": len(items),
            "total_inr": 420.0,
            "eta_min": 25,
            "eta_max": 40,
            "checkout_url": "https://www.zomato.com/checkout",
        }

    adapter = ZomatoStoreAdapter(access_token="zom_tok", server_override=srv)
    # Non-food returns empty
    assert await adapter.search_live_offers(item_id="i1", query="plates", category="supplies") == []
    # Food returns live offer
    offers = await adapter.search_live_offers(item_id="i2", query="Paneer Tikka", category="food")
    assert len(offers) == 1
    assert offers[0].channel == "zomato"
    assert offers[0].price_inr == 210.0

    cart = await adapter.build_cart(quantities={"i2": 2})
    assert cart.store == "zomato"
    assert cart.added_items == 1
    assert cart.checkout_url == "https://www.zomato.com/checkout"


@pytest.mark.asyncio
async def test_knowledge_adapters_and_osm_with_respx_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import respx

    from hostora.adapters.knowledge.off import fetch_off_taxonomy, search_off_product_evidence
    from hostora.adapters.knowledge.wikidata import fetch_wikidata_evidence
    from hostora.adapters.osm import _haversine_km, query_overpass_vendors, validate_osm_tag
    from hostora.config import get_settings

    assert _haversine_km(22.5726, 88.3639, 22.5726, 88.3639) == 0.0

    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://static.openfoodfacts.org/data/taxonomies/ingredients.json").mock(
            return_value=httpx.Response(
                200,
                json={"en:paneer": {"name": {"en": "Paneer"}, "parents": ["en:dairy"]}},
            )
        )
        mock.get("https://world.openfoodfacts.org/api/v2/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "products": [
                        {
                            "code": "890123",
                            "product_name": "Amul Paneer",
                            "ingredients": [{"text": "en:milk"}, {"text": "en:citric-acid"}],
                            "labels_tags": ["en:vegetarian", "en:halal"],
                            "ingredients_analysis_tags": ["en:vegan"],
                        },
                        {
                            "code": "890124",
                            "product_name": "Chicken Sausage",
                            "ingredients_text": "chicken, spices",
                            "ingredients_analysis_tags": ["en:non-vegetarian"],
                        },
                    ]
                },
            )
        )
        mock.get(url__startswith="https://www.wikidata.org/w/api.php").mock(
            return_value=httpx.Response(
                200,
                json={"search": [{"id": "Q12345"}]},
            )
        )
        mock.get(url__startswith="https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(
                200,
                json={"results": {"bindings": [{"ingLabel": {"value": "paneer"}}]}},
            )
        )
        mock.get(url__startswith="https://taginfo.openstreetmap.org").mock(
            return_value=httpx.Response(200, json={"data": [{"count": 200}]})
        )
        mock.post(url__startswith="https://overpass-api.de").mock(
            return_value=httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "lat": 22.58,
                            "lon": 88.37,
                            "tags": {"name": "Kolkata Mithai Shop"},
                        }
                    ]
                },
            )
        )

        tax = await fetch_off_taxonomy()
        assert "en:paneer" in tax

        off_ev = await search_off_product_evidence("paneer")
        assert len(off_ev) == 2
        assert off_ev[0].citation == "off:890123"

        wk_ev = await fetch_wikidata_evidence("Paneer Tikka", local_names=("पनीर टिक्का",))
        assert len(wk_ev) == 1
        assert "paneer" in wk_ev[0].ingredients

        # Test OSM in non-demo mode using mocked HTTP client
        s = get_settings()
        monkeypatch.setattr(s, "demo_mode", False)
        async with httpx.AsyncClient() as client:
            assert await validate_osm_tag("shop", "confectionery", client=client) is True
            vendors = await query_overpass_vendors(
                22.57, 88.36, "shop", "confectionery", client=client
            )
            assert len(vendors) == 1
            assert vendors[0]["name"] == "Kolkata Mithai Shop"


def test_cli_commands_and_secret_status(tmp_path: pytest.TempPathFactory) -> None:
    from pathlib import Path

    from typer.testing import CliRunner

    from hostora.cli import _check_secret_status, app

    runner = CliRunner()
    res_keys = runner.invoke(app, ["gen-keys"])
    assert res_keys.exit_code == 0
    assert "STORE_TOKEN_KEY=" in res_keys.stdout

    res_data = runner.invoke(app, ["data-check"])
    assert res_data.exit_code == 0
    assert "Channels: 7" in res_data.stdout

    metrics_file = Path(str(tmp_path)) / "metrics.md"
    res_sim = runner.invoke(
        app, ["simulate", "--events", "50", "--seed", "7", "--metrics-path", str(metrics_file)]
    )
    assert res_sim.exit_code == 0
    assert metrics_file.exists()
    # Second run updates existing markers
    res_sim2 = runner.invoke(
        app, ["simulate", "--events", "50", "--seed", "7", "--metrics-path", str(metrics_file)]
    )
    assert res_sim2.exit_code == 0

    assert _check_secret_status(None) == "MISSING"
    assert _check_secret_status("short") == "MALFORMED"
    assert _check_secret_status("a" * 24) == "OK"
    assert _check_secret_status("not_b64!", kind="b64_32") == "MALFORMED"
    assert _check_secret_status("not_hex!", kind="hex_32") == "MALFORMED"
    assert _check_secret_status("http://bad", kind="db_url") == "MALFORMED"
    assert _check_secret_status("postgresql://localhost/db", kind="db_url") == "OK"

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        res_doc = pool.submit(runner.invoke, app, ["doctor"]).result()
    assert res_doc.exit_code == 0
    assert "[OK] Env STORE_TOKEN_KEY" in res_doc.stdout


@pytest.mark.asyncio
async def test_groq_and_gemini_clients_with_respx() -> None:
    import httpx
    import respx

    from hostora.adapters.llm.base import LlmClientError
    from hostora.adapters.llm.gemini import GeminiClient
    from hostora.adapters.llm.groq import GroqClient

    groq = GroqClient(api_key="gsk_test", model="llama-3.1-8b-instant")
    gemini = GeminiClient(api_key="gem_test", model="gemini-2.5-flash")

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"items": []}'}}]},
            )
        )
        mock.post(url__startswith="https://generativelanguage.googleapis.com").mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": '{"items": []}'}]}}]},
            )
        )

        assert await groq.complete_json("sys", "usr", 200) == '{"items": []}'
        assert await groq.complete("usr", "sys") == '{"items": []}'
        assert await gemini.complete_json("sys", "usr", 200) == '{"items": []}'
        assert await gemini.complete("usr", "sys") == '{"items": []}'

        # 429 rate limit
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(429, text="rate_limited")
        )
        mock.post(url__startswith="https://generativelanguage.googleapis.com").mock(
            return_value=httpx.Response(429, text="rate_limited")
        )
        with pytest.raises(LlmClientError):
            await groq.complete("usr")
        with pytest.raises(LlmClientError):
            await gemini.complete("usr")
