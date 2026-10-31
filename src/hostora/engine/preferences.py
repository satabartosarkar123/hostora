"""What to keep when the budget is tight: learned preferences and an exact budget optimiser.

* ``bradley_terry`` turns pairwise choices ("kept the photo booth, dropped the second dessert",
  or the host's own drag-to-rank) into preference scores (Bradley & Terry 1952; fitted with the
  MM algorithm of Hunter 2004, with one virtual win and loss per item for stability).
* ``optimize_budget`` picks one variant per plan line (premium / standard / cheaper / drop) to
  maximise total utility within the budget: a multiple-choice knapsack solved exactly by dynamic
  programming over cost in rupee steps. This replaces fixed drop orders.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def bradley_terry(
    items: Sequence[str], comparisons: Sequence[tuple[str, str]], iterations: int = 500
) -> dict[str, float]:
    """Scores summing to 1 from (winner, loser) pairs. Items never compared get equal shares."""
    names = list(dict.fromkeys(items))
    if not names:
        return {}
    known = set(names)
    wins = dict.fromkeys(names, 1.0)  # one virtual win each
    pair_counts: dict[tuple[str, str], float] = {}
    for w, loser in comparisons:
        if w not in known or loser not in known or w == loser:
            raise ValueError(f"bad comparison ({w!r}, {loser!r})")
        wins[w] += 1
        key = (min(w, loser), max(w, loser))
        pair_counts[key] = pair_counts.get(key, 0.0) + 1
    virtual = "\0virtual"
    for name in names:  # one virtual game against a reference player of strength 1
        pair_counts[(name, virtual)] = pair_counts.get((name, virtual), 0.0) + 2
    score = dict.fromkeys(names, 1.0)
    score[virtual] = 1.0
    for _ in range(iterations):
        new: dict[str, float] = {}
        for i in names:
            denom = 0.0
            for (x, y), n in pair_counts.items():
                if i in (x, y):
                    j = y if x == i else x
                    denom += n / (score[i] + score[j])
            new[i] = wins[i] / denom
        total = sum(new.values())
        delta = max(abs(new[i] * len(names) / total - score[i]) for i in names)
        for i in names:
            score[i] = new[i] * len(names) / total
        if delta < 1e-10:
            break
    total = sum(score[i] for i in names)
    return {i: score[i] / total for i in names}


@dataclass(frozen=True, slots=True)
class Variant:
    label: str  # e.g. "premium", "standard", "drop"
    cost_inr: float
    utility: float


@dataclass(frozen=True, slots=True)
class BudgetChoice:
    picks: tuple[int, ...]  # chosen variant index per group
    cost_inr: float
    utility: float


def optimize_budget(
    groups: Sequence[Sequence[Variant]], budget_inr: float, step_inr: float = 100
) -> BudgetChoice | None:
    """Exact multiple-choice knapsack. Every group must pick one variant (include a zero-cost
    "drop" variant for optional lines). Costs are rounded UP to ``step_inr`` for the search, so
    the true total never exceeds the budget. Returns None if even the cheapest picks overflow."""
    if budget_inr < 0 or step_inr <= 0:
        raise ValueError("budget must be >= 0 and step > 0")
    if any(not g for g in groups):
        raise ValueError("every group needs at least one variant")
    cap = int(budget_inr // step_inr)
    neg = -math.inf
    best: list[float] = [0.0] + [neg] * cap  # best utility at exactly c units
    choice: list[list[int]] = []
    for g in groups:
        units = [math.ceil(v.cost_inr / step_inr - 1e-9) for v in g]
        nxt = [neg] * (cap + 1)
        pick = [-1] * (cap + 1)
        for c in range(cap + 1):
            if best[c] == neg:
                continue
            for k, (v, u) in enumerate(zip(g, units, strict=True)):
                nc = c + u
                if nc <= cap and best[c] + v.utility > nxt[nc]:
                    nxt[nc] = best[c] + v.utility
                    pick[nc] = k
        best = nxt
        choice.append(pick)
    end = max(range(cap + 1), key=lambda c: (best[c], -c))
    if best[end] == neg:
        return None
    picks: list[int] = []
    c = end
    for g, pick in zip(reversed(groups), reversed(choice), strict=True):
        k = pick[c]
        picks.append(k)
        c -= math.ceil(g[k].cost_inr / step_inr - 1e-9)
    picks.reverse()
    cost = sum(g[k].cost_inr for g, k in zip(groups, picks, strict=True))
    util = sum(g[k].utility for g, k in zip(groups, picks, strict=True))
    return BudgetChoice(picks=tuple(picks), cost_inr=cost, utility=util)
