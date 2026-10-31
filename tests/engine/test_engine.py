"""Engine tests, including property-based tests for the safety-critical rules."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hostora.engine import (
    Headcount,
    PassClaims,
    PlanLine,
    RuleSet,
    apply_rules,
    buffer_from_prediction,
    expand_tags,
    fit_budget,
    generate_key_pair_hex,
    make_link_token,
    normalize_code,
    predict_arrivals,
    raw_quantity,
    round_to_packs,
    sign_pass,
    split_household_code,
    verify_link_token,
    verify_pass,
)
from hostora.engine.budget import Alternative
from hostora.engine.constraints import CatalogItem, Category
from hostora.engine.prediction import ReplySeats
from hostora.engine.quantity import ConsumptionRow

RULES = {
    "halal": RuleSet("halal", "Halal", ("pork", "alcohol", "meat_unverified_halal")),
    "jain": RuleSet("jain", "Jain", ("meat", "fish", "egg", "root_veg", "onion", "garlic")),
    "pure_veg": RuleSet("pure_veg", "Pure veg", ("meat", "fish", "egg")),
    "no_beef": RuleSet("no_beef", "No beef", ("beef",)),
    "no_alcohol": RuleSet("no_alcohol", "No alcohol", ("alcohol",)),
}


def item(item_id: str, tags: tuple[str, ...], category: Category = "food") -> CatalogItem:
    return CatalogItem(
        id=item_id,
        name=item_id,
        category=category,
        course="main",
        unit="g",
        pack_sizes=(500, 1000),
        tags=tags,
        est_price_per_unit_inr=0.5,
        search_query=item_id,
    )


# ---------- codes ----------


def test_normalize_and_split_codes() -> None:
    assert normalize_code("ab1o2-il9xz") == "AB102119XZ"
    assert split_household_code("7KQ2M-9TX4P") == ("7KQ2M", "9TX4P")
    assert split_household_code("SHORT") is None
    assert normalize_code("ABU12") is None


def test_link_tokens_verify_and_reject_tampering() -> None:
    token = make_link_token("s3cret", "7KQ2M", "9TX4P")
    assert len(token) == 16
    assert verify_link_token("s3cret", "7KQ2M", token) == "9TX4P"
    assert verify_link_token("s3cret", "AAAAA", token) is None
    assert verify_link_token("other", "7KQ2M", token) is None
    tampered = token[:15] + ("1" if token.endswith("0") else "0")
    assert verify_link_token("s3cret", "7KQ2M", tampered) is None


# ---------- constraints ----------

ITEMS = [
    item("chicken_biryani", ("declared", "diet:nonveg", "chicken")),
    item("pork_vindaloo", ("declared", "diet:nonveg", "pork")),
    item("veg_biryani", ("declared", "diet:veg", "onion", "allergen:dairy")),
    item("paneer_tikka", ("declared", "diet:veg", "allergen:dairy")),
    item("mystery_curry", ("chicken",)),
    item("fairy_lights", (), "lighting"),
]


def test_halal_removes_pork_and_undeclared_keeps_decor() -> None:
    ids = [i.id for i in apply_rules(ITEMS, ["halal"], RULES).allowed]
    assert "chicken_biryani" in ids
    assert "fairy_lights" in ids
    assert "pork_vindaloo" not in ids
    assert "mystery_curry" not in ids


def test_jain_uses_implied_tags_and_warns_on_allergens() -> None:
    out = apply_rules(ITEMS, ["jain"], RULES, ["dairy"])
    assert sorted(i.id for i in out.allowed) == ["fairy_lights", "paneer_tikka"]
    assert out.allergen_warnings == [("paneer_tikka", "dairy")]


def test_unknown_rule_set_raises() -> None:
    with pytest.raises(KeyError, match="Unknown rule set"):
        apply_rules(ITEMS, ["nope"], RULES)


TAG_POOL = [
    "chicken",
    "pork",
    "beef",
    "mutton",
    "prawn",
    "egg",
    "onion",
    "potato",
    "wine",
    "paneer",
]


@given(
    tag_sets=st.lists(st.lists(st.sampled_from(TAG_POOL), unique=True), min_size=1, max_size=30),
    rule_ids=st.lists(st.sampled_from(sorted(RULES)), min_size=1, unique=True),
)
def test_property_no_forbidden_tag_survives(tag_sets: list[list[str]], rule_ids: list[str]) -> None:
    pool = [item(f"i{n}", ("declared", "diet:x", *tags)) for n, tags in enumerate(tag_sets)]
    forbidden = {t for r in rule_ids for t in RULES[r].forbid}
    for allowed in apply_rules(pool, rule_ids, RULES).allowed:
        assert not (expand_tags(allowed.tags) & forbidden)


# ---------- quantity ----------

ROW = ConsumptionRow("chicken_biryani", "dinner", per_adult=300, per_child=150, source="seed")


def test_quantity_formula() -> None:
    assert raw_quantity(Headcount(40, 4), ROW, 1, 0.65, 0) == 12600
    assert raw_quantity(Headcount(40, 4), ROW, 2, 0.65, 0) == pytest.approx(12600 * 2**-0.65)


def test_buffer_is_capped_at_ten_percent() -> None:
    assert buffer_from_prediction(100, 130) == 0.10
    assert buffer_from_prediction(100, 105) == pytest.approx(0.05)
    assert raw_quantity(Headcount(10, 0), ROW, 1, 0.65, 0.5) == pytest.approx(3300)


@given(st.integers(0, 500), st.integers(0, 500), st.integers(0, 50), st.integers(1, 8))
def test_property_more_guests_never_less_food(a: int, extra: int, c: int, k: int) -> None:
    assert raw_quantity(Headcount(a + extra, c), ROW, k) >= raw_quantity(Headcount(a, c), ROW, k)


def test_round_to_packs_examples() -> None:
    plan = round_to_packs(12600, [500, 1000])
    assert plan.packs == ((1000, 13),)
    assert plan.total == 13000
    plan = round_to_packs(1100, [500, 750])
    assert plan.packs == ((750, 1), (500, 1))
    assert plan.total == 1250
    assert round_to_packs(0, [500]).total == 0


@given(
    qty=st.integers(1, 50_000),
    sizes=st.lists(st.sampled_from([100, 200, 250, 500, 750, 1000, 2000]), min_size=1, unique=True),
)
def test_property_packs_cover_need_with_bounded_waste(qty: int, sizes: list[int]) -> None:
    plan = round_to_packs(qty, sizes)
    assert plan.total >= qty
    assert sum(size * count for size, count in plan.packs) == plan.total
    assert plan.total - qty < min(sizes) + max(sizes)


# ---------- prediction ----------


def test_prediction_is_ordered() -> None:
    p = predict_arrivals(ReplySeats(yes=410, maybe=60, no_reply=60))
    assert p.likely == round(410 * 0.96 + 60 * 0.7 + 60 * 0.3)
    assert p.low <= p.likely <= p.p80 <= p.high


def test_prediction_rejects_negative_seats() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        predict_arrivals(ReplySeats(yes=-1, maybe=0, no_reply=0))


@given(st.integers(0, 5000), st.integers(0, 5000), st.integers(0, 5000))
def test_property_prediction_ordered(yes: int, maybe: int, no_reply: int) -> None:
    p = predict_arrivals(ReplySeats(yes, maybe, no_reply))
    assert 0 <= p.low <= p.likely <= p.p80 <= p.high


# ---------- budget ----------


def test_budget_swaps_then_drops() -> None:
    result = fit_budget(
        [
            PlanLine(
                "biryani_premium",
                13,
                600,
                must_have=True,
                priority=9,
                alternatives=(Alternative("biryani_std", 400),),
            ),
            PlanLine("mocktails", 40, 60, must_have=False, priority=5),
            PlanLine("photo_booth", 1, 3000, must_have=False, priority=1),
        ],
        8000,
    )
    assert result.changes[0].startswith("swap biryani_premium -> biryani_std")
    assert [line.item_id for line in result.lines] == ["biryani_std", "mocktails"]
    assert result.total_inr == 13 * 400 + 40 * 60
    assert result.fits


# ---------- passes ----------


def test_passes_sign_verify_and_reject_forgery_and_expiry() -> None:
    private_hex, public_hex = generate_key_pair_hex()
    claims = PassClaims(event_key="7KQ2M", pass_id="01JC9ZPASS", seats=4, exp=2_000_000_000)
    token = sign_pass(claims, private_hex)
    assert verify_pass(token, public_hex, 1_900_000_000) == claims
    assert verify_pass(token.replace(".4.", ".9."), public_hex, 1_900_000_000) is None
    assert verify_pass(token, public_hex, 2_100_000_000) is None
    _, other_public = generate_key_pair_hex()
    assert verify_pass(token, other_public, 1_900_000_000) is None
    assert verify_pass("garbage", public_hex, 0) is None
    assert verify_pass(token[:-4] + "!!!!", public_hex, 1_900_000_000) is None
