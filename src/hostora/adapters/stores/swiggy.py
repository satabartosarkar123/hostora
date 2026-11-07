"""Swiggy Instamart and Swiggy Food MCP store adapter (AGENTS.md section 25.2).

Uses the official Swiggy Builders Club MCP endpoints (`https://mcp.swiggy.com/im` and
`https://mcp.swiggy.com/food`) with runtime tool discovery. Never places an order or takes
payment; fills the host's cart and hands off to Swiggy checkout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote_plus

from hostora.adapters.stores.mcp_client import (
    CartBuildResult,
    LiveStoreOffer,
    get_store_config,
    open_store_mcp_session,
)


class SwiggyStoreAdapter:
    store_name = "swiggy"

    def __init__(
        self,
        *,
        access_token: str | None = None,
        instamart_server_override: Any = None,
        food_server_override: Any = None,
        forbidden_terms: Sequence[str] = (),
    ) -> None:
        self.access_token = access_token
        self.instamart_server_override = instamart_server_override
        self.food_server_override = food_server_override
        self.forbidden_terms = tuple(forbidden_terms)

    def _endpoints(self) -> tuple[str, str, str, str]:
        cfg = get_store_config("swiggy")
        im_url = str(cfg.get("instamart_mcp_url", "https://mcp.swiggy.com/im"))
        food_url = str(cfg.get("food_mcp_url", "https://mcp.swiggy.com/food"))
        im_checkout = str(
            cfg.get("checkout_instamart_url", "https://www.swiggy.com/instamart/checkout")
        )
        food_checkout = str(cfg.get("checkout_food_url", "https://www.swiggy.com/checkout"))
        return im_url, food_url, im_checkout, food_checkout

    async def search_instamart_offers(
        self,
        *,
        item_id: str,
        query: str,
        lat: float = 22.5726,
        lng: float = 88.3639,
    ) -> list[LiveStoreOffer]:
        im_url, _, _im_checkout, _ = self._endpoints()
        async with open_store_mcp_session(
            "swiggy",
            im_url,
            access_token=self.access_token,
            server_override=self.instamart_server_override,
            forbidden_terms=self.forbidden_terms,
            required_capabilities=("search_products",),
        ) as sess:
            raw = await sess.call_capability(
                "search_products",
                {"query": query, "lat": lat, "lng": lng},
            )
        items = raw.get("items") or raw.get("products") or []
        if not isinstance(items, list):
            return []

        offers: list[LiveStoreOffer] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            if not bool(entry.get("available", True)):
                continue
            prod_name = str(entry.get("name") or entry.get("product_name") or query)
            pack_size = max(1, int(entry.get("pack_size") or entry.get("quantity") or 1))
            unit = str(entry.get("unit") or "piece")
            price = float(entry.get("price_inr") or entry.get("price") or 0.0)
            if price <= 0:
                continue
            eta_min = float(entry.get("eta_min") or entry.get("eta_min_minutes") or 10.0)
            eta_max = float(
                entry.get("eta_max") or entry.get("eta_max_minutes") or max(eta_min, 20.0)
            )
            url = str(
                entry.get("url")
                or f"https://www.swiggy.com/instamart/search?query={quote_plus(query)}"
            )
            offers.append(
                LiveStoreOffer(
                    store="swiggy",
                    channel="instamart",
                    item_id=item_id,
                    product_name=prod_name,
                    pack_size=pack_size,
                    unit=unit,
                    price_inr=price,
                    inr_per_unit=round(price / pack_size, 4),
                    eta_min_minutes=eta_min,
                    eta_max_minutes=eta_max,
                    url=url,
                    available=True,
                )
            )
        return offers

    async def search_food_offers(
        self,
        *,
        item_id: str,
        query: str,
        lat: float = 22.5726,
        lng: float = 88.3639,
    ) -> list[LiveStoreOffer]:
        _, food_url, _, _food_checkout = self._endpoints()
        async with open_store_mcp_session(
            "swiggy",
            food_url,
            access_token=self.access_token,
            server_override=self.food_server_override,
            forbidden_terms=self.forbidden_terms,
            required_capabilities=("search_restaurants",),
        ) as sess:
            raw = await sess.call_capability(
                "search_restaurants",
                {"query": query, "lat": lat, "lng": lng},
            )
        items = raw.get("items") or raw.get("dishes") or raw.get("restaurants") or []
        if not isinstance(items, list):
            return []

        offers: list[LiveStoreOffer] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            if not bool(entry.get("available", True)):
                continue
            dish_name = str(entry.get("name") or entry.get("dish_name") or query)
            pack_size = max(1, int(entry.get("pack_size") or entry.get("serves") or 1))
            unit = str(entry.get("unit") or "piece")
            price = float(entry.get("price_inr") or entry.get("price") or 0.0)
            if price <= 0:
                continue
            eta_min = float(entry.get("eta_min") or entry.get("eta_min_minutes") or 25.0)
            eta_max = float(
                entry.get("eta_max") or entry.get("eta_max_minutes") or max(eta_min, 45.0)
            )
            url = str(
                entry.get("url") or f"https://www.swiggy.com/search?query={quote_plus(query)}"
            )
            offers.append(
                LiveStoreOffer(
                    store="swiggy",
                    channel="swiggy_food",
                    item_id=item_id,
                    product_name=dish_name,
                    pack_size=pack_size,
                    unit=unit,
                    price_inr=price,
                    inr_per_unit=round(price / pack_size, 4),
                    eta_min_minutes=eta_min,
                    eta_max_minutes=eta_max,
                    url=url,
                    available=True,
                )
            )
        return offers

    async def search_live_offers(
        self,
        *,
        item_id: str,
        query: str,
        category: str,
        lat: float = 22.5726,
        lng: float = 88.3639,
    ) -> list[LiveStoreOffer]:
        if category == "food":
            return await self.search_food_offers(item_id=item_id, query=query, lat=lat, lng=lng)
        if category in ("beverage", "supply", "tableware"):
            return await self.search_instamart_offers(
                item_id=item_id, query=query, lat=lat, lng=lng
            )
        return []

    async def build_cart(
        self,
        *,
        channel: str,
        quantities: Mapping[str, int],
        lat: float = 22.5726,
        lng: float = 88.3639,
    ) -> CartBuildResult:
        """Call the MCP cart tool exactly once for ``channel`` with confirmed quantities and
        return the Swiggy checkout URL. Never calls any order-placing or payment tool."""
        im_url, food_url, im_checkout, food_checkout = self._endpoints()
        is_instamart = channel == "instamart"
        url = im_url if is_instamart else food_url
        srv = self.instamart_server_override if is_instamart else self.food_server_override
        default_checkout = im_checkout if is_instamart else food_checkout

        clean_items = [
            {"item_id": str(k), "quantity": int(v)} for k, v in quantities.items() if int(v) > 0
        ]
        async with open_store_mcp_session(
            "swiggy",
            url,
            access_token=self.access_token,
            server_override=srv,
            forbidden_terms=self.forbidden_terms,
            required_capabilities=("cart_add",),
        ) as sess:
            raw = await sess.call_capability(
                "cart_add",
                {"items": clean_items, "lat": lat, "lng": lng},
            )

        total_inr = float(raw.get("total_inr") or raw.get("total") or 0.0)
        eta_min = float(raw.get("eta_min") or (12.0 if is_instamart else 25.0))
        eta_max = float(raw.get("eta_max") or (25.0 if is_instamart else 45.0))
        checkout_url = str(raw.get("checkout_url") or default_checkout)
        return CartBuildResult(
            store="swiggy",
            channel=channel,
            added_items=len(clean_items),
            total_inr=total_inr,
            eta_min_minutes=eta_min,
            eta_max_minutes=eta_max,
            checkout_url=checkout_url,
        )
