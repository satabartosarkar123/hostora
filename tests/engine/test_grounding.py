"""Tests for runtime grounding, calibration, portions and pricing.

The vocabulary below is a TEST FIXTURE that stands in for what the app builds at runtime from
knowledge sources. Production code never ships a word list.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hostora.engine import CatalogItem, RuleSet, apply_rules
from hostora.engine.calibration import conformal_threshold, empirical_risk
from hostora.engine.grounding import (
    Evidence,
    GroundingPolicy,
    ProposedItem,
    Vocabulary,
    agreement,
    ground,
    quote_supported,
    tokenize,
)
from hostora.engine.portions import (
    MAX_RATIO,
    MIN_RATIO,
    Observation,
    ServingKind,
    consumption_row,
    posterior_portion,
    units_per_guests,
)
from hostora.engine.pricing import BuyOption, PriceQuote, choose_price, rank_options

RULES = {
    "halal": RuleSet("halal", "Halal", ("pork", "alcohol")),
    "jain": RuleSet(
        "jain", "Jain", ("meat", "fish", "egg", "root_veg", "onion", "garlic", "honey")
    ),
    "pure_veg": RuleSet("pure_veg", "Pure veg", ("meat", "fish", "egg")),
    "no_beef": RuleSet("no_beef", "No beef", ("beef",)),
    "no_alcohol": RuleSet("no_alcohol", "No alcohol", ("alcohol",)),
    "eggless": RuleSet("eggless", "Eggless", ("egg",)),
    "vegan": RuleSet("vegan", "Vegan", ("meat", "fish", "egg", "allergen:dairy", "honey")),
}
CONFIRM = {"halal": {"meat": "Confirm the meat comes from a halal-certified supplier."}}

RESTRICTED = {
    "chicken": ["chicken"],
    "चिकन": ["chicken"],
    "murgh": ["chicken"],
    "mutton": ["mutton"],
    "pork": ["pork"],
    "lard": ["pork"],
    "beef": ["beef"],
    "prawn": ["prawn"],
    "egg": ["egg"],
    "anda": ["egg"],
    "onion": ["onion"],
    "garlic": ["garlic"],
    "potato": ["root_veg"],
    "wine": ["wine"],
    "rum": ["alcohol"],
    "honey": ["honey"],
    "milk": ["dairy", "allergen:dairy"],
    "paneer": ["dairy", "allergen:dairy"],
}
BENIGN = [
    "rice",
    "basmati",
    "tomato",
    "salt",
    "sugar",
    "flour",
    "cumin",
    "coriander",
    "cocoa",
    "water",
]
DISH_WORDS = [
    "biryani",
    "curry",
    "masala",
    "fry",
    "cake",
    "kebab",
    "बिरयानी",
    "plate",
    "glass",
    "ribs",
]
VOCAB = Vocabulary.build(
    [(f, t) for f, t in RESTRICTED.items()]
    + [(f, []) for f in BENIGN + DISH_WORDS]
    + [("wine glass", [])],
    negations=[("eggless", ["egg"]), ("egg free", ["egg"])],
)


def kb(*ingredients: str, cite: str | None = "https://www.wikidata.org/wiki/Q1") -> Evidence:
    return Evidence(source="knowledge_base", ingredients=ingredients, citation=cite)


def verifier(*ingredients: str, agreement: float = 1.0, diet: str | None = None) -> Evidence:
    return Evidence(source="verifier", ingredients=ingredients, agreement=agreement, diet=diet)


def food(name: str) -> ProposedItem:
    return ProposedItem(name=name, category="food")


# ---------------------------------------------------------------- vocabulary


def test_tokenize_keeps_devanagari_words_whole() -> None:
    assert tokenize("चिकन बिरयानी!") == ["चिकन", "बिरयानी"]
    assert tokenize("Egg-free CAKE") == ["egg", "free", "cake"]


def test_scan_handles_plurals_phrases_and_negation() -> None:
    assert "fish" in VOCAB.scan("Prawns fry").tags
    assert VOCAB.scan("wine glasses").tags == frozenset()
    scan = VOCAB.scan("eggless cake")
    assert "egg" not in scan.tags
    assert not scan.conflicts
    assert VOCAB.scan("galouti kebab").unknown == ("galouti",)


# ---------------------------------------------------------------- decisions


def test_model_only_evidence_never_auto_approves_under_rules() -> None:
    proposer = Evidence(source="proposer", ingredients=("rice", "tomato"), diet="veg")
    out = ground(
        food("Tomato rice"), [proposer, verifier("rice", "tomato")], VOCAB, ["pure_veg"], RULES
    )
    assert out.status == "needs_confirmation"


def test_quorum_with_cited_knowledge_source_approves() -> None:
    evidence = [kb("rice", "tomato", "salt"), verifier("rice", "tomato")]
    out = ground(food("Tomato rice"), evidence, VOCAB, ["pure_veg"], RULES)
    assert out.status == "allowed"
    assert out.supporting == ("knowledge_base", "model")
    assert out.citations == ("https://www.wikidata.org/wiki/Q1",)


def test_uncited_or_low_agreement_evidence_does_not_count_as_support() -> None:
    uncited = Evidence(source="web", ingredients=("rice", "tomato"))
    out = ground(
        food("Tomato rice"), [uncited, verifier("rice", "tomato")], VOCAB, ["pure_veg"], RULES
    )
    assert out.status == "needs_confirmation"
    shaky = verifier("rice", "tomato", agreement=0.4)
    out = ground(food("Tomato rice"), [kb("rice", "tomato"), shaky], VOCAB, ["pure_veg"], RULES)
    assert out.status == "needs_confirmation"


def test_unknown_dish_resolved_by_knowledge_source_is_then_judged_on_ingredients() -> None:
    claims_veg = Evidence(source="proposer", ingredients=("flour", "cumin"), diet="veg")
    out = ground(food("Galouti kebab"), [claims_veg], VOCAB, ["pure_veg"], RULES)
    assert out.status == "needs_confirmation"
    assert any("galouti" in r for r in out.reasons)
    out = ground(
        food("Galouti kebab"), [claims_veg, kb("mutton", "cumin")], VOCAB, ["pure_veg"], RULES
    )
    assert out.status == "removed"
    assert "forbidden:meat" in out.reasons


def test_devanagari_name_is_caught() -> None:
    out = ground(food("चिकन बिरयानी"), [], VOCAB, ["pure_veg"], RULES)
    assert out.status == "removed"


def test_negation_in_name_and_contradiction() -> None:
    evidence = [kb("flour", "sugar", "milk"), verifier("flour", "sugar", "milk")]
    assert ground(food("Eggless cake"), evidence, VOCAB, ["eggless"], RULES).status == "allowed"
    out = ground(food("Eggless egg cake"), evidence, VOCAB, ["eggless"], RULES)
    assert out.status == "needs_confirmation"
    assert any("disagree" in r for r in out.reasons)
    out = ground(food("Eggless cake"), [*evidence, kb("egg", "flour")], VOCAB, ["eggless"], RULES)
    assert out.status == "removed"


def test_halal_meat_needs_host_or_label_confirmation() -> None:
    evidence = [kb("chicken", "rice"), verifier("chicken", "rice")]
    out = ground(food("Chicken biryani"), evidence, VOCAB, ["halal"], RULES, CONFIRM)
    assert out.status == "needs_confirmation"
    assert CONFIRM["halal"]["meat"] in out.reasons
    model_claim = Evidence(source="verifier", tags=frozenset({"confirmed:halal"}))
    out = ground(
        food("Chicken biryani"), [*evidence, model_claim], VOCAB, ["halal"], RULES, CONFIRM
    )
    assert out.status == "needs_confirmation"
    host = Evidence(source="host", tags=frozenset({"confirmed:halal"}))
    out = ground(food("Chicken biryani"), [*evidence, host], VOCAB, ["halal"], RULES, CONFIRM)
    assert out.status == "allowed"


def test_non_food_category_cannot_smuggle_food() -> None:
    glasses = ProposedItem(name="Wine glasses", category="tableware")
    assert ground(glasses, [], VOCAB, ["no_alcohol"], RULES).status == "allowed"
    ribs = ProposedItem(name="Pork ribs", category="decor")
    out = ground(ribs, [], VOCAB, ["halal"], RULES)
    assert out.status == "removed"
    assert out.category == "food"


def test_wrong_diet_claim_is_flagged_even_without_rules() -> None:
    proposer = Evidence(source="proposer", diet="veg")
    out = ground(food("Rice"), [proposer, kb("chicken", "rice")], VOCAB, [], RULES)
    assert out.status == "needs_confirmation"
    assert any("said veg" in r for r in out.reasons)


def test_product_label_is_authoritative() -> None:
    label = Evidence(
        source="product_label",
        ingredients=("flour", "sugar", "cocoa"),
        citation="off:8901234567890",
    )
    out = ground(food("Chocolate cake"), [label], VOCAB, ["eggless"], RULES)
    assert out.status == "allowed"


def test_no_rules_means_no_quorum_needed() -> None:
    out = ground(food("Galouti kebab"), [verifier("mutton")], VOCAB, [], RULES)
    assert out.status == "allowed"


def test_unknown_rule_raises() -> None:
    with pytest.raises(KeyError, match="Unknown rule set"):
        ground(food("Rice"), [], VOCAB, ["nope"], RULES)


def test_policy_is_configurable() -> None:
    evidence = [kb("rice", "tomato")]
    strict = ground(food("Tomato rice"), evidence, VOCAB, ["pure_veg"], RULES)
    relaxed = ground(
        food("Tomato rice"), evidence, VOCAB, ["pure_veg"], RULES, policy=GroundingPolicy(quorum=1)
    )
    assert (strict.status, relaxed.status) == ("needs_confirmation", "allowed")


# ---------------------------------------------------------------- properties

FORBIDDEN_FORMS = {
    rule_id: sorted(
        form
        for form, tags in RESTRICTED.items()
        if VOCAB.scan(form).tags & set(RULES[rule_id].forbid)
    )
    for rule_id in RULES
}
RULES_WITH_FORMS = sorted(r for r, forms in FORBIDDEN_FORMS.items() if forms)
PLACES = ["name", "proposer", "verifier", "knowledge_base", "web", "product_label"]


@given(
    rule_id=st.sampled_from(RULES_WITH_FORMS),
    data=st.data(),
    place=st.sampled_from(PLACES),
    filler=st.lists(st.sampled_from(BENIGN), max_size=3),
    casing=st.sampled_from([str.lower, str.upper, str.title]),
    agreement=st.floats(0, 1),
    cited=st.booleans(),
)
def test_property_any_source_mentioning_forbidden_removes(
    rule_id: str,
    data: st.DataObject,
    place: str,
    filler: list[str],
    casing: object,
    agreement: float,
    cited: bool,
) -> None:
    form = data.draw(st.sampled_from(FORBIDDEN_FORMS[rule_id]))
    word = casing(form)  # type: ignore[operator]
    name = " ".join([*filler, "curry"])
    evidence = [kb("rice", "tomato"), verifier("rice", "tomato", diet="veg")]  # all say "safe"
    if place == "name":
        name = f"{name} {word}"
    else:
        evidence.append(
            Evidence(
                source=place,  # type: ignore[arg-type]
                ingredients=(*filler, word),
                agreement=agreement,
                citation="https://example.org" if cited else None,
            )
        )
    out = ground(food(name), evidence, VOCAB, [rule_id], RULES)
    assert out.status == "removed"


@given(
    ingredients=st.lists(st.sampled_from(BENIGN + list(RESTRICTED)), min_size=1, max_size=5),
    rule_ids=st.lists(st.sampled_from(sorted(RULES)), min_size=1, max_size=3, unique=True),
    sources=st.lists(
        st.sampled_from(["proposer", "verifier", "knowledge_base", "web"]), min_size=1, max_size=4
    ),
)
def test_property_allowed_always_means_grounded_and_safe(
    ingredients: list[str], rule_ids: list[str], sources: list[str]
) -> None:
    evidence = [
        Evidence(source=s, ingredients=tuple(ingredients), citation="https://example.org")  # type: ignore[arg-type]
        for s in sources
    ]
    out = ground(food("curry"), evidence, VOCAB, rule_ids, RULES, CONFIRM)
    if out.status == "allowed":
        forbidden = {t for r in rule_ids for t in RULES[r].forbid}
        assert not (out.tags & forbidden)
        assert len(out.supporting) >= 2
        assert set(out.supporting) - {"model"}


@given(
    tags=st.lists(
        st.sampled_from(["chicken", "pork", "beef", "egg", "onion", "wine", "honey", "paneer"]),
        unique=True,
    ),
    rule_ids=st.lists(st.sampled_from(sorted(RULES)), min_size=1, max_size=3, unique=True),
)
def test_property_removal_matches_engine_apply_rules(tags: list[str], rule_ids: list[str]) -> None:
    label = Evidence(source="product_label", tags=frozenset(tags), citation="off:1")
    out = ground(food("curry"), [label], VOCAB, rule_ids, RULES)
    item = CatalogItem(
        id="x",
        name="x",
        category="food",
        course="main",
        unit="g",
        pack_sizes=(1,),
        tags=(*tags, "declared", "diet:x"),
        est_price_per_unit_inr=0,
        search_query="x",
    )
    removed_by_engine = not apply_rules([item], rule_ids, RULES).allowed
    assert (out.status == "removed") == removed_by_engine


# ---------------------------------------------------------------- calibration


def test_conformal_threshold_example() -> None:
    scores = [i / 100 for i in range(100)]
    unsafe = [s < 0.3 for s in scores]  # low-confidence items are the unsafe ones
    t = conformal_threshold(scores, unsafe, alpha=0.05)
    assert t is not None
    assert empirical_risk(scores, unsafe, t) <= 0.05
    assert conformal_threshold([0.9] * 5, [False] * 5, alpha=0.1) is None  # too few points


@given(
    pairs=st.lists(st.tuples(st.floats(0, 1), st.booleans()), min_size=20, max_size=200),
    alpha=st.floats(0.05, 0.5),
)
def test_property_conformal_threshold_is_valid_and_minimal(
    pairs: list[tuple[float, bool]], alpha: float
) -> None:
    scores = [p[0] for p in pairs]
    unsafe = [p[1] for p in pairs]
    n = len(scores)
    t = conformal_threshold(scores, unsafe, alpha)
    if t is None:
        return
    bound = (sum(1 for s, u in pairs if s >= t and u) + 1) / (n + 1)
    assert bound <= alpha
    lower = [c for c in sorted(set(scores)) if c < t]
    if lower:
        c = lower[-1]
        assert (sum(1 for s, u in pairs if s >= c and u) + 1) / (n + 1) > alpha


# ---------------------------------------------------------------- portions

RICE_MAIN = ServingKind(
    id="rice_main",
    course="main",
    unit="g",
    per_adult=300,
    per_child=150,
    pack_sizes=(1000,),
    meal_factor={"snacks": 0.5},
    basis="test",
)


def test_consumption_row_and_per_guest_units() -> None:
    assert consumption_row(RICE_MAIN, "veg_biryani", "snacks").per_adult == 150
    decor = ServingKind("decor", "decor", "piece", 0, 0, (1,), per_guests=50)
    assert units_per_guests(decor, 0) == 1
    assert units_per_guests(decor, 101) == 3


def test_posterior_portion_moves_with_outcomes_and_is_clamped() -> None:
    assert posterior_portion(300, 5, []) == 300
    short = [Observation(300, "ran_short")] * 5
    left = [Observation(300, "leftover", 0.4)] * 5
    assert posterior_portion(300, 5, short) > 300
    assert posterior_portion(300, 5, left) < 300
    extreme = [Observation(5000, "ran_short")] * 100
    assert posterior_portion(300, 5, extreme) == 300 * MAX_RATIO
    tiny = [Observation(1, "leftover", 0.9)] * 100
    assert posterior_portion(300, 5, tiny) == 300 * MIN_RATIO


@given(st.integers(0, 30), st.integers(0, 30))
def test_property_more_ran_short_never_lowers_portion(a: int, b: int) -> None:
    few = [Observation(300, "ran_short")] * min(a, b) + [Observation(300, "about_right")] * max(
        a, b
    )
    many = [Observation(300, "ran_short")] * max(a, b) + [Observation(300, "about_right")] * min(
        a, b
    )
    assert posterior_portion(300, 5, many) >= posterior_portion(300, 5, few)


# ---------------------------------------------------------------- pricing

TODAY = date(2026, 11, 1)


def test_choose_price_prefers_trusted_fresh_sources() -> None:
    quotes = [
        PriceQuote("ai_estimate", 0.3, 0.6, TODAY, "model"),
        PriceQuote("host_reported", 0.40, 0.40, TODAY - timedelta(days=10)),
        PriceQuote("host_reported", 0.50, 0.50, TODAY - timedelta(days=3)),
        PriceQuote("host_reported", 0.45, 0.45, TODAY - timedelta(days=90)),  # stale
        PriceQuote("live", 0.55, 0.55, TODAY - timedelta(days=5)),  # stale for live
    ]
    choice = choose_price(quotes, TODAY)
    assert choice is not None
    assert (choice.source, choice.samples, choice.low, choice.high) == (
        "host_reported",
        2,
        0.40,
        0.50,
    )
    assert choice.mid == pytest.approx(0.45)
    assert choose_price([PriceQuote("live", 1, 1, TODAY)], TODAY).source == "live"  # type: ignore[union-attr]
    assert choose_price([], TODAY) is None


def test_rank_options_priced_first_then_search_links() -> None:
    options = [
        BuyOption("amazon", "Amazon", "https://a/s?k=x", None),
        BuyOption("instamart", "Instamart", "https://i/1", 0.9, 500, "Brand A"),
        BuyOption("instamart", "Instamart", "https://i/2", 0.7, 1000, "Brand B"),
        BuyOption("blinkit", "Blinkit", "https://b/s?q=x", None),
        BuyOption("amazon", "Amazon", "https://a/s?k=x", None),  # duplicate
    ]
    ranked = rank_options(options, limit=4)
    assert [o.url for o in ranked] == [
        "https://i/2",
        "https://i/1",
        "https://a/s?k=x",
        "https://b/s?q=x",
    ]


# ---------------------------------------------------------------- helpers


def test_quote_supported_drops_ingredients_not_in_quotes() -> None:
    quotes = ["Galouti kebab is made from finely minced mutton, papaya and spices."]
    ok, dropped = quote_supported(["mutton", "spices", "chicken"], quotes)
    assert ok == ("mutton", "spices")
    assert dropped == ("chicken",)


def test_agreement_is_majority_share() -> None:
    a, b = frozenset({"meat"}), frozenset()
    assert agreement([a, a, b]) == pytest.approx(2 / 3)
    assert agreement([a, a, a]) == 1.0
    assert agreement([]) == 0.0
