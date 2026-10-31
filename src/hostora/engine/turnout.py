"""Learned turnout: hierarchical Beta-Binomial attendance with Monte Carlo prediction.

Replaces fixed attendance rates. For each reply status (yes, maybe, no_reply) and each segment
(for example region x event type x reply-timing bucket), the share of invited households that
actually arrive is a Beta posterior:

* the global prior is weak (a few pseudo-households) and centred on cited published estimates;
* the global posterior is updated from every finished event's gate scans or reported counts;
* each segment shrinks towards the global posterior with a pooling strength estimated from the
  data (method of moments), so small segments borrow strength and large ones speak for themselves.

``simulate_arrivals`` draws rates from the posteriors (parameter uncertainty) and then household
arrivals (households come or not as a unit), giving a full predictive distribution. The plate
count is then a decision on that distribution (see ``engine.newsvendor``), not a fixed percentile.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MIN_STRENGTH, MAX_STRENGTH = 2.0, 1000.0


@dataclass(frozen=True, slots=True)
class BetaRate:
    a: float
    b: float

    def __post_init__(self) -> None:
        if self.a <= 0 or self.b <= 0:
            raise ValueError("Beta parameters must be positive")

    @property
    def mean(self) -> float:
        return self.a / (self.a + self.b)

    @property
    def strength(self) -> float:
        return self.a + self.b


def prior_from_rate(rate: float, strength: float) -> BetaRate:
    """A Beta prior with the given mean and pseudo-count strength."""
    if not 0 < rate < 1:
        raise ValueError("rate must be strictly between 0 and 1")
    if strength <= 0:
        raise ValueError("strength must be positive")
    return BetaRate(rate * strength, (1 - rate) * strength)


def update(rate: BetaRate, arrived: int, invited: int) -> BetaRate:
    """Conjugate update with ``arrived`` of ``invited`` households."""
    if not 0 <= arrived <= invited:
        raise ValueError("need 0 <= arrived <= invited")
    return BetaRate(rate.a + arrived, rate.b + invited - arrived)


def pooling_strength(segments: Sequence[tuple[int, int]]) -> float:
    """Method-of-moments estimate of how strongly segments should shrink to the pooled rate.

    ``segments``: (arrived, invited) per segment. With N = total invited, m = pooled rate and
    S = sum n_i (p_i - m)^2, the between-segment variance of true rates is estimated as
    tau^2 = (S - (k - 1) m (1 - m)) / (N - sum n_i^2 / N), and a Beta prior with that variance
    around m has strength m (1 - m) / tau^2 - 1. Large real differences between segments give a
    small strength (trust each segment); none give the maximum (pool heavily)."""
    usable = [(x, n) for x, n in segments if n > 0]
    k = len(usable)
    total = sum(n for _, n in usable)
    if k < 2 or total == 0:
        return MAX_STRENGTH
    m = sum(x for x, _ in usable) / total
    if m in (0.0, 1.0):
        return MAX_STRENGTH
    spread = sum(n * (x / n - m) ** 2 for x, n in usable)
    denom = total - sum(n * n for _, n in usable) / total
    tau2 = (spread - (k - 1) * m * (1 - m)) / denom if denom > 0 else 0.0
    if tau2 <= 0:
        return MAX_STRENGTH
    return min(max(m * (1 - m) / tau2 - 1, MIN_STRENGTH), MAX_STRENGTH)


def segment_rate(parent: BetaRate, arrived: int, invited: int, strength: float) -> BetaRate:
    """Segment posterior: a prior centred on the parent's mean with ``strength`` pseudo-counts,
    updated with the segment's own data."""
    return update(prior_from_rate(parent.mean, strength), arrived, invited)


@dataclass(frozen=True, slots=True)
class HouseholdGroup:
    status: str  # yes | maybe | no_reply
    seats: int  # seats each household in the group would bring
    households: int


def simulate_arrivals(
    groups: Sequence[HouseholdGroup],
    rates: Mapping[str, BetaRate],
    draws: int = 4000,
    seed: int = 0,
) -> list[int]:
    """Predictive samples of total arriving guests. Deterministic for a given seed."""
    if draws <= 0:
        raise ValueError("draws must be positive")
    rng = random.Random(seed)  # noqa: S311  # simulation, not security
    for g in groups:
        if g.seats < 0 or g.households < 0:
            raise ValueError("seats and households must be non-negative")
        if g.status not in rates:
            raise KeyError(f"no rate for status {g.status!r}")
    out: list[int] = []
    for _ in range(draws):
        p = {s: rng.betavariate(r.a, r.b) for s, r in rates.items()}
        total = 0
        for g in groups:
            if g.households and g.seats:
                total += g.seats * rng.binomialvariate(g.households, p[g.status])
        out.append(total)
    return out


def quantile(samples: Sequence[float], q: float) -> float:
    """Nearest-rank quantile (q in [0, 1])."""
    if not samples:
        raise ValueError("no samples")
    if not 0 <= q <= 1:
        raise ValueError("q must be in [0, 1]")
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


def pinball_loss(actual: float, predicted: float, q: float) -> float:
    """Quantile (pinball) loss, for comparing turnout models in backtests."""
    diff = actual - predicted
    return max(q * diff, (q - 1) * diff)
