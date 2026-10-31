from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from hostora.data.loader import Channel, get_data
from hostora.engine.pricing import BuyOption, rank_options
from hostora.schemas.plan import BuyOptionSchema, Offer

if TYPE_CHECKING:
    from collections.abc import Mapping

_IST = ZoneInfo("Asia/Kolkata")


def compute_days_left(starts_at: datetime, now: datetime | None = None) -> int:
    ref = (now or datetime.now(_IST)).astimezone(_IST).date()
    target = starts_at.astimezone(_IST).date()
    return max(0, (target - ref).days)


def build_offers_for_item(
    item_or_category: Any,
    days_left: int = 7,
    channels: Mapping[str, Channel] | None = None,
    search_query: str | None = None,
) -> list[Offer]:
    channel_map = channels if channels is not None else get_data().channels

    if isinstance(item_or_category, str):
        category = item_or_category
        query = search_query or category
    else:
        category = getattr(item_or_category, "category", "food")
        query = str(
            getattr(item_or_category, "search_query", None)
            or getattr(item_or_category, "name", "item")
        )

    if category == "food":
        kinds_order = ["food"]
    elif category in ("beverage", "supply"):
        if days_left < 1:
            kinds_order = ["quick"]
        elif days_left <= 7:
            kinds_order = ["quick", "ecommerce"]
        else:
            kinds_order = ["ecommerce", "quick"]
    else:
        # decor, lighting, tableware
        if days_left < 1:
            kinds_order = ["quick"]
        elif days_left <= 7:
            kinds_order = ["ecommerce", "quick"]
        else:
            kinds_order = ["ecommerce"]

    encoded_query = quote_plus(query)
    offers: list[Offer] = []
    for kind in kinds_order:
        for ch in channel_map.values():
            if ch.kind == kind:
                offers.append(
                    Offer(
                        channel=ch.id,
                        label=ch.label,
                        kind=ch.kind,
                        url=ch.url.replace("{q}", encoded_query),
                    )
                )
    return offers


def build_buy_options_for_item(
    category: str,
    query: str,
    days_left: int = 7,
    channels: Mapping[str, Channel] | None = None,
    checked_options: list[BuyOption] | None = None,
) -> list[BuyOptionSchema]:
    """Rank priced options and channel search links using engine.pricing.rank_options."""
    raw_offers = build_offers_for_item(category, days_left, channels, search_query=query)
    all_options: list[BuyOption] = list(checked_options or [])

    for off in raw_offers:
        all_options.append(
            BuyOption(
                channel=off.channel,
                label=off.label,
                url=off.url,
                inr_per_unit=None,
            )
        )

    ranked = rank_options(all_options, limit=4)
    return [
        BuyOptionSchema(
            channel=opt.channel,
            label=opt.label,
            url=opt.url,
            inr_per_unit=opt.inr_per_unit,
            pack_size=opt.pack_size,
            product_name=opt.product_name,
        )
        for opt in ranked
    ]
