"""How many plates to order: a newsvendor decision on the predicted arrival distribution.

Instead of a fixed percentile, the host states how bad running short is compared with waste
(for example "running out is 4 times worse than a wasted plate"). The cost-minimising order is
the critical-ratio quantile of predicted arrivals:

    q* = cost_short / (cost_short + cost_waste)

(the classical newsvendor result; see any operations-research text, e.g. Porteus 2002).
"""

from __future__ import annotations

from collections.abc import Sequence

from hostora.engine.turnout import quantile


def critical_ratio(cost_short: float, cost_waste: float) -> float:
    if cost_short <= 0 or cost_waste <= 0:
        raise ValueError("costs must be positive")
    return cost_short / (cost_short + cost_waste)


def order_quantity(samples: Sequence[float], cost_short: float, cost_waste: float) -> int:
    """Plates to order: the critical-ratio quantile of predicted arrivals, rounded up."""
    return int(-(-quantile(samples, critical_ratio(cost_short, cost_waste)) // 1))


def expected_cost(
    samples: Sequence[float], order: float, cost_short: float, cost_waste: float
) -> float:
    """Average cost of ordering ``order`` plates over the predictive samples."""
    if not samples:
        raise ValueError("no samples")
    total = 0.0
    for d in samples:
        total += cost_short * max(0.0, d - order) + cost_waste * max(0.0, order - d)
    return total / len(samples)
