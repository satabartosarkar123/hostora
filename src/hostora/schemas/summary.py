from __future__ import annotations

from datetime import datetime

from hostora.schemas.common import ApiModel, Meal


class SeatCounts(ApiModel):
    yes: int
    maybe: int
    no_households: int
    no_reply: int


class MealCounts(ApiModel):
    veg: int
    jain: int
    non_veg: int


class PredictionOut(ApiModel):
    low: int
    likely: int
    high: int
    p80: int


class FunctionSummary(ApiModel):
    function_id: str
    name: str
    meal: Meal
    invited_households: int
    replied_households: int
    seats: SeatCounts
    meals: MealCounts
    children: int
    prediction: PredictionOut
    recommended_plates: int
    arrivals: int
    possible_duplicates: int
    unknown_passes: int


class EventSummary(ApiModel):
    event_key: str
    title: str
    functions: list[FunctionSummary]
    updated_at: datetime
