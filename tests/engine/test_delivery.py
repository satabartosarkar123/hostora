"""Delivery feasibility and cart planning."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hostora.engine.delivery import Eta, Offer, feasible, learned_eta, plan_carts

QUICK = Eta(10, 30, "declared")
FOOD = Eta(30, 60, "declared")
STANDARD = Eta(1440, 4320, "declared")  # 1 to 3 days


def offer(channel: str, eta: Eta, price: float | None = None) -> Offer:
    return Offer(channel=channel, eta=eta, price_inr=price, url=f"https://{channel}/x")


def test_two_hours_left_never_offers_multi_day_delivery() -> None:
    assert feasible(QUICK, 120, 30)
    assert feasible(FOOD, 120, 30)
    assert not feasible(STANDARD, 120, 30)
    assert feasible(STANDARD, 7 * 1440, 60)


def test_learned_eta_replaces_declared_after_enough_reports() -> None:
    assert learned_eta(QUICK, [12, 15]) == QUICK
    eta = learned_eta(QUICK, [12, 14, 15, 18, 22, 25, 40, 45, 50, 55])
    assert eta.source == "learned"
    assert eta.min_minutes == 25
    assert eta.max_minutes == 55


def test_party_in_two_hours_masala_goes_to_quick_commerce() -> None:
    offers = {
        "garam_masala": [
            offer("amazon", STANDARD, 80),
            offer("instamart", QUICK, 95),
            offer("blinkit", QUICK, None),
        ],
        "paper_plates": [offer("amazon", STANDARD, 150), offer("instamart", QUICK, 180)],
        "biryani": [offer("swiggy_food", FOOD, 4200), offer("zomato", FOOD, 4000)],
        "fairy_lights": [offer("amazon", STANDARD, 300)],
    }
    plan = plan_carts(list(offers), offers, minutes_left=120, safety_minutes=30)
    assert "amazon" not in plan.carts
    assert plan.carts["instamart"] == ("garam_masala", "paper_plates")
    assert plan.choice["biryani"].channel == "zomato"  # cheaper among equals
    assert plan.unfulfillable == ("fairy_lights",)
    assert plan.latest_arrival_minutes <= 90


def test_fewest_carts_wins_over_slightly_cheaper_split() -> None:
    offers = {
        "a": [offer("zepto", QUICK, 10), offer("instamart", QUICK, 11)],
        "b": [offer("instamart", QUICK, 20)],
        "c": [offer("instamart", QUICK, 30)],
    }
    plan = plan_carts(list(offers), offers, 120, 30)
    assert list(plan.carts) == ["instamart"]


etas = st.sampled_from([QUICK, FOOD, STANDARD])
channels = st.sampled_from(["amazon", "instamart", "zepto", "swiggy_food", "zomato"])
offer_lists = st.lists(
    st.builds(offer, channels, etas, st.one_of(st.none(), st.floats(1, 5000))), max_size=5
)


@given(
    offers=st.dictionaries(st.sampled_from(list("abcdefgh")), offer_lists, min_size=1),
    minutes_left=st.floats(0, 10_000),
    safety=st.floats(0, 120),
)
def test_property_every_choice_arrives_in_time_and_nothing_is_lost(
    offers: dict[str, list[Offer]], minutes_left: float, safety: float
) -> None:
    plan = plan_carts(list(offers), offers, minutes_left, safety)
    for item, o in plan.choice.items():
        assert feasible(o.eta, minutes_left, safety)
        assert o in offers[item]
    covered = {i for items in plan.carts.values() for i in items}
    assert covered == set(plan.choice)
    assert covered | set(plan.unfulfillable) == set(offers)
    assert not covered & set(plan.unfulfillable)
    for item in plan.unfulfillable:
        assert not any(feasible(o.eta, minutes_left, safety) for o in offers[item])


def test_eta_validation() -> None:
    with pytest.raises(ValueError, match="min_minutes"):
        Eta(30, 10, "declared")
