"""Price evidence and buy-option ranking.

Every price shown to a host carries its source and date. Sources, most trusted first:
a live price from a connected store, a caterer's quote, prices other hosts actually paid, and
finally an AI estimate (a range, clearly labelled). Search links with no checked price are still
offered, ranked after options with a known price, and never called "cheapest".
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

PriceSource = Literal["live", "caterer_quote", "host_reported", "ai_estimate"]

SOURCE_RANK: dict[str, int] = {"live": 0, "caterer_quote": 1, "host_reported": 2, "ai_estimate": 3}
MAX_AGE_DAYS: dict[str, int] = {
    "live": 1,
    "caterer_quote": 30,
    "host_reported": 60,
    "ai_estimate": 30,
}


@dataclass(frozen=True, slots=True)
class PriceQuote:
    source: PriceSource
    low_inr_per_unit: float
    high_inr_per_unit: float
    observed_on: date
    detail: str = ""  # e.g. store name, "12 hosts in Kolkata", model name


@dataclass(frozen=True, slots=True)
class PriceChoice:
    source: PriceSource
    low: float
    mid: float
    high: float
    observed_on: date
    detail: str
    samples: int


def choose_price(quotes: Sequence[PriceQuote], today: date) -> PriceChoice | None:
    """Best available price: the most trusted source with fresh data; within host-reported
    prices, the median with the observed range. ``None`` when nothing is fresh."""
    fresh = [
        q
        for q in quotes
        if q.low_inr_per_unit >= 0
        and q.high_inr_per_unit >= q.low_inr_per_unit
        and 0 <= (today - q.observed_on).days <= MAX_AGE_DAYS[q.source]
    ]
    if not fresh:
        return None
    best_rank = min(SOURCE_RANK[q.source] for q in fresh)
    group = [q for q in fresh if SOURCE_RANK[q.source] == best_rank]
    source = group[0].source
    if source == "host_reported":
        mids = [(q.low_inr_per_unit + q.high_inr_per_unit) / 2 for q in group]
        latest = max(q.observed_on for q in group)
        return PriceChoice(
            source=source,
            low=min(q.low_inr_per_unit for q in group),
            mid=statistics.median(mids),
            high=max(q.high_inr_per_unit for q in group),
            observed_on=latest,
            detail=f"{len(group)} reported purchases",
            samples=len(group),
        )
    newest = max(group, key=lambda q: q.observed_on)
    return PriceChoice(
        source=source,
        low=newest.low_inr_per_unit,
        mid=(newest.low_inr_per_unit + newest.high_inr_per_unit) / 2,
        high=newest.high_inr_per_unit,
        observed_on=newest.observed_on,
        detail=newest.detail,
        samples=1,
    )


@dataclass(frozen=True, slots=True)
class BuyOption:
    channel: str
    label: str
    url: str
    inr_per_unit: float | None  # None for a plain search link (price not checked)
    pack_size: int | None = None
    product_name: str | None = None


def rank_options(options: Sequence[BuyOption], limit: int = 4) -> list[BuyOption]:
    """Options with a checked price first (cheapest per unit first), then search links in
    their given order (already ordered by delivery fit). Duplicated URLs are dropped."""
    seen: set[str] = set()
    priced: list[BuyOption] = []
    unpriced: list[BuyOption] = []
    for opt in options:
        if opt.url in seen:
            continue
        seen.add(opt.url)
        (priced if opt.inr_per_unit is not None else unpriced).append(opt)
    priced.sort(key=lambda o: (o.inr_per_unit or 0.0, o.channel))
    return (priced + unpriced)[: max(0, limit)]
