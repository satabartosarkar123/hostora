"""Fit a plan to a budget: biggest-saving swap first, then drop optional lines."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace


@dataclass(frozen=True, slots=True)
class Alternative:
    item_id: str
    unit_price_inr: float


@dataclass(frozen=True, slots=True)
class PlanLine:
    item_id: str
    qty: float
    unit_price_inr: float
    must_have: bool
    priority: int  # lower numbers are dropped first
    alternatives: tuple[Alternative, ...] = ()


@dataclass(slots=True)
class BudgetResult:
    lines: list[PlanLine]
    total_inr: float
    fits: bool
    changes: list[str] = field(default_factory=list)


def line_cost(line: PlanLine) -> float:
    return line.qty * line.unit_price_inr


def _total(lines: Sequence[PlanLine]) -> float:
    return sum(line_cost(line) for line in lines)


def fit_budget(lines_in: Sequence[PlanLine], budget_inr: float) -> BudgetResult:
    lines = list(lines_in)
    changes: list[str] = []

    while _total(lines) > budget_inr:
        best: tuple[float, int, int] | None = None  # (saving, line index, alternative index)
        for i, line in enumerate(lines):
            for j, alt in enumerate(line.alternatives):
                saving = (line.unit_price_inr - alt.unit_price_inr) * line.qty
                if saving > 0 and (best is None or saving > best[0]):
                    best = (saving, i, j)
        if best is None:
            break
        saving, i, j = best
        line, alt = lines[i], lines[i].alternatives[j]
        changes.append(f"swap {line.item_id} -> {alt.item_id} (saves ₹{round(saving)})")
        lines[i] = replace(
            line,
            item_id=alt.item_id,
            unit_price_inr=alt.unit_price_inr,
            alternatives=tuple(a for k, a in enumerate(line.alternatives) if k != j),
        )

    dropped: set[int] = set()
    optional = sorted(
        ((i, line) for i, line in enumerate(lines) if not line.must_have),
        key=lambda pair: pair[1].priority,
    )
    for i, line in optional:
        if _total([x for k, x in enumerate(lines) if k not in dropped]) <= budget_inr:
            break
        dropped.add(i)
        changes.append(f"drop {line.item_id}")

    kept = [x for k, x in enumerate(lines) if k not in dropped]
    total = _total(kept)
    return BudgetResult(lines=kept, total_inr=total, fits=total <= budget_inr, changes=changes)
