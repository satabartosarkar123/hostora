"""Tests for the learning modules: turnout, newsvendor, Bayesian portions and elasticity,
Thompson sampling, preferences and budget optimisation, plus the data-driven grounding
semantics and absence handling."""

from __future__ import annotations

import itertools
import math
import random
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from hostora.engine import RuleSet
from hostora.engine.bandit import ArmStats, days_band, rank_arms
from hostora.engine.bayes import Obs, learn_elasticity, lognormal_posterior, outcome_to_obs
from hostora.engine.grounding import (
    DEFAULT_SEMANTICS,
    Evidence,
    ProposedItem,
    Vocabulary,
    ground,
    semantics_from_data,
)
from hostora.engine.newsvendor import critical_ratio, expected_cost, order_quantity
from hostora.engine.preferences import Variant, bradley_terry, optimize_budget
from hostora.engine.turnout import (
    MAX_STRENGTH,
    MIN_STRENGTH,
    HouseholdGroup,
    pinball_loss,
    pooling_strength,
    prior_from_rate,
    quantile,
    segment_rate,
    simulate_arrivals,
    update,
)

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------- turnout


def test_beta_prior_and_update() -> None:
    r = prior_from_rate(0.8, 10)
    assert (r.a, r.b) == pytest.approx((8, 2))
    r2 = update(r, 40, 50)
    assert r2.mean == pytest.approx(48 / 60)
    with pytest.raises(ValueError, match="arrived"):
        update(r, 6, 5)


def test_pooling_strength_responds_to_real_differences() -> None:
    same = [(80, 100), (160, 200), (40, 50)]
    assert pooling_strength(same) == MAX_STRENGTH
    different = [(95, 100), (50, 100), (20, 100), (90, 100)]
    s = pooling_strength(different)
    assert MIN_STRENGTH <= s < 20


@given(
    st.lists(st.tuples(st.integers(0, 200), st.integers(1, 200)), min_size=0, max_size=8).map(
        lambda xs: [(min(x, n), n) for x, n in xs]
    )
)
def test_property_pooling_strength_in_bounds(segments: list[tuple[int, int]]) -> None:
    assert MIN_STRENGTH <= pooling_strength(segments) <= MAX_STRENGTH


@given(st.floats(0.05, 0.95), st.integers(0, 50), st.integers(1, 50), st.floats(1, 200))
def test_property_segment_rate_between_parent_and_data(
    parent_mean: float, arrived: int, invited: int, strength: float
) -> None:
    arrived = min(arrived, invited)
    parent = prior_from_rate(parent_mean, 50)
    post = segment_rate(parent, arrived, invited, strength)
    lo, hi = sorted([parent.mean, arrived / invited])
    assert lo - 1e-9 <= post.mean <= hi + 1e-9


def test_simulation_is_deterministic_bounded_and_centred() -> None:
    groups = [
        HouseholdGroup("yes", 4, 100),
        HouseholdGroup("maybe", 3, 20),
        HouseholdGroup("no_reply", 4, 30),
    ]
    rates = {
        "yes": prior_from_rate(0.95, 400),
        "maybe": prior_from_rate(0.6, 400),
        "no_reply": prior_from_rate(0.3, 400),
    }
    a = simulate_arrivals(groups, rates, draws=2000, seed=7)
    assert a == simulate_arrivals(groups, rates, draws=2000, seed=7)
    assert all(0 <= x <= 4 * 100 + 3 * 20 + 4 * 30 for x in a)
    expected = 4 * 100 * 0.95 + 3 * 20 * 0.6 + 4 * 30 * 0.3
    assert sum(a) / len(a) == pytest.approx(expected, rel=0.02)
    assert quantile(a, 0.1) <= quantile(a, 0.5) <= quantile(a, 0.9)


def test_learning_converges_to_true_rate() -> None:
    rng = random.Random(1)
    true_rate = 0.82
    post = prior_from_rate(0.96, 5)  # starts from a (wrong) published estimate
    for _ in range(60):  # 60 events of 50 "yes" households
        arrived = sum(rng.random() < true_rate for _ in range(50))
        post = update(post, arrived, 50)
    assert post.mean == pytest.approx(true_rate, abs=0.02)


def test_pinball_loss() -> None:
    assert pinball_loss(100, 90, 0.8) == pytest.approx(8)
    assert pinball_loss(80, 90, 0.8) == pytest.approx(2)


# ---------------------------------------------------------------- newsvendor


def test_critical_ratio() -> None:
    assert critical_ratio(4, 1) == pytest.approx(0.8)
    with pytest.raises(ValueError, match="positive"):
        critical_ratio(0, 1)


