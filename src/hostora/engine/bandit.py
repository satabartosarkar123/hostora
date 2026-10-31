"""Which shops to show first: Thompson sampling that learns from what hosts actually buy.

Replaces fixed "days left -> shop kind" tables. For each context (for example state x item kind
x days-left band) and each shop, the app counts successes (the host marked the item bought from
that shop) and failures (the shop was shown in the top slots but not used). Each context's shop
rates shrink towards the shop's global rate, so new contexts start sensibly and then adapt.
Sampling (rather than always showing the current best) keeps exploring, so a newly added shop
or a shop that improved gets a fair chance (Thompson 1933; Russo et al., "A Tutorial on Thompson
Sampling", 2018).
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArmStats:
    successes: int = 0
    failures: int = 0

    def __post_init__(self) -> None:
        if self.successes < 0 or self.failures < 0:
            raise ValueError("counts must be non-negative")


def rank_arms(
    arms: Sequence[str],
    context_stats: Mapping[str, ArmStats],
    global_stats: Mapping[str, ArmStats],
    strength: float,
    rng: random.Random,
) -> list[str]:
    """Order ``arms`` by a Thompson sample from each arm's context posterior.

    Prior for an arm in this context: Beta centred on its global success rate (with add-one
    smoothing) with ``strength`` pseudo-counts. Unknown arms get a uniform-ish prior."""
    if strength <= 0:
        raise ValueError("strength must be positive")
    scored: list[tuple[float, str]] = []
    for arm in dict.fromkeys(arms):
        g = global_stats.get(arm, ArmStats())
        g_rate = (g.successes + 1) / (g.successes + g.failures + 2)
        c = context_stats.get(arm, ArmStats())
        a = g_rate * strength + c.successes
        b = (1 - g_rate) * strength + c.failures
        scored.append((rng.betavariate(a, b), arm))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [arm for _, arm in scored]


def days_band(days_left: float, edges: Sequence[float]) -> int:
    """Index of the band ``days_left`` falls in, given increasing ``edges`` (from config).
    Bands only group learning data; the ordering itself is learned."""
    for i, edge in enumerate(edges):
        if days_left < edge:
            return i
    return len(edges)
