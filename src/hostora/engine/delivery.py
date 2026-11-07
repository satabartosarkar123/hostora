"""Can it arrive in time, and from where: delivery feasibility and cart planning.

Time is measured in minutes until the function starts, not days. A shop is only ever shown for
an item if its delivery time (live ETA from a connected store, learned from hosts' reported
delivery times, or the shop's own declared promise as a cold-start prior) fits inside the time
left with a safety margin. Learning (``engine.bandit``) only reorders shops that pass this test.

``plan_carts`` then covers the whole shopping list with as few carts as possible (each extra
app or shop is another checkout, another delivery to wait for), preferring cheaper and faster
offers among equals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

EtaSource = Literal["live", "learned", "declared"]
MIN_REPORTS_TO_LEARN = 5


@dataclass(frozen=True, slots=True)
class Eta:
    min_minutes: float
    max_minutes: float
    source: EtaSource

    def __post_init__(self) -> None:
        if self.min_minutes < 0 or self.max_minutes < self.min_minutes:
            raise ValueError("need 0 <= min_minutes <= max_minutes")


def feasible(eta: Eta, minutes_left: float, safety_minutes: float) -> bool:
    """True only if even the slow end of the ETA plus the safety margin fits in time left."""
    return eta.max_minutes + max(0.0, safety_minutes) <= minutes_left


def learned_eta(declared: Eta, reported_minutes: Sequence[float]) -> Eta:
    """Replace a shop's declared promise with what hosts actually experienced: the median and
    90th percentile of reported delivery times, once there are enough reports."""
    reports = sorted(r for r in reported_minutes if r >= 0)
    if len(reports) < MIN_REPORTS_TO_LEARN:
        return declared
    median = reports[len(reports) // 2]
    p90 = reports[min(len(reports) - 1, int(0.9 * len(reports)))]
    return Eta(min_minutes=median, max_minutes=max(median, p90), source="learned")


@dataclass(frozen=True, slots=True)
class Offer:
    channel: str
    eta: Eta
    price_inr: float | None  # None when the price is not checked (search link)
    url: str


@dataclass(frozen=True, slots=True)
class CartPlan:
    carts: Mapping[str, tuple[str, ...]]  # channel -> item ids
    choice: Mapping[str, Offer]  # item id -> chosen offer
    unfulfillable: tuple[str, ...]  # items no shop can deliver in time
    latest_arrival_minutes: float


def plan_carts(
    items: Sequence[str],
    offers: Mapping[str, Sequence[Offer]],
    minutes_left: float,
    safety_minutes: float,
) -> CartPlan:
    """Greedy set cover over feasible offers: repeatedly open the cart (channel) that covers the
    most remaining items, breaking ties by lower checked total price, then faster ETA."""
    usable: dict[str, dict[str, Offer]] = {}
    for item in dict.fromkeys(items):
        best_by_channel: dict[str, Offer] = {}
        for o in offers.get(item, ()):
            if not feasible(o.eta, minutes_left, safety_minutes):
                continue
            cur = best_by_channel.get(o.channel)
            if cur is None or _offer_key(o) < _offer_key(cur):
                best_by_channel[o.channel] = o
        usable[item] = best_by_channel

    unfulfillable = tuple(i for i, chans in usable.items() if not chans)
    remaining = {i for i, chans in usable.items() if chans}
    carts: dict[str, list[str]] = {}
    choice: dict[str, Offer] = {}
    while remaining:
        channels = {c for i in remaining for c in usable[i]}

        pick = min(channels, key=lambda c: _cover_score(c, remaining, usable))
        covered = sorted(i for i in remaining if pick in usable[i])
        carts[pick] = covered
        for i in covered:
            choice[i] = usable[i][pick]
        remaining -= set(covered)

    latest = max((o.eta.max_minutes for o in choice.values()), default=0.0)
    return CartPlan(
        carts={c: tuple(v) for c, v in carts.items()},
        choice=choice,
        unfulfillable=unfulfillable,
        latest_arrival_minutes=latest,
    )


def _cover_score(
    channel: str, remaining: set[str], usable: Mapping[str, Mapping[str, Offer]]
) -> tuple[int, float, float, str]:
    covered = [i for i in remaining if channel in usable[i]]
    price = sum(usable[i][channel].price_inr or 0.0 for i in covered)
    eta = max(usable[i][channel].eta.max_minutes for i in covered)
    return (-len(covered), price, eta, channel)


def _offer_key(o: Offer) -> tuple[float, float]:
    return (o.price_inr if o.price_inr is not None else float("inf"), o.eta.max_minutes)
