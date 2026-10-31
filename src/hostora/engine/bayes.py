"""Bayesian estimation from messy, censored real-world feedback.

Hosts rarely know exactly how much food was eaten. They know "we ran out" (demand was at least
what we had), "about a third was left" (demand was roughly two thirds of it) or "it was just
right" (demand was in a narrow band). This module estimates a positive quantity, such as the
grams per head of a serving kind, from exactly that kind of evidence: exact, lower-bounded
(right-censored), upper-bounded and interval observations, under log-normal noise, with a
grid posterior (no fudge factors such as "ran short means +15%").

It also learns the dish-count elasticity used by ``quantity.raw_quantity``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

ObsKind = Literal["exact", "at_least", "at_most", "between"]


@dataclass(frozen=True, slots=True)
class Obs:
    kind: ObsKind
    low: float  # exact value, or the bound (between: lower bound)
    high: float | None = None  # between: upper bound

    def __post_init__(self) -> None:
        if self.low <= 0:
            raise ValueError("observations must be positive")
        if self.kind == "between" and (self.high is None or self.high <= self.low):
            raise ValueError("between needs high > low")


@dataclass(frozen=True, slots=True)
class Posterior:
    median: float
    mean: float
    p10: float
    p90: float
    n: int


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _log_lik(mu: float, sd: float, o: Obs) -> float:
    lo = (math.log(o.low) - mu) / sd
    if o.kind == "exact":
        return -0.5 * lo * lo
    if o.kind == "at_least":
        return math.log(max(1e-300, 1 - _phi(lo)))
    if o.kind == "at_most":
        return math.log(max(1e-300, _phi(lo)))
    assert o.high is not None
    hi = (math.log(o.high) - mu) / sd
    return math.log(max(1e-300, _phi(hi) - _phi(lo)))


def lognormal_posterior(
    prior_median: float,
    prior_log_sd: float,
    obs_log_sd: float,
    observations: Sequence[Obs],
    grid: int = 801,
    width: float = 4.0,
) -> Posterior:
    """Posterior over the typical value (median) of a log-normal quantity.

    The grid spans the prior median x exp(+/- width * prior_log_sd), which is also the hard
    physical bound on how far data can move the estimate."""
    if prior_median <= 0 or prior_log_sd <= 0 or obs_log_sd <= 0:
        raise ValueError("prior_median and standard deviations must be positive")
    if grid < 3:
        raise ValueError("grid too small")
    m0 = math.log(prior_median)
    mus = [m0 + width * prior_log_sd * (2 * i / (grid - 1) - 1) for i in range(grid)]
    logp = []
    for mu in mus:
        lp = -0.5 * ((mu - m0) / prior_log_sd) ** 2
        lp += sum(_log_lik(mu, obs_log_sd, o) for o in observations)
        logp.append(lp)
    top = max(logp)
    weights = [math.exp(v - top) for v in logp]
    z = sum(weights)
    probs = [w / z for w in weights]

    def q(p: float) -> float:
        acc = 0.0
        for mu, pr in zip(mus, probs, strict=True):
            acc += pr
            if acc >= p:
                return math.exp(mu)
        return math.exp(mus[-1])

    mean = sum(math.exp(mu) * pr for mu, pr in zip(mus, probs, strict=True))
    return Posterior(median=q(0.5), mean=mean, p10=q(0.1), p90=q(0.9), n=len(observations))


def outcome_to_obs(
    planned_per_head: float,
    outcome: str,
    leftover_fraction: float = 0.0,
    right_tolerance: float = 0.1,
) -> Obs:
    """Translate a host's report into an observation of per-head demand.

    ``ran_short``: demand was at least what was planned. ``leftover``: demand was planned x
    (1 - fraction). ``about_right``: demand was within ``right_tolerance`` below the plan."""
    if planned_per_head <= 0:
        raise ValueError("planned_per_head must be positive")
    if outcome == "ran_short":
        return Obs("at_least", planned_per_head)
    if outcome == "leftover":
        if not 0 <= leftover_fraction < 1:
            raise ValueError("leftover_fraction must be in [0, 1)")
        return Obs("exact", planned_per_head * (1 - leftover_fraction))
    if outcome == "about_right":
        if not 0 < right_tolerance < 1:
            raise ValueError("right_tolerance must be in (0, 1)")
        return Obs("between", planned_per_head * (1 - right_tolerance), planned_per_head)
    raise ValueError(f"unknown outcome {outcome!r}")


@dataclass(frozen=True, slots=True)
class Elasticity:
    mean: float
    sd: float
    n: int


def learn_elasticity(
    prior_mean: float,
    prior_sd: float,
    obs_sd: float,
    observations: Sequence[tuple[int, float]],
) -> Elasticity:
    """Normal-normal update of the dish-count elasticity alpha.

    Each observation is (dishes in the course k >= 2, ratio = observed total per head across
    the course / single-dish portion). Under total = portion x k^(1 - alpha), each gives
    alpha_obs = 1 - ln(ratio) / ln(k). Alpha is physically limited to [0, 1]: adding dishes
    never lowers total consumption, and never raises it proportionally or more."""
    if prior_sd <= 0 or obs_sd <= 0:
        raise ValueError("standard deviations must be positive")
    precision = 1 / prior_sd**2
    weighted = prior_mean * precision
    used = 0
    for k, ratio in observations:
        if k < 2 or ratio <= 0:
            continue
        a_obs = 1 - math.log(ratio) / math.log(k)
        w = 1 / obs_sd**2
        weighted += a_obs * w
        precision += w
        used += 1
    mean = min(max(weighted / precision, 0.0), 1.0)
    return Elasticity(mean=mean, sd=math.sqrt(1 / precision), n=used)
