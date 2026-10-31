"""Dietary and faith rule filtering.

Closed world: food and drink must be tagged `declared` plus one `diet:*` tag, or
they are removed. Implied tags (chicken -> meat, onion -> root_veg, wine ->
alcohol, ...) are expanded before filtering, so a data-entry slip cannot leak a
forbidden item into a plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

Unit = Literal["g", "ml", "piece"]
Category = Literal["food", "beverage", "decor", "lighting", "tableware", "supply"]
Course = Literal["starter", "main", "rice", "bread", "side", "dessert", "drink", "supply", "decor"]

CONSUMABLE: frozenset[str] = frozenset({"food", "beverage"})


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: str
    name: str
    category: Category
    course: Course
    unit: Unit
    pack_sizes: tuple[int, ...]
    tags: tuple[str, ...]
    est_price_per_unit_inr: float
    search_query: str
    popularity: int = 0


@dataclass(frozen=True, slots=True)
class RuleSet:
    id: str
    label: str
    forbid: tuple[str, ...]


IMPLIED_TAGS: Mapping[str, tuple[str, ...]] = {
    "beef": ("meat",),
    "pork": ("meat",),
    "mutton": ("meat",),
    "chicken": ("meat",),
    "prawn": ("fish",),
    "onion": ("root_veg",),
    "garlic": ("root_veg",),
    "potato": ("root_veg",),
    "carrot": ("root_veg",),
    "beer": ("alcohol",),
    "wine": ("alcohol",),
}


@dataclass(slots=True)
class FilterOutcome:
    allowed: list[CatalogItem] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)  # (item_id, reason)
    allergen_warnings: list[tuple[str, str]] = field(default_factory=list)  # (item_id, allergen)


def expand_tags(tags: Iterable[str]) -> set[str]:
    out = set(tags)
    queue = list(out)
    while queue:
        tag = queue.pop()
        for implied in IMPLIED_TAGS.get(tag, ()):
            if implied not in out:
                out.add(implied)
                queue.append(implied)
    return out


def is_declared(tags: set[str]) -> bool:
    return "declared" in tags and any(t.startswith("diet:") for t in tags)


def apply_rules(
    items: Iterable[CatalogItem],
    rule_ids: Sequence[str],
    rules: Mapping[str, RuleSet],
    allergies: Sequence[str] = (),
) -> FilterOutcome:
    forbidden: set[str] = set()
    for rule_id in rule_ids:
        rule = rules.get(rule_id)
        if rule is None:
            raise KeyError(f"Unknown rule set: {rule_id}")
        forbidden.update(rule.forbid)

    result = FilterOutcome()
    for item in items:
        tags = expand_tags(item.tags)
        if item.category in CONSUMABLE and not is_declared(tags):
            result.removed.append((item.id, "undeclared"))
            continue
        hit = next((t for t in sorted(tags) if t in forbidden), None)
        if hit is not None:
            result.removed.append((item.id, f"forbidden:{hit}"))
            continue
        for allergen in allergies:
            if f"allergen:{allergen}" in tags:
                result.allergen_warnings.append((item.id, allergen))
        result.allowed.append(item)
    return result
