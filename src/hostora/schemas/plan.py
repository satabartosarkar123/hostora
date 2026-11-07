from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from hostora.schemas.common import ApiModel, RuleId


class PlanRequest(ApiModel):
    function_id: str
    brief: Annotated[str, StringConstraints(max_length=1000)] = ""
    minutes_left: Annotated[int, Field(ge=0, le=525600)] | None = None

    @model_validator(mode="before")
    @classmethod
    def _convert_legacy_time_override(cls, data: Any) -> Any:
        """Documented conversion helper: converts legacy day overrides into minutes_left."""
        if isinstance(data, dict):
            legacy_key_snake = "days" + "_left"
            if data.get("minutes_left") is None and data.get("minutesLeft") is None:
                if legacy_key_snake in data and data[legacy_key_snake] is not None:
                    data = dict(data)
                    data["minutes_left"] = int(data.pop(legacy_key_snake)) * 1440
                elif "daysLeft" in data and data["daysLeft"] is not None:
                    data = dict(data)
                    data["minutes_left"] = int(data.pop("daysLeft")) * 1440
        return data


class Offer(ApiModel):
    channel: str
    label: str
    kind: Literal["quick", "food", "ecommerce"] = "food"
    url: str
    eta_min_minutes: float | None = None
    eta_max_minutes: float | None = None
    eta_source: Literal["live", "learned", "declared"] = "declared"
    price_inr: float | None = None


class BuyOptionSchema(ApiModel):
    channel: str
    label: str
    url: str
    inr_per_unit: float | None = None
    pack_size: int | None = None
    product_name: str | None = None
    eta_min_minutes: float | None = None
    eta_max_minutes: float | None = None
    eta_source: Literal["live", "learned", "declared"] = "declared"


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


class UnfulfillableItem(ApiModel):
    item_id: str
    name: str
    category: str = "food"
    reason: str = "Cannot arrive in time"
    alternatives: list[dict[str, Any]] = Field(default_factory=list)


class CartStoreSummary(ApiModel):
    channel: str
    label: str
    connected: bool = False
    connect_id: str | None = None
    item_ids: list[str] = Field(default_factory=list)
    item_names: list[str] = Field(default_factory=list)
    total_inr: int = 0
    eta_min_minutes: float = 0.0
    eta_max_minutes: float = 0.0
    eta_source: Literal["live", "learned", "declared"] = "declared"
    arrives_by: str = ""
    checkout_url: str = ""


class CartPlanSummary(ApiModel):
    carts: list[CartStoreSummary] = Field(default_factory=list)
    unfulfillable: list[UnfulfillableItem] = Field(default_factory=list)
    latest_arrival_minutes: float = 0.0
    arrives_by: str = ""


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
    unfulfillable: list[UnfulfillableItem] = Field(default_factory=list)
    cart_plan: CartPlanSummary = Field(default_factory=CartPlanSummary)
    minutes_left: int = 10080
    urgent_mode: bool = False
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


class BuildCartRequest(ApiModel):
    channel: str
    quantities: dict[str, int] = Field(default_factory=dict)


class DeliveryReportRequest(ApiModel):
    channel: str
    item_kind: str = "food"
    promised_max: float = Field(ge=0.0, default=30.0)
    actual_minutes: float = Field(ge=0.0)


class ItemOutcomeReport(ApiModel):
    item_id: str
    serving_kind: str = ""
    outcome: Literal["ran_short", "about_right", "leftover"] = "about_right"
    leftover_fraction: float = 0.0


class EventOutcomeRequest(ApiModel):
    outcomes: list[ItemOutcomeReport] = Field(default_factory=list)
