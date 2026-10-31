"""Privacy audit test for learning tables (Milestone M5-L).

Verifies that no guest PII (names, phone numbers, free-form notes, or household IDs)
ever enters learning tables:
- turnout_observation
- turnout_model
- portion_model
- elasticity_model
- channel_stat
- preference_comparison
- function_pattern
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from hostora.db.models import (
    ChannelStat,
    ElasticityModel,
    Event,
    Fn,
    FunctionPattern,
    Host,
    Household,
    PortionModel,
    PreferenceComparison,
    TurnoutModel,
    TurnoutObservation,
)
from hostora.services.learning import (
    log_channel_purchase,
    record_portion_outcome,
    record_preference_comparison,
    record_turnout_observation,
    run_nightly_learning,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.db


async def test_learning_tables_never_contain_pii(db: AsyncSession) -> None:
    """Test that all learning tables are strictly aggregate and PII-free."""
    # 1. Setup Host, Event, and Function
    host = Host(uid="host_privacy_test", display_name="Privacy Host")
    db.add(host)
    await db.flush()

    event = Event(
        event_key="PRIV1",
        host_uid="host_privacy_test",
        title="Privacy Test Wedding",
        type="wedding",
        tradition="bengali",
        venue_label="Kolkata",
        venue_lat=22.57,
        venue_lng=88.36,
        budget_inr=100000,
        purge_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
    )
    db.add(event)
    await db.flush()

    fn = Fn(
        id="fn_priv_1",
        event_key="PRIV1",
        name="Reception Dinner",
        meal="dinner",
        starts_at=dt.datetime.now(dt.UTC),
        position=0,
    )
    db.add(fn)

    # 2. Add households with sensitive PII: names, phones, notes
    pii_names = ["Anirban Sharma", "Pooja Mukherjee", "Vikramaditya Roy"]
    pii_phones = ["9876543210", "9123456789", "9988776655"]
    pii_notes = ["Uncle has diabetes, need sugar-free desserts", "Strictly Jain, no onion garlic"]
    hh_ids = ["hh_priv_01", "hh_priv_02", "hh_priv_03"]

    for i, name in enumerate(pii_names):
        hh = Household(
            id=hh_ids[i],
            key=f"H000{i}",
            event_key="PRIV1",
            display_name=name,
            phone_enc="encrypted_phone_string",
            seat_cap=4,
        )
        db.add(hh)

    await db.flush()

    # 3. Record learning outcomes (turnout, portions, channels, preferences)
    await record_turnout_observation(
        db,
        function_id="fn_priv_1",
        state="wb",
        event_type="wedding",
        function_kind="dinner",
        reply_bucket="lead_3d",
        status="yes",
        invited_households=3,
        arrived_households=3,
    )

    await record_portion_outcome(
        db,
        function_id="fn_priv_1",
        serving_kind="dessert",
        region="wb",
        planned_per_head=100.0,
        outcome="about_right",
    )

    await log_channel_purchase(
        db,
        context_key="wb:dessert:0",
        channel="zepto",
    )

    await record_preference_comparison(
        db,
        event_key="PRIV1",
        winner="food",
        loser="decor",
        source="drag_rank",
    )

    pattern = FunctionPattern(
        traditions="bengali",
        region="wb",
        structure={"functions": [{"name": "Dinner", "meal": "dinner"}]},
        accepted_count=5,
    )
    db.add(pattern)
    await db.commit()

    # 4. Run nightly learning job to populate models
    await run_nightly_learning(db)

    # 5. Audit all 7 learning tables
    learning_tables = [
        TurnoutObservation,
        TurnoutModel,
        PortionModel,
        ElasticityModel,
        ChannelStat,
        PreferenceComparison,
        FunctionPattern,
    ]

    all_forbidden = (
        pii_names
        + pii_phones
        + pii_notes
        + ["diabetes", "sugar-free", "Anirban", "Mukherjee", "Roy"]
        + hh_ids
    )

    for model in learning_tables:
        rows = (await db.scalars(select(model))).all()
        for row in rows:
            for col in row.__table__.columns:
                val = getattr(row, col.name)
                val_str = str(val).lower()
                for secret in all_forbidden:
                    assert secret.lower() not in val_str, (
                        f"PII leak detected! Table {model.__tablename__}, column {col.name} "
                        f"contains forbidden value: {secret!r} in {val_str!r}"
                    )
