from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from hostora.data.loader import Channel, get_data
from hostora.engine.delivery import Eta, feasible
from hostora.engine.delivery import Offer as DeliveryOffer
from hostora.engine.pricing import BuyOption, rank_options
from hostora.schemas.plan import BuyOptionSchema, Offer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_IST = ZoneInfo("Asia/Kolkata")

TIME_LEFT_PRESETS: tuple[tuple[int, str], ...] = (
    (60, "1 hour"),
    (120, "2 hours"),
    (360, "6 hours"),
    (1440, "Tomorrow"),
    (10080, "Next week"),
)


def compute_minutes_left(starts_at: datetime, now: datetime | None = None) -> int:
    """Compute minutes remaining until ``starts_at`` in ``Asia/Kolkata``."""
    ref = (now or datetime.now(_IST)).astimezone(_IST)
    target = starts_at.astimezone(_IST)
    delta_minutes = int((target - ref).total_seconds() // 60)
    return max(0, delta_minutes)


def format_arrival_clock(eta_max_minutes: float, now: datetime | None = None) -> str:
    """Format expected arrival wall-clock time (HH:MM IST) given ``eta_max_minutes``."""
    ref = (now or datetime.now(_IST)).astimezone(_IST)
    arrival = ref + timedelta(minutes=max(0.0, eta_max_minutes))
    return arrival.strftime("%H:%M")


def channel_declared_eta(ch: Channel) -> Eta:
    return Eta(
        min_minutes=float(ch.eta_minutes[0]),
        max_minutes=float(ch.eta_minutes[1]),
        source="declared",
    )


def resolve_channel_eta(
    ch: Channel,
    learned_etas: Mapping[str, Eta] | None = None,
    live_etas: Mapping[str, Eta] | None = None,
) -> Eta:
    """Resolve channel ETA in strict preference order: live > learned > declared."""
    if live_etas and ch.id in live_etas:
        return live_etas[ch.id]
    if learned_etas and ch.id in learned_etas:
        return learned_etas[ch.id]
    return channel_declared_eta(ch)


def candidate_channel_kinds(category: str, minutes_left: float) -> list[str]:
    """Return candidate channel kinds for an item category before feasibility filtering."""
    if category == "food":
        return ["food"]
    if category in ("beverage", "supply", "tableware"):
        if minutes_left <= 10080:
            return ["quick", "ecommerce"]
        return ["ecommerce", "quick"]
    # decor, lighting: multi-day ecommerce unless fulfilled by live quick commerce
    return ["ecommerce"]


def build_offers_for_item(
    item_or_category: Any,
    minutes_left: float = 10080.0,
    channels: Mapping[str, Channel] | None = None,
    search_query: str | None = None,
    *,
    safety_minutes: float = 30.0,
    learned_etas: Mapping[str, Eta] | None = None,
    live_etas: Mapping[str, Eta] | None = None,
    ranked_channels: Sequence[str] | None = None,
    live_offers: Sequence[DeliveryOffer] | None = None,
) -> list[Offer]:
    """Return only feasible offers for an item, filtered by ``engine.delivery.feasible`` first."""
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

    kinds_order = candidate_channel_kinds(category, minutes_left)
    encoded_query = quote_plus(query)

    offers_by_channel: dict[str, Offer] = {}

    # 1. Include live offers first if they pass the feasibility gate
    if live_offers:
        for lo in live_offers:
            if not feasible(lo.eta, minutes_left, safety_minutes):
                continue
            ch_meta = channel_map.get(lo.channel)
            label = ch_meta.label if ch_meta else lo.channel
            lo_kind: Literal["quick", "food", "ecommerce"] = (
                ch_meta.kind if ch_meta else ("quick" if lo.channel == "instamart" else "food")
            )
            offers_by_channel[lo.channel] = Offer(
                channel=lo.channel,
                label=label,
                kind=lo_kind,
                url=lo.url,
                eta_min_minutes=lo.eta.min_minutes,
                eta_max_minutes=lo.eta.max_minutes,
                eta_source=lo.eta.source,
                price_inr=lo.price_inr,
            )

    # 2. Check declared/learned channels through the feasibility gate
    feasible_channels: list[Channel] = []
    for k_name in kinds_order:
        for ch in channel_map.values():
            if ch.kind != k_name:
                continue
            eta = resolve_channel_eta(ch, learned_etas=learned_etas, live_etas=live_etas)
            if feasible(eta, minutes_left, safety_minutes):
                feasible_channels.append(ch)

    if ranked_channels:
        order_idx = {cid: idx for idx, cid in enumerate(ranked_channels)}
        feasible_channels.sort(key=lambda c: order_idx.get(c.id, 999))

    for ch in feasible_channels:
        if ch.id in offers_by_channel:
            continue
        eta = resolve_channel_eta(ch, learned_etas=learned_etas, live_etas=live_etas)
        offers_by_channel[ch.id] = Offer(
            channel=ch.id,
            label=ch.label,
            kind=ch.kind,
            url=ch.url.replace("{q}", encoded_query),
            eta_min_minutes=eta.min_minutes,
            eta_max_minutes=eta.max_minutes,
            eta_source=eta.source,
            price_inr=None,
        )

    return list(offers_by_channel.values())


def build_delivery_offers_for_item(
    item_or_category: Any,
    minutes_left: float = 10080.0,
    channels: Mapping[str, Channel] | None = None,
    search_query: str | None = None,
    *,
    safety_minutes: float = 30.0,
    learned_etas: Mapping[str, Eta] | None = None,
    live_etas: Mapping[str, Eta] | None = None,
    live_offers: Sequence[DeliveryOffer] | None = None,
) -> list[DeliveryOffer]:
    """Build candidate ``engine.delivery.Offer`` list (including infeasible ones so ``plan_carts``
    can also evaluate feasibility and classify unfulfillable items)."""
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

    kinds_order = candidate_channel_kinds(category, minutes_left)
    encoded_query = quote_plus(query)
    out: list[DeliveryOffer] = list(live_offers or [])
    seen_live = {o.channel for o in out}

    for kind in kinds_order:
        for ch in channel_map.values():
            if ch.kind != kind or ch.id in seen_live:
                continue
            eta = resolve_channel_eta(ch, learned_etas=learned_etas, live_etas=live_etas)
            out.append(
                DeliveryOffer(
                    channel=ch.id,
                    eta=eta,
                    price_inr=None,
                    url=ch.url.replace("{q}", encoded_query),
                )
            )
    return out


def build_buy_options_for_item(
    category: str,
    query: str,
    minutes_left: float = 10080.0,
    channels: Mapping[str, Channel] | None = None,
    checked_options: list[BuyOption] | None = None,
    *,
    safety_minutes: float = 30.0,
    learned_etas: Mapping[str, Eta] | None = None,
    live_etas: Mapping[str, Eta] | None = None,
    ranked_channels: Sequence[str] | None = None,
    live_offers: Sequence[DeliveryOffer] | None = None,
) -> list[BuyOptionSchema]:
    """Rank feasible priced options and channel search links using ``rank_options``."""
    raw_offers = build_offers_for_item(
        category,
        minutes_left,
        channels,
        search_query=query,
        safety_minutes=safety_minutes,
        learned_etas=learned_etas,
        live_etas=live_etas,
        ranked_channels=ranked_channels,
        live_offers=live_offers,
    )
    offer_by_channel = {off.channel: off for off in raw_offers}
    feasible_channel_ids = set(offer_by_channel)

    all_options: list[BuyOption] = []
    for opt in checked_options or []:
        if opt.channel in feasible_channel_ids:
            all_options.append(opt)

    for off in raw_offers:
        all_options.append(
            BuyOption(
                channel=off.channel,
                label=off.label,
                url=off.url,
                inr_per_unit=off.price_inr,
            )
        )

    ranked = rank_options(all_options, limit=4)
    result: list[BuyOptionSchema] = []
    for opt in ranked:
        matched_off = offer_by_channel.get(opt.channel)
        result.append(
            BuyOptionSchema(
                channel=opt.channel,
                label=opt.label,
                url=opt.url,
                inr_per_unit=opt.inr_per_unit,
                pack_size=opt.pack_size,
                product_name=opt.product_name,
                eta_min_minutes=matched_off.eta_min_minutes if matched_off else None,
                eta_max_minutes=matched_off.eta_max_minutes if matched_off else None,
                eta_source=matched_off.eta_source if matched_off else "declared",
            )
        )
    return result
