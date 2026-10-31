from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class RuleId(StrEnum):
    HALAL = "halal"
    JAIN = "jain"
    PURE_VEG = "pure_veg"
    NO_BEEF = "no_beef"
    NO_PORK = "no_pork"
    NO_ALCOHOL = "no_alcohol"
    EGGLESS = "eggless"
    VEGAN = "vegan"


class EventType(StrEnum):
    WEDDING = "wedding"
    BIRTHDAY = "birthday"
    ANNIVERSARY = "anniversary"
    HOUSEWARMING = "housewarming"
    FESTIVAL = "festival"
    RELIGIOUS = "religious"
    OFFICE = "office"
    COMMUNITY = "community"
    OTHER = "other"


class Meal(StrEnum):
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACKS = "snacks"


class GateMode(StrEnum):
    OFF = "off"
    SOFT = "soft"
    STRICT = "strict"


class Language(StrEnum):
    EN = "en"
    HI = "hi"


class Attending(StrEnum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"
