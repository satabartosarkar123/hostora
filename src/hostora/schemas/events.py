from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, Field, StringConstraints

from hostora.schemas.common import ApiModel, EventType, GateMode, Language, Meal, RuleId


class FunctionInput(ApiModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    starts_at: AwareDatetime
    meal: Meal
    rules: list[RuleId] = Field(default_factory=list)
    gate_mode: GateMode = GateMode.OFF


class Venue(ApiModel):
    label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    lat: Annotated[float, Field(ge=-90, le=90)]
    lng: Annotated[float, Field(ge=-180, le=180)]
    pincode: Annotated[str, StringConstraints(pattern=r"^\d{6}$")] | None = None


class CreateEventInput(ApiModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    type: EventType
    tradition: Annotated[str, StringConstraints(max_length=40)] = "none"
    venue: Venue
    budget_inr: Annotated[int, Field(ge=0, le=100_000_000)]
    functions: Annotated[list[FunctionInput], Field(min_length=1, max_length=8)]


class FunctionRef(ApiModel):
    id: str
    name: str


class CreatedEvent(ApiModel):
    event_key: str
    functions: list[FunctionRef]


class HouseholdInput(ApiModel):
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    phone: Annotated[str, StringConstraints(pattern=r"^\+?\d{10,15}$")] | None = None
    seat_cap: Annotated[int, Field(ge=1, le=20)]
    function_ids: Annotated[list[str], Field(min_length=1)]
    language: Language = Language.EN


class HouseholdBatch(ApiModel):
    households: Annotated[list[HouseholdInput], Field(min_length=1, max_length=500)]


class PreparedInvite(ApiModel):
    household_id: str
    display_name: str
    link: str
    code: str
    whatsapp_url: str
