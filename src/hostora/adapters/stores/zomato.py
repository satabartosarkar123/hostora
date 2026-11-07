"""Zomato MCP store adapter (AGENTS.md section 25.2).

Uses the official Zomato MCP server (`https://mcp-server.zomato.com/mcp`) with runtime tool
discovery. Never places an order or takes payment; fills the host's cart and hands off to
Zomato checkout.
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


class ZomatoStoreAdapter:
    store_name = "zomato"

    def __init__(
        self,
        *,
        access_token: str | None = None,
        server_override: Any = None,
        forbidden_terms: Sequence[str] = (),
    ) -> None:
        self.access_token = access_token
        self.server_override = server_override
        self.forbidden_terms = tuple(forbidden_terms)

    def _endpoints(self) -> tuple[str, str]:
        cfg = get_store_config("zomato")
        mcp_url = str(cfg.get("mcp_url", "https://mcp-server.zomato.com/mcp"))
        checkout_url = str(cfg.get("checkout_url", "https://www.zomato.com/checkout"))
        return mcp_url, checkout_url

    async def search_live_offers(
        self,
        *,
        item_id: str,
        query: str,
        category: str,
        lat: float = 22.5726,
        lng: float = 88.3639,
    ) -> list[LiveStoreOffer]:
        if category != "food":
            return []
        mcp_url, _checkout_url = self._endpoints()
        async with open_store_mcp_session(
            "zomato",
            mcp_url,
            access_token=self.access_token,
            server_override=self.server_override,
            forbidden_terms=self.forbidden_terms,
            required_capabilities=("search_restaurants",),
        ) as sess:
            raw = await sess.call_capability(
                "search_restaurants",
                {"keyword": query, "lat": lat, "lng": lng},
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
                entry.get("eta_max") or entry.get("eta_max_minutes") or max(eta_min, 50.0)
            )
            url = str(entry.get("url") or f"https://www.zomato.com/search?q={quote_plus(query)}")
            offers.append(
                LiveStoreOffer(
                    store="zomato",
                    channel="zomato",
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

    async def build_cart(
        self,
        *,
        quantities: Mapping[str, int],
        lat: float = 22.5726,
        lng: float = 88.3639,
    ) -> CartBuildResult:
        """Call Zomato's MCP cart tool exactly once with confirmed quantities and return
        the Zomato checkout URL. Never calls any order-placing or payment tool."""
        mcp_url, default_checkout = self._endpoints()
        clean_items = [
            {"item_id": str(k), "quantity": int(v)} for k, v in quantities.items() if int(v) > 0
        ]
        async with open_store_mcp_session(
            "zomato",
            mcp_url,
            access_token=self.access_token,
            server_override=self.server_override,
            forbidden_terms=self.forbidden_terms,
            required_capabilities=("cart_add",),
        ) as sess:
            raw = await sess.call_capability(
                "cart_add",
                {"items": clean_items, "lat": lat, "lng": lng},
            )

        total_inr = float(raw.get("total_inr") or raw.get("total") or 0.0)
        eta_min = float(raw.get("eta_min") or 25.0)
        eta_max = float(raw.get("eta_max") or 45.0)
        checkout_url = str(raw.get("checkout_url") or default_checkout)
        return CartBuildResult(
            store="zomato",
            channel="zomato",
            added_items=len(clean_items),
            total_inr=total_inr,
            eta_min_minutes=eta_min,
            eta_max_minutes=eta_max,
            checkout_url=checkout_url,
        )
