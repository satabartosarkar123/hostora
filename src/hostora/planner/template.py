from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hostora.schemas.plan import PlanHeadcount, PlanItem

# Default course structure and standard serving kinds (no dishes)
DEFAULT_COURSE_KINDS: list[tuple[str, str, str]] = [
    ("main", "rice_main", "Rice / Main Grain"),
    ("main", "main_gravy", "Curry / Main Gravy"),
    ("side", "main_dry", "Side Dish"),
    ("bread", "bread_piece", "Bread"),
    ("dessert", "sweet_piece", "Dessert / Sweet"),
    ("drink", "drink_cup", "Beverage / Welcome Drink"),
]


def build_template_explanations(
    headcount: PlanHeadcount,
    items: Sequence[PlanItem],
) -> dict[str, str]:
    counts_by_course: dict[str, int] = {}
    for item in items:
        counts_by_course[item.course] = counts_by_course.get(item.course, 0) + 1

    out: dict[str, str] = {}
    for course, count in counts_by_course.items():
        plural = "items" if count != 1 else "item"
        if course in ("supply", "decor"):
            out[course] = (
                f"Sized for {headcount.adults + headcount.children} total guests "
                f"across {count} {course} {plural}."
            )
        else:
            out[course] = (
                f"Sized for {headcount.adults} adults and {headcount.children} children "
                f"across {count} {plural}."
            )
    return out
