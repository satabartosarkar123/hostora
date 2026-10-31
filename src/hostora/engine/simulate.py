"""Synthetic food-waste simulator comparing catering order strategies.

NOTE: All simulated events and turnout draws in this module are synthetic.
Assumptions:
- Each synthetic event has 30 to 150 invited households with seat caps 1 to 6.
- Households reply yes (~55%), maybe (~15%), no (~10%), or no-reply (~20%).
- True attendance rates are drawn per event from Beta distributions centered around
  the Phase-1 rates (yes ~ 95%, maybe ~ 68%, no-reply ~ 28%) with event-level variance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from hostora.engine.prediction import ReplySeats, predict_arrivals


@dataclass(frozen=True, slots=True)
class StrategyReport:
    name: str
    mean_over_ordered_pct: float
    shortage_prob_pct: float
    plates_saved_per_100_invited: float


@dataclass(frozen=True, slots=True)
class SimulationSummary:
    events: int
    seed: int
    strategies: tuple[StrategyReport, StrategyReport, StrategyReport]
    markdown_table: str


def run_waste_simulation(events: int = 10000, seed: int = 7) -> SimulationSummary:
    if events < 1:
        raise ValueError("events must be >= 1")
    rng = random.Random(seed)  # noqa: S311

    over_pct_sums = [0.0, 0.0, 0.0]
    shortages = [0, 0, 0]
    saved_per_100_sums = [0.0, 0.0, 0.0]

    for _ in range(events):
        num_households = rng.randint(30, 150)
        invited_seats = 0
        yes_seats = 0
        maybe_seats = 0
        no_reply_seats = 0

        p_yes = rng.uniform(0.45, 0.65)
        p_maybe = rng.uniform(0.10, 0.20)
        p_no = rng.uniform(0.08, 0.15)

        for _ in range(num_households):
            cap = rng.randint(1, 6)
            invited_seats += cap
            roll = rng.random()
            if roll < p_yes:
                yes_seats += rng.randint(max(1, cap - 1), cap)
            elif roll < p_yes + p_maybe:
                maybe_seats += rng.randint(1, cap)
            elif roll < p_yes + p_maybe + p_no:
                pass
            else:
                no_reply_seats += cap

        # Draw true attendance from Beta distributions around Phase-1 rates with noise
        true_yes_rate = rng.betavariate(38.0, 2.0)  # mean 0.95
        true_maybe_rate = rng.betavariate(13.6, 6.4)  # mean 0.68
        true_noreply_rate = rng.betavariate(5.6, 14.4)  # mean 0.28

        actual = round(
            yes_seats * true_yes_rate
            + maybe_seats * true_maybe_rate
            + no_reply_seats * true_noreply_rate
        )
        actual = max(1, actual)

        pred = predict_arrivals(
            ReplySeats(yes=yes_seats, maybe=maybe_seats, no_reply=no_reply_seats)
        )

        orders = (
            invited_seats,  # (a) plates for every invited seat
            yes_seats + maybe_seats,  # (b) plates for yes + maybe seats
            pred.p80,  # (c) Hostora P80
        )

        for i, ordered in enumerate(orders):
            over = max(0, ordered - actual)
            over_pct_sums[i] += (over / actual) * 100.0
            if ordered < actual:
                shortages[i] += 1
            saved_per_100_sums[i] += ((invited_seats - ordered) / invited_seats) * 100.0

    names = (
        "All invited seats (baseline)",
        "Yes + Maybe seats only",
        "Hostora P80 recommendation",
    )
    reports = tuple(
        StrategyReport(
            name=names[i],
            mean_over_ordered_pct=round(over_pct_sums[i] / events, 2),
            shortage_prob_pct=round((shortages[i] / events) * 100.0, 2),
            plates_saved_per_100_invited=round(saved_per_100_sums[i] / events, 2),
        )
        for i in range(3)
    )

    lines = [
        "**Synthetic Simulation Notice:** The results below are generated from synthetic "
        f"events (`events={events}`, `seed={seed}`) using Beta-distributed attendance around "
        "Phase-1 turnout rates (Yes mean 95%, Maybe mean 68%, No-reply mean 28%).",
        "",
        (
            "| Ordering Strategy | Mean Over-Ordered Plates (%) "
            "| Shortage Probability (%) | Plates Saved per 100 Invited |"
        ),
        "| --- | ---: | ---: | ---: |",
    ]
    for r in reports:
        lines.append(
            f"| {r.name} | {r.mean_over_ordered_pct:.2f}% | "
            f"{r.shortage_prob_pct:.2f}% | {r.plates_saved_per_100_invited:.2f} |"
        )
    table = "\n".join(lines)

    return SimulationSummary(
        events=events,
        seed=seed,
        strategies=(reports[0], reports[1], reports[2]),
        markdown_table=table,
    )
