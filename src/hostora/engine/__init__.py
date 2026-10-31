"""Pure, dependency-light domain logic. No I/O, no framework imports.

Everything that decides a number or a safety outcome lives here, so it can be
tested exhaustively and reused by the API, the planner, jobs and notebooks.
"""

from hostora.engine.budget import BudgetResult, PlanLine, fit_budget, line_cost
from hostora.engine.codes import (
    ALPHABET,
    format_household_code,
    make_link_token,
    normalize_code,
    random_key,
    split_household_code,
    verify_link_token,
)
from hostora.engine.constraints import (
    IMPLIED_TAGS,
    CatalogItem,
    FilterOutcome,
    RuleSet,
    apply_rules,
    expand_tags,
    is_declared,
)
from hostora.engine.passes import (
    PassClaims,
    generate_key_pair_hex,
    pass_payload,
    sign_pass,
    verify_pass,
)
from hostora.engine.prediction import (
    HIGH,
    LIKELY,
    LOW,
    Prediction,
    Rates,
    ReplySeats,
    predict_arrivals,
)
from hostora.engine.quantity import (
    DEFAULT_ALPHA,
    MAX_BUFFER,
    ConsumptionRow,
    Headcount,
    PackPlan,
    buffer_from_prediction,
    raw_quantity,
    round_to_packs,
)

__all__ = [
    "ALPHABET",
    "DEFAULT_ALPHA",
    "HIGH",
    "IMPLIED_TAGS",
    "LIKELY",
    "LOW",
    "MAX_BUFFER",
    "BudgetResult",
    "CatalogItem",
    "ConsumptionRow",
    "FilterOutcome",
    "Headcount",
    "PackPlan",
    "PassClaims",
    "PlanLine",
    "Prediction",
    "Rates",
    "ReplySeats",
    "RuleSet",
    "apply_rules",
    "buffer_from_prediction",
    "expand_tags",
    "fit_budget",
    "format_household_code",
    "generate_key_pair_hex",
    "is_declared",
    "line_cost",
    "make_link_token",
    "normalize_code",
    "pass_payload",
    "predict_arrivals",
    "random_key",
    "raw_quantity",
    "round_to_packs",
    "sign_pass",
    "split_household_code",
    "verify_link_token",
    "verify_pass",
]
