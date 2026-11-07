"""Unit tests for M5-R: STORE_TOKEN_KEY encryption round trip, MCP tool-capability mapping,
safety/privacy guards, and minutes_left calculation across midnight and timezones."""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
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

