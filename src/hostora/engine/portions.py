"""Portion sizes: grounded priors that learn from real outcomes.

The AI chooses dishes freely and labels each with a serving *kind* (for example a rice main,
a gravy main, a sweet served by the piece). Quantities never come from the model. For each
serving kind the application keeps a portion estimate that is:

1. initialised at runtime from retrieved, cited catering guidance (web-grounded extraction,
   cached with its sources; see AGENTS.md section 23), and
2. updated after every event from the host's outcome report (ran short, about right, or a
   leftover fraction), per region.

This module holds the pure maths for both, plus the conversion into the existing
``raw_quantity`` formula.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from hostora.engine.quantity import ConsumptionRow, Meal

Outcome = Literal["ran_short", "about_right", "leftover"]

RAN_SHORT_FACTOR = 1.15  # if it ran out, true demand was at least ~15% higher
MIN_RATIO, MAX_RATIO = 0.5, 2.0  # a learned portion never drifts beyond this vs its prior


@dataclass(frozen=True, slots=True)
class ServingKind:
    id: str
    course: str
    unit: str  # g | ml | piece
    per_adult: float
    per_child: float
    pack_sizes: tuple[int, ...]
    per_guests: int | None = None  # decor-style: one unit per N guests
    meal_factor: Mapping[str, float] | None = None
    basis: str = ""  # citations or "learned from N events"


@dataclass(frozen=True, slots=True)
class Observation:
    planned_per_head: float
    outcome: Outcome
    leftover_fraction: float = 0.0  # only for "leftover", 0..1


def consumption_row(kind: ServingKind, item_id: str, meal: Meal) -> ConsumptionRow:
    """A ConsumptionRow for ``raw_quantity``, scaled by the kind's meal factor."""
    factor = (kind.meal_factor or {}).get(meal, 1.0)
    return ConsumptionRow(
        item_id=item_id,
        meal=meal,
        per_adult=kind.per_adult * factor,
        per_child=kind.per_child * factor,
        source=kind.basis or "unattributed",
    )


def units_per_guests(kind: ServingKind, guests: int) -> int:
    """For per-guest-count kinds (decor, lighting): at least one unit."""
    if kind.per_guests is None or kind.per_guests <= 0:
        raise ValueError(f"serving kind {kind.id} is not counted per guests")
    return max(1, math.ceil(max(0, guests) / kind.per_guests))


def implied_demand(obs: Observation) -> float:
    """Per-head demand implied by one outcome report."""
    if obs.planned_per_head < 0:
        raise ValueError("planned_per_head must be non-negative")
    if obs.outcome == "ran_short":
        return obs.planned_per_head * RAN_SHORT_FACTOR
    if obs.outcome == "leftover":
        if not 0 <= obs.leftover_fraction <= 1:
            raise ValueError("leftover_fraction must be between 0 and 1")
        return obs.planned_per_head * (1 - obs.leftover_fraction)
    return obs.planned_per_head


def posterior_portion(
    prior: float, prior_weight: float, observations: Sequence[Observation]
) -> float:
    """Shrinkage estimate: the prior counts as ``prior_weight`` pseudo-events; each outcome
    report adds one. Clamped to [MIN_RATIO, MAX_RATIO] x prior so a few odd events cannot
    produce absurd quantities."""
    if prior <= 0 or prior_weight <= 0:
        raise ValueError("prior and prior_weight must be positive")
    total = prior * prior_weight + sum(implied_demand(o) for o in observations)
    estimate = total / (prior_weight + len(observations))
    return min(max(estimate, prior * MIN_RATIO), prior * MAX_RATIO)
