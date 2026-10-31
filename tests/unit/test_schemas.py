from __future__ import annotations

import pytest
from pydantic import ValidationError

from hostora.schemas import (
    Attending,
    CreateEventInput,
    EventType,
    FunctionInput,
    GateMode,
    GateScan,
    GateScanBatch,
    HouseholdBatch,
    HouseholdInput,
    Language,
    Meal,
    Meals,
    PlanRequest,
    ResolveCodeInput,
    RsvpFunction,
    RsvpInput,
    RuleId,
    Venue,
)


def test_function_input_valid_and_invalid() -> None:
    fn = FunctionInput.model_validate(
        {
            "name": "Reception",
            "startsAt": "2026-12-10T19:00:00+05:30",
            "meal": "dinner",
            "rules": ["halal"],
            "gateMode": "soft",
        }
    )
    assert fn.name == "Reception"
    assert fn.rules == [RuleId.HALAL]
    assert fn.gate_mode is GateMode.SOFT
    assert fn.model_dump(by_alias=True)["startsAt"] is not None

    with pytest.raises(ValidationError):
        FunctionInput.model_validate(
            {
                "name": "",
                "startsAt": "2026-12-10T19:00:00+05:30",
                "meal": "dinner",
            }
        )

    with pytest.raises(ValidationError):
        FunctionInput.model_validate(
            {
                "name": "Naive datetime",
                "startsAt": "2026-12-10T19:00:00",
                "meal": "dinner",
            }
        )


def test_venue_and_create_event_input() -> None:
    v = Venue.model_validate({"label": "Hall", "lat": 28.6, "lng": 77.2, "pincode": "110001"})
    assert v.pincode == "110001"

    with pytest.raises(ValidationError):
        Venue.model_validate({"label": "Hall", "lat": 100.0, "lng": 77.2})

    with pytest.raises(ValidationError):
        Venue.model_validate({"label": "Hall", "lat": 28.6, "lng": 77.2, "pincode": "12345"})

    ev = CreateEventInput.model_validate(
        {
            "title": "Wedding",
            "type": "wedding",
            "venue": {"label": "Hall", "lat": 28.6, "lng": 77.2},
            "budgetInr": 60000,
            "functions": [
                {
                    "name": "Reception",
                    "startsAt": "2026-12-10T19:00:00+05:30",
                    "meal": "dinner",
                }
            ],
        }
    )
    assert ev.type is EventType.WEDDING
    assert ev.budget_inr == 60000
    dumped = ev.model_dump(by_alias=True)
    assert dumped["budgetInr"] == 60000

    with pytest.raises(ValidationError):
        CreateEventInput.model_validate(
            {
                "title": "Wedding",
                "type": "wedding",
                "venue": {"label": "Hall", "lat": 28.6, "lng": 77.2},
                "budgetInr": -1,
                "functions": [],
            }
        )

    with pytest.raises(ValidationError):
        CreateEventInput.model_validate(
            {
                "title": "Wedding",
                "type": "wedding",
                "venue": {"label": "Hall", "lat": 28.6, "lng": 77.2},
                "budgetInr": 1000,
                "functions": [
                    {
                        "name": "Reception",
                        "startsAt": "2026-12-10T19:00:00+05:30",
                        "meal": "dinner",
                    }
                ],
                "unknownExtra": 123,
            }
        )


def test_household_input_and_batch() -> None:
    hh = HouseholdInput.model_validate(
        {
            "displayName": "Sharma Family",
            "phone": "+919876543210",
            "seatCap": 4,
            "functionIds": ["fn_1"],
        }
    )
    assert hh.display_name == "Sharma Family"
    assert hh.language is Language.EN

    batch = HouseholdBatch.model_validate({"households": [hh.model_dump(by_alias=True)]})
    assert len(batch.households) == 1

    with pytest.raises(ValidationError):
        HouseholdInput.model_validate(
            {
                "displayName": "Bad Phone",
                "phone": "abc",
                "seatCap": 4,
                "functionIds": ["fn_1"],
            }
        )

    with pytest.raises(ValidationError):
        HouseholdInput.model_validate(
            {
                "displayName": "Zero Seats",
                "seatCap": 0,
                "functionIds": ["fn_1"],
            }
        )

    with pytest.raises(ValidationError):
        HouseholdBatch.model_validate({"households": []})


def test_rsvp_models_and_resolve_code() -> None:
    m = Meals.model_validate({"veg": 2, "jain": 1, "nonVeg": 1})
    assert m.non_veg == 1

    with pytest.raises(ValidationError):
        Meals.model_validate({"veg": -1})

    with pytest.raises(ValidationError):
        Meals.model_validate({"veg": 25})

    rf = RsvpFunction.model_validate(
        {
            "functionId": "fn_1",
            "attending": "yes",
            "adults": 3,
            "children": 1,
            "meals": {"veg": 2, "jain": 1, "nonVeg": 1},
            "dietaryRules": ["halal"],
            "allergies": ["dairy"],
        }
    )
    assert rf.attending is Attending.YES

    rsvp = RsvpInput.model_validate({"functions": [rf.model_dump(by_alias=True)]})
    assert len(rsvp.functions) == 1

    with pytest.raises(ValidationError):
        RsvpFunction.model_validate({"functionId": "", "attending": "yes"})

    with pytest.raises(ValidationError):
        RsvpInput.model_validate({"functions": []})

    rc = ResolveCodeInput.model_validate({"code": "DEMO1-ABCDE"})
    assert rc.code == "DEMO1-ABCDE"

    with pytest.raises(ValidationError):
        ResolveCodeInput.model_validate({"code": "SHORT"})

    with pytest.raises(ValidationError):
        ResolveCodeInput.model_validate({"code": "TOO-LONG-CODE-12345"})


def test_plan_request_and_gate_scan() -> None:
    pr = PlanRequest.model_validate({"functionId": "fn_1", "brief": "Halal dinner", "daysLeft": 5})
    assert pr.days_left == 5

    with pytest.raises(ValidationError):
        PlanRequest.model_validate({"functionId": "fn_1", "daysLeft": -1})

    with pytest.raises(ValidationError):
        PlanRequest.model_validate({"functionId": "fn_1", "daysLeft": 400})

    scan = GateScan.model_validate(
        {"deviceId": "dev_1234", "seq": 0, "passId": "p_1", "seats": 2, "at": 1700000000000}
    )
    batch = GateScanBatch.model_validate(
        {"functionId": "fn_1", "scans": [scan.model_dump(by_alias=True)]}
    )
    assert len(batch.scans) == 1

    with pytest.raises(ValidationError):
        GateScan.model_validate(
            {"deviceId": "ab", "seq": 0, "passId": "p_1", "seats": 2, "at": 1700000000000}
        )

    with pytest.raises(ValidationError):
        GateScanBatch.model_validate({"functionId": "fn_1", "scans": []})

    assert Meal.DINNER.value == "dinner"
