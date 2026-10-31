from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from hostora.schemas.common import ApiModel, Attending, Language, RuleId


class Meals(ApiModel):
    veg: Annotated[int, Field(ge=0, le=20)] = 0
    jain: Annotated[int, Field(ge=0, le=20)] = 0
    non_veg: Annotated[int, Field(ge=0, le=20)] = 0


class RsvpFunction(ApiModel):
    function_id: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    attending: Attending
    adults: Annotated[int, Field(ge=0, le=20)] = 0
    children: Annotated[int, Field(ge=0, le=20)] = 0
    meals: Meals = Field(default_factory=Meals)
    dietary_rules: list[RuleId] = Field(default_factory=list)
    allergies: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=30)]],
        Field(default_factory=list, max_length=10),
    ]
    arrival_window: Annotated[str, StringConstraints(max_length=20)] | None = None


class RsvpInput(ApiModel):
    functions: Annotated[list[RsvpFunction], Field(min_length=1, max_length=8)]
    language: Language = Language.EN


class IssuedPass(ApiModel):
    function_id: str
    pass_id: str
    seats: int
    token: str


class RsvpResult(ApiModel):
    ok: bool
    functions: list[str]
    passes: list[IssuedPass]


class ResolveCodeInput(ApiModel):
    code: Annotated[str, StringConstraints(min_length=10, max_length=14)]
