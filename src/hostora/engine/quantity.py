"""Per-head quantities and rounding to real pack sizes.

Q = (adults * per_adult + children * per_child) * k^-alpha * (1 + buffer)

k is the number of dishes in the course: each dish's share shrinks as dishes are
added (alpha < 1 means more dishes -> slightly more food in total). The buffer
comes from the gap between the P80 and P50 headcount, capped at 10%.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import reduce
from typing import Literal

Meal = Literal["lunch", "dinner", "snacks"]

DEFAULT_ALPHA = 0.65
MAX_BUFFER = 0.10
_MAX_STATES = 200_000


@dataclass(frozen=True, slots=True)
class ConsumptionRow:
    item_id: str
    meal: Meal
    per_adult: float  # g, ml or pieces, in the item's unit
    per_child: float
    source: str


@dataclass(frozen=True, slots=True)
class Headcount:
    adults: int
    children: int


@dataclass(frozen=True, slots=True)
class PackPlan:
    packs: tuple[tuple[int, int], ...]  # (size, count), largest size first
    total: int


def buffer_from_prediction(p50: float, p80: float) -> float:
    if p50 <= 0:
        return 0.0
    return min(max((p80 - p50) / p50, 0.0), MAX_BUFFER)


def raw_quantity(
    headcount: Headcount,
    row: ConsumptionRow,
    dishes_in_course: int,
    alpha: float = DEFAULT_ALPHA,
    buffer: float = 0.0,
) -> float:
    if headcount.adults < 0 or headcount.children < 0:
        raise ValueError("headcount must be non-negative")
    base = headcount.adults * row.per_adult + headcount.children * row.per_child
    share: float = float(max(1, dishes_in_course)) ** -alpha
    return float(base * share * (1 + min(max(buffer, 0.0), MAX_BUFFER)))


def round_to_packs(qty: float, pack_sizes: Sequence[int]) -> PackPlan:
    """Smallest total >= qty buildable from pack_sizes; ties broken by fewer packs.

    Unbounded knapsack over units of gcd(pack_sizes).
    """
    sizes = sorted({round(s) for s in pack_sizes if round(s) > 0})
    if not sizes:
        raise ValueError("pack_sizes must contain a positive size")
    if qty <= 0:
        return PackPlan(packs=(), total=0)

    g = reduce(math.gcd, sizes)
    units = [s // g for s in sizes]
    target = math.ceil(qty / g)
    limit = target + max(units)

    if limit > _MAX_STATES:
        big = sizes[-1]
        count = math.ceil(qty / big)
        return PackPlan(packs=((big, count),), total=count * big)

    inf = math.inf
    fewest: list[float] = [inf] * (limit + 1)
    last: list[int] = [-1] * (limit + 1)
    fewest[0] = 0
    for v in range(1, limit + 1):
        for i, u in enumerate(units):
            if u <= v and fewest[v - u] + 1 < fewest[v]:
                fewest[v] = fewest[v - u] + 1
                last[v] = i

    best = next(v for v in range(target, limit + 1) if fewest[v] < inf)
    counts: Counter[int] = Counter()
    v = best
    while v > 0:
        i = last[v]
        counts[sizes[i]] += 1
        v -= units[i]
    packs = tuple(sorted(counts.items(), key=lambda kv: -kv[0]))
    return PackPlan(packs=packs, total=best * g)
