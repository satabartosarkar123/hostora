"""Simulation-based validation of learning loops (Milestone M5-L, AGENTS.md Section 24.10).

Simulates at least 200 synthetic events from known ground-truth parameters:
- Segment turnout rates differing from published priors;
- True portion sizes and dish-count elasticity alpha;
- True shop success rates;
- True Bradley-Terry preference ordering.

Asserts:
1. Turnout means within 0.03 of the truth in large segments;
2. 80% intervals cover 70-90% of held-out events;
3. Portion median within 10% of the truth;
4. Alpha within 0.05 of the truth;
5. Thompson sampling cumulative regret in 2nd half is below 1st half;
6. Bradley-Terry recovers true preference order with Kendall tau >= 0.8;
7. Bayesian model beats Phase-1 rules in pinball loss.
"""

from __future__ import annotations

import math
import random

from hostora.engine.bandit import ArmStats, rank_arms
from hostora.engine.bayes import learn_elasticity, lognormal_posterior, outcome_to_obs
from hostora.engine.prediction import ReplySeats, predict_arrivals
from hostora.engine.preferences import bradley_terry
from hostora.engine.turnout import (
    HouseholdGroup,
    pinball_loss,
    prior_from_rate,
    quantile,
    simulate_arrivals,
)
from hostora.engine.turnout import (
    update as update_beta,
)


def compute_kendall_tau(order_a: list[str], order_b: list[str]) -> float:
    """Compute Kendall's tau rank correlation between two permutations of same items."""
    assert len(order_a) == len(order_b)
    n = len(order_a)
    if n <= 1:
        return 1.0
    pos_a = {item: i for i, item in enumerate(order_a)}
    pos_b = {item: i for i, item in enumerate(order_b)}

    items = list(pos_a.keys())
    concordant = 0
    discordant = 0

    for i in range(n):
        for j in range(i + 1, n):
            u, v = items[i], items[j]
            diff_a = pos_a[u] - pos_a[v]
            diff_b = pos_b[u] - pos_b[v]
            if (diff_a > 0 and diff_b > 0) or (diff_a < 0 and diff_b < 0):
                concordant += 1
            else:
                discordant += 1

    total = concordant + discordant
    return (concordant - discordant) / total if total > 0 else 1.0


# Ground Truth Parameters for Synthetic Simulation
TRUE_TURNOUT_YES = 0.92
TRUE_TURNOUT_MAYBE = 0.62
TRUE_TURNOUT_NO_REPLY = 0.15
TRUE_PORTION_MEDIAN = 280.0
TRUE_ALPHA = 0.58
TRUE_SHOP_RATES = {"zepto": 0.75, "blinkit": 0.50, "instamart": 0.35, "amazon": 0.20}
TRUE_PREF_ORDER = ["food", "beverage", "tableware", "decor"]
N_EVENTS = 200
N_TRAIN = 160


