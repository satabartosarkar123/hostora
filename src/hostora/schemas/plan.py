from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from hostora.schemas.common import ApiModel, RuleId


class PlanRequest(ApiModel):
    function_id: str
    brief: Annotated[str, StringConstraints(max_length=1000)] = ""
    days_left: Annotated[int, Field(ge=0, le=365)] | None = None


class Offer(ApiModel):
    channel: str
    label: str
    kind: Literal["quick", "food", "ecommerce"] = "food"
    url: str


class BuyOptionSchema(ApiModel):
    channel: str
    label: str
    url: str
    inr_per_unit: float | None = None
    pack_size: int | None = None
    product_name: str | None = None


class GroundingInfo(ApiModel):
    supporting: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)


class PriceInfo(ApiModel):
    source: str = "ai_estimate"
    low: float = 0.0
    mid: float = 0.0
    high: float = 0.0
    observed_on: str = ""
    detail: str = ""


class PackOut(ApiModel):
    size: int
    count: int


class PlanItem(ApiModel):
    item_id: str
    name: str
    category: str = "food"
    course: str = "main"
    unit: Literal["g", "ml", "piece"] = "g"
    needed: float = 0.0
    packs: list[PackOut] = Field(default_factory=list)
    buy_total: int = 0
    est_cost_inr: int = 0
    per_head_basis: str = ""
    allergen_warnings: list[str] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)
    status: Literal["allowed", "needs_confirmation"] = "allowed"
    serving_kind: str = ""
    why: str = ""
    grounding: GroundingInfo = Field(default_factory=GroundingInfo)
    price: PriceInfo = Field(default_factory=PriceInfo)
    options: list[BuyOptionSchema] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)


class RemovedItem(ApiModel):
    item_id: str
    name: str
    reason: str = ""
    reasons: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class PlanHeadcount(ApiModel):
    adults: int
    children: int
    veg_seats: int
    jain_seats: int
    non_veg_seats: int


class PlanBudget(ApiModel):
    limit_inr: int
    total_inr: int
    fits: bool
    changes: list[str]


class Plan(ApiModel):
    plan_id: str
    function_id: str
    headcount: PlanHeadcount
    rules: list[RuleId]
    items: list[PlanItem]
    needs_confirmation: list[PlanItem] = Field(default_factory=list)
    removed: list[RemovedItem] = Field(default_factory=list)
    budget: PlanBudget
    route: Literal["groq", "gemini", "template", "manual"] = "groq"
    explanation: dict[str, str] = Field(default_factory=dict)
    data_version: str = ""
    created_at: datetime


class ConfirmItemRequest(ApiModel):
    confirmations: list[str] = Field(default_factory=lambda: ["composition"])


class ReplaceItemRequest(ApiModel):
    alternative_index: int | None = None
    name: str | None = None


class ItemOutcomeReport(ApiModel):
    item_id: str
    serving_kind: str = ""
    outcome: Literal["ran_short", "about_right", "leftover"] = "about_right"
    leftover_fraction: float = 0.0


class EventOutcomeRequest(ApiModel):
    outcomes: list[ItemOutcomeReport] = Field(default_factory=list)