@given(
    samples=st.lists(st.integers(0, 600), min_size=1, max_size=60),
    cost_short=st.floats(0.1, 20),
    cost_waste=st.floats(0.1, 20),
)
def test_property_order_quantity_minimises_expected_cost(
    samples: list[int], cost_short: float, cost_waste: float
) -> None:
    q = order_quantity(samples, cost_short, cost_waste)
    best = min(expected_cost(samples, c, cost_short, cost_waste) for c in set(samples))
    assert expected_cost(samples, q, cost_short, cost_waste) <= best + 1e-9


def test_worse_shortage_means_more_plates() -> None:
    samples = list(range(300, 501))
    assert (
        order_quantity(samples, 9, 1)
        > order_quantity(samples, 1, 1)
        > order_quantity(samples, 1, 9)
    )


# ---------------------------------------------------------------- bayes


def test_posterior_without_data_is_the_prior() -> None:
    post = lognormal_posterior(300, 0.3, 0.2, [])
    assert post.median == pytest.approx(300, rel=0.01)
    assert post.p10 < post.median < post.p90


def test_posterior_learns_from_exact_and_censored_data() -> None:
    exact = [Obs("exact", 400)] * 30
    assert lognormal_posterior(300, 0.3, 0.2, exact).median == pytest.approx(400, rel=0.05)
    ran_out = [Obs("at_least", 300)] * 10
    assert lognormal_posterior(300, 0.3, 0.2, ran_out).median > 300
    too_much = [Obs("at_most", 300)] * 10
    assert lognormal_posterior(300, 0.3, 0.2, too_much).median < 300
    band = [Obs("between", 250, 280)] * 20
    assert 250 <= lognormal_posterior(300, 0.3, 0.2, band).median <= 285


def test_posterior_is_physically_bounded() -> None:
    absurd = [Obs("exact", 1_000_000)] * 100
    post = lognormal_posterior(300, 0.3, 0.2, absurd, width=4.0)
    assert post.median <= 300 * math.exp(4 * 0.3) * 1.0001


@settings(max_examples=50, deadline=None)
@given(st.floats(150, 900), st.integers(1, 20))
def test_property_ran_out_never_lowers_the_estimate(level: float, n: int) -> None:
    base = lognormal_posterior(300, 0.3, 0.2, [], grid=201)
    more = lognormal_posterior(300, 0.3, 0.2, [Obs("at_least", level)] * n, grid=201)
    assert more.median >= base.median * 0.999


def test_outcome_to_obs() -> None:
    assert outcome_to_obs(300, "ran_short") == Obs("at_least", 300)
    assert outcome_to_obs(300, "leftover", 0.25) == Obs("exact", 225)
    assert outcome_to_obs(300, "about_right", right_tolerance=0.1) == Obs("between", 270, 300)
    with pytest.raises(ValueError, match="unknown"):
        outcome_to_obs(300, "weird")


def test_learn_elasticity() -> None:
    # true alpha 0.5: with k dishes, total = portion * k ** 0.5
    obs = [(k, k**0.5) for k in (2, 3, 4, 5)] * 20
    est = learn_elasticity(0.65, 0.15, 0.1, obs)
    assert est.mean == pytest.approx(0.5, abs=0.02)
    assert est.n == 80
    assert learn_elasticity(0.65, 0.15, 0.1, [(1, 5.0)]).n == 0  # k < 2 ignored
    extreme = learn_elasticity(0.65, 0.15, 0.01, [(2, 100.0)] * 50)
    assert 0.0 <= extreme.mean <= 1.0


# ---------------------------------------------------------------- bandit


def test_days_band() -> None:
    edges = [1, 3, 7, 30]
    assert [days_band(d, edges) for d in (0, 2, 5, 10, 90)] == [0, 1, 2, 3, 4]


def test_thompson_prefers_what_hosts_buy_but_still_explores() -> None:
    arms = ["blinkit", "amazon", "new_shop"]
    context = {"blinkit": ArmStats(60, 40), "amazon": ArmStats(5, 95)}
    first = [
        rank_arms(arms, context, {}, strength=4, rng=random.Random(seed))[0] for seed in range(400)
    ]
    assert first.count("blinkit") > first.count("amazon")
    assert first.count("new_shop") > 0  # unknown shops still get tried
    assert rank_arms(arms, context, {}, 4, random.Random(3)) == rank_arms(
        arms, context, {}, 4, random.Random(3)
    )


# ---------------------------------------------------------------- preferences