def test_simulation_based_validation_200_events() -> None:
    """Run full simulation-based validation over 200 synthetic events."""
    rng = random.Random(20261101)  # Seeded and fully deterministic

    # Generate 200 synthetic events
    events_data = []

    for ev_id in range(N_EVENTS):
        # Household counts per status
        inv_yes = rng.randint(40, 100)
        inv_maybe = rng.randint(10, 30)
        inv_no_reply = rng.randint(20, 50)

        # Arrived draws from binomial(inv, true_rate)
        arr_yes = sum(1 for _ in range(inv_yes) if rng.random() < TRUE_TURNOUT_YES)
        arr_maybe = sum(1 for _ in range(inv_maybe) if rng.random() < TRUE_TURNOUT_MAYBE)
        arr_no_reply = sum(1 for _ in range(inv_no_reply) if rng.random() < TRUE_TURNOUT_NO_REPLY)

        total_arrived = arr_yes + arr_maybe + arr_no_reply
        total_invited = inv_yes + inv_maybe + inv_no_reply

        # Portion observations: draw consumption around true median with log-normal noise
        planned = 260.0 + rng.uniform(-20, 40)
        actual_eaten = rng.lognormvariate(math.log(TRUE_PORTION_MEDIAN), 0.15)
        if actual_eaten > planned * 1.15:
            outcome = "ran_short"
            leftover = 0.0
        elif actual_eaten < planned * 0.85:
            outcome = "leftover"
            leftover = (planned - actual_eaten) / planned
        else:
            outcome = "about_right"
            leftover = 0.0

        # Course elasticity observation: total / single_dish = k^(1 - alpha)
        k_dishes = rng.choice([2, 3, 4])
        obs_ratio = (k_dishes ** (1.0 - TRUE_ALPHA)) * rng.uniform(0.98, 1.02)

        events_data.append(
            {
                "id": ev_id,
                "yes": (arr_yes, inv_yes),
                "maybe": (arr_maybe, inv_maybe),
                "no_reply": (arr_no_reply, inv_no_reply),
                "total_arrived": total_arrived,
                "total_invited": total_invited,
                "portion_obs": outcome_to_obs(planned, outcome, leftover_fraction=leftover),
                "elasticity_obs": (k_dishes, obs_ratio),
            }
        )

    train_events = events_data[:N_TRAIN]
    held_out_events = events_data[N_TRAIN:]

    # -------------------------------------------------------------------------
    # CHECK 1: Turnout means within 0.03 of truth in large segments
    # -------------------------------------------------------------------------
    prior_strength = 5.0
    prior_yes = prior_from_rate(0.85, prior_strength)  # cold start prior 0.85 vs truth 0.92
    prior_maybe = prior_from_rate(0.50, prior_strength)
    prior_no_reply = prior_from_rate(0.20, prior_strength)

    # Accumulate training data
    tot_arr_yes = sum(e["yes"][0] for e in train_events)
    tot_inv_yes = sum(e["yes"][1] for e in train_events)
    post_yes = update_beta(prior_yes, tot_arr_yes, tot_inv_yes)

    tot_arr_maybe = sum(e["maybe"][0] for e in train_events)
    tot_inv_maybe = sum(e["maybe"][1] for e in train_events)
    post_maybe = update_beta(prior_maybe, tot_arr_maybe, tot_inv_maybe)

    tot_arr_no = sum(e["no_reply"][0] for e in train_events)
    tot_inv_no = sum(e["no_reply"][1] for e in train_events)
    post_no_reply = update_beta(prior_no_reply, tot_arr_no, tot_inv_no)

    assert abs(post_yes.mean - TRUE_TURNOUT_YES) < 0.03, (
        f"Yes mean {post_yes.mean:.3f} not within 0.03 of {TRUE_TURNOUT_YES}"
    )
    assert abs(post_maybe.mean - TRUE_TURNOUT_MAYBE) < 0.03, (
        f"Maybe mean {post_maybe.mean:.3f} not within 0.03 of {TRUE_TURNOUT_MAYBE}"
    )
    assert abs(post_no_reply.mean - TRUE_TURNOUT_NO_REPLY) < 0.03, (
        f"No-reply mean {post_no_reply.mean:.3f} not within 0.03 of {TRUE_TURNOUT_NO_REPLY}"
    )

    # -------------------------------------------------------------------------
    # CHECK 2: 80% intervals cover 70-90% of held-out events
    # CHECK 7: Bayesian model beats Phase-1 rules in pinball loss
    # -------------------------------------------------------------------------
    learned_rates = {"yes": post_yes, "maybe": post_maybe, "no_reply": post_no_reply}
    covered_80_count = 0
    bayesian_pinball_losses = []
    rules_pinball_losses = []

    for ev in held_out_events:
        actual = float(ev["total_arrived"])
        groups = [
            HouseholdGroup(status="yes", seats=1, households=ev["yes"][1]),
            HouseholdGroup(status="maybe", seats=1, households=ev["maybe"][1]),
            HouseholdGroup(status="no_reply", seats=1, households=ev["no_reply"][1]),
        ]
        sim_draws = [
            float(x) for x in simulate_arrivals(groups, learned_rates, draws=500, seed=ev["id"])
        ]
        p10 = quantile(sim_draws, 0.10)
        p50 = quantile(sim_draws, 0.50)
        p90 = quantile(sim_draws, 0.90)

        if p10 <= actual <= p90:
            covered_80_count += 1

        b_loss = (
            pinball_loss(actual, p10, 0.10)
            + pinball_loss(actual, p50, 0.50)
            + pinball_loss(actual, p90, 0.90)
        ) / 3.0
        bayesian_pinball_losses.append(b_loss)

        # Rule-based prediction
        seats = ReplySeats(yes=ev["yes"][1], maybe=ev["maybe"][1], no_reply=ev["no_reply"][1])
        r_pred = predict_arrivals(seats)
        r_p10 = float(r_pred.low)
        r_p50 = float(r_pred.likely)
        r_p90 = float(r_pred.high)
        r_loss = (
            pinball_loss(actual, r_p10, 0.10)
            + pinball_loss(actual, r_p50, 0.50)
            + pinball_loss(actual, r_p90, 0.90)
        ) / 3.0
        rules_pinball_losses.append(r_loss)

    coverage_rate = covered_80_count / len(held_out_events)
    assert 0.70 <= coverage_rate <= 0.90, f"Coverage {coverage_rate:.2f} not between 70% and 90%"

    avg_b_loss = sum(bayesian_pinball_losses) / len(bayesian_pinball_losses)
    avg_r_loss = sum(rules_pinball_losses) / len(rules_pinball_losses)
    assert avg_b_loss < avg_r_loss, (
        f"Bayesian loss {avg_b_loss:.3f} did not beat rules loss {avg_r_loss:.3f}"
    )

    # -------------------------------------------------------------------------
    # CHECK 3: Portion median within 10% of truth
    # -------------------------------------------------------------------------
    portion_obs_list = [e["portion_obs"] for e in train_events]
    post_portion = lognormal_posterior(
        prior_median=250.0,
        prior_log_sd=0.35,
        obs_log_sd=0.20,
        observations=portion_obs_list,
    )
    portion_error = abs(post_portion.median - TRUE_PORTION_MEDIAN) / TRUE_PORTION_MEDIAN
    assert portion_error < 0.10, (
        f"Portion median {post_portion.median:.1f} not within 10% of {TRUE_PORTION_MEDIAN}"
    )

    # -------------------------------------------------------------------------
    # CHECK 4: Alpha within 0.05 of truth
    # -------------------------------------------------------------------------
    elasticity_obs_list = [e["elasticity_obs"] for e in train_events]
    post_elasticity = learn_elasticity(
        prior_mean=0.65,
        prior_sd=0.15,
        obs_sd=0.10,
        observations=elasticity_obs_list,
    )
    alpha_error = abs(post_elasticity.mean - TRUE_ALPHA)
    assert alpha_error < 0.05, (
        f"Alpha mean {post_elasticity.mean:.3f} not within 0.05 of {TRUE_ALPHA}"
    )

    # -------------------------------------------------------------------------
    # CHECK 5: Thompson sampling cumulative regret in 2nd half below 1st half
    # -------------------------------------------------------------------------
    arms = list(TRUE_SHOP_RATES.keys())
    opt_rate = max(TRUE_SHOP_RATES.values())  # 0.75 for zepto
    context_stats: dict[str, ArmStats] = {a: ArmStats(0, 0) for a in arms}

    regrets_first_half: list[float] = []
    regrets_second_half: list[float] = []
    n_bandit_trials = 300

    for t in range(n_bandit_trials):
        ranked = rank_arms(arms, context_stats, {}, strength=4.0, rng=rng)
        picked = ranked[0]
        # Realize success/failure
        succ = 1 if rng.random() < TRUE_SHOP_RATES[picked] else 0
        curr = context_stats[picked]
        context_stats[picked] = ArmStats(curr.successes + succ, curr.failures + (1 - succ))

        regret = opt_rate - TRUE_SHOP_RATES[picked]
        if t < n_bandit_trials // 2:
            regrets_first_half.append(regret)
        else:
            regrets_second_half.append(regret)

    cum_regret_1 = sum(regrets_first_half)
    cum_regret_2 = sum(regrets_second_half)
    assert cum_regret_2 < cum_regret_1, (
        f"Second half regret ({cum_regret_2:.2f}) not less than first half ({cum_regret_1:.2f})"
    )

    # -------------------------------------------------------------------------
    # CHECK 6: Bradley-Terry recovers true order with Kendall tau >= 0.8
    # -------------------------------------------------------------------------
    comparisons = []
    # Generate pairwise comparisons consistent with TRUE_PREF_ORDER:
    # food > beverage > tableware > decor
    for _ in range(120):
        # Pick two distinct categories
        c1, c2 = rng.sample(TRUE_PREF_ORDER, 2)
        idx1 = TRUE_PREF_ORDER.index(c1)
        idx2 = TRUE_PREF_ORDER.index(c2)
        # Winner is with 85% probability the higher ranked item (smaller index)
        if rng.random() < 0.85:
            winner = c1 if idx1 < idx2 else c2
            loser = c2 if idx1 < idx2 else c1
        else:
            winner = c2 if idx1 < idx2 else c1
            loser = c1 if idx1 < idx2 else c2
        comparisons.append((winner, loser))

    bt_scores = bradley_terry(items=TRUE_PREF_ORDER, comparisons=comparisons)
    recovered_order = sorted(TRUE_PREF_ORDER, key=lambda c: bt_scores[c], reverse=True)
    tau = compute_kendall_tau(TRUE_PREF_ORDER, recovered_order)
    assert tau >= 0.8, f"Kendall tau {tau:.2f} was below 0.8. Recovered: {recovered_order}"