def test_bradley_terry_orders_by_wins() -> None:
    scores = bradley_terry(
        ["decor", "dessert", "starter"], [("dessert", "decor")] * 8 + [("starter", "dessert")] * 8
    )
    assert sum(scores.values()) == pytest.approx(1)
    assert scores["starter"] > scores["dessert"] > scores["decor"]
    flat = bradley_terry(["a", "b"], [])
    assert flat["a"] == pytest.approx(flat["b"])
    with pytest.raises(ValueError, match="bad comparison"):
        bradley_terry(["a"], [("a", "z")])


variants = st.lists(st.tuples(st.integers(0, 40), st.floats(0, 10)), min_size=1, max_size=3).map(
    lambda vs: [Variant(f"v{i}", float(c), u) for i, (c, u) in enumerate(vs)]
)


@settings(max_examples=150, deadline=None)
@given(groups=st.lists(variants, min_size=1, max_size=4), budget=st.integers(0, 120))
def test_property_budget_optimiser_matches_brute_force(
    groups: list[list[Variant]], budget: int
) -> None:
    got = optimize_budget(groups, budget, step_inr=1)
    feasible = [
        combo
        for combo in itertools.product(*[range(len(g)) for g in groups])
        if sum(g[k].cost_inr for g, k in zip(groups, combo, strict=True)) <= budget
    ]
    if not feasible:
        assert got is None
        return
    assert got is not None
    assert got.cost_inr <= budget
    best = max(sum(g[k].utility for g, k in zip(groups, c, strict=True)) for c in feasible)
    assert got.utility == pytest.approx(best)


def test_budget_optimiser_never_exceeds_budget_with_coarse_steps() -> None:
    groups = [
        [Variant("premium", 5_430, 9), Variant("standard", 3_210, 7)],
        [Variant("keep", 1_999, 3), Variant("drop", 0, 0)],
    ]
    got = optimize_budget(groups, 5_300, step_inr=100)
    assert got is not None
    assert got.cost_inr <= 5_300
    assert got.picks == (1, 0)  # standard + keep = 5,209, fits after rounding up
    assert got.cost_inr == 5_209


# ---------------------------------------------------------------- grounding semantics


RULES = {
    "eggless": RuleSet("eggless", "Eggless", ("egg",)),
    "pure_veg": RuleSet("pure_veg", "Pure veg", ("meat", "fish", "egg")),
}
VOCAB = Vocabulary.build(
    [("egg", ["egg"]), ("chicken", ["chicken"])]
    + [(w, []) for w in ("free", "cake", "flour", "sugar", "milk", "rice")]
)


def test_rules_yaml_semantics_match_engine_defaults() -> None:
    data = yaml.safe_load((ROOT / "data/rules/rules.yaml").read_text())
    assert semantics_from_data(data["semantics"]) == DEFAULT_SEMANTICS


def kb(*ing: str, absent: frozenset[str] = frozenset()) -> Evidence:
    return Evidence("knowledge_base", ingredients=ing, citation="wd:Q1", absent=absent)


def test_egg_free_name_cleared_only_by_independent_absence_evidence() -> None:
    item = ProposedItem("Egg free cake", "food")
    base = [kb("flour", "sugar", "milk"), Evidence("verifier", ingredients=("flour", "sugar"))]
    assert ground(item, base, VOCAB, ["eggless"], RULES).status == "removed"
    egg_absent = frozenset({"egg"})
    models_only = [
        kb("flour", "sugar", "milk"),
        Evidence("verifier", ingredients=("flour",), absent=egg_absent),
        Evidence("proposer", absent=egg_absent),
    ]
    assert ground(item, models_only, VOCAB, ["eggless"], RULES).status == "removed"
    grounded = [
        kb("flour", "sugar", "milk", absent=egg_absent),
        Evidence("verifier", ingredients=("flour", "sugar"), absent=egg_absent),
    ]
    out = ground(item, grounded, VOCAB, ["eggless"], RULES)
    assert out.status == "allowed"
    assert any("confirm it is absent" in r for r in out.reasons)
    contradicted = [*grounded, Evidence("web", ingredients=("egg",), citation="https://x")]
    assert ground(item, contradicted, VOCAB, ["eggless"], RULES).status == "removed"


def test_absence_never_overrides_an_asserted_ingredient() -> None:
    item = ProposedItem("Chicken rice", "food")
    claims = [
        kb("chicken", "rice", absent=frozenset({"chicken", "meat"})),
        Evidence("host", absent=frozenset({"chicken", "meat"})),
    ]
    assert ground(item, claims, VOCAB, ["pure_veg"], RULES).status == "removed"
