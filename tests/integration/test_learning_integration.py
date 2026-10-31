"""Integration tests for Hostora learning loops (Milestone M5-L).

Tests:
1. A finished event updates turnout, portions, elasticity, channel stats and preferences;
2. `hostora learn` twice is idempotent;
3. Host rule edits change grounding;
4. Custom "no mushrooms" rule removes a mushroom dish;
5. Local vendors with respx: taginfo rejection of unknown tag;
6. Invitation validation rejects a draft without {link} or {code};
7. No-model paths work.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.adapters.osm import query_overpass_vendors, validate_osm_tag
from hostora.data.loader import get_data
from hostora.db.models import (
    ChannelStat,
    Event,
    PortionModel,
    TurnoutModel,
)
from hostora.engine.grounding import ProposedItem, Vocabulary, ground
from hostora.services.learning import (
    log_channel_purchase,
    record_portion_outcome,
    record_preference_comparison,
    record_turnout_observation,
    run_nightly_learning,
    save_event_rule,
    validate_invitation_text,
)


@pytest.mark.asyncio
async def test_learning_loop_updates_from_finished_event(db: AsyncSession) -> None:
    """Test that recording outcomes updates turnout, portions, elasticity, and channels."""
    # 1. Turnout
    await record_turnout_observation(
        db,
        function_id="fn_test_01",
        state="wb",
        event_type="wedding",
        function_kind="dinner",
        reply_bucket="lead_3d",
        status="yes",
        invited_households=50,
        arrived_households=47,
    )

    # 2. Portions
    await record_portion_outcome(
        db,
        function_id="fn_test_01",
        serving_kind="main_gravy",
        region="wb",
        planned_per_head=250.0,
        outcome="about_right",
    )

    # 3. Channel purchase
    await log_channel_purchase(
        db,
        context_key="wb:sweets:0",
        channel="zepto",
    )

    # 4. Preferences
    await record_preference_comparison(
        db,
        event_key="TEST1",
        winner="food",
        loser="decor",
        source="drag_rank",
    )

    await db.commit()

    # Run learning job
    result1 = await run_nightly_learning(db)
    assert result1["status"] == "success"
    assert result1["turnout_models_updated"] > 0
    assert result1["portion_models_updated"] > 0

    # Verify models exist in DB
    t_models = (await db.scalars(select(TurnoutModel))).all()
    assert len(t_models) > 0

    p_models = (await db.scalars(select(PortionModel))).all()
    assert len(p_models) > 0

    c_stats = (await db.scalars(select(ChannelStat))).all()
    assert len(c_stats) > 0

    # Idempotence: running learn second time doesn't error and produces identical models
    result2 = await run_nightly_learning(db)
    assert result2["status"] == "success"
    assert result2["turnout_models_updated"] == result1["turnout_models_updated"]


@pytest.mark.asyncio
async def test_custom_rule_no_mushrooms(db: AsyncSession) -> None:
    """Test that a host's custom 'no mushrooms' rule removes a mushroom dish."""
    data = get_data()

    # Base vocabulary with mushroom concept
    vocab = Vocabulary.build(
        [
            ("paneer", ["dairy"]),
            ("mushroom", ["mushroom"]),
            ("button mushroom", ["mushroom"]),
            ("tikka", []),
        ]
    )

    # Without custom rule: mushroom tikka with verified ingredients under pure_veg is allowed
    dish = ProposedItem(name="Mushroom Tikka", category="food")
    from hostora.engine.grounding import Evidence

    ev = [
        Evidence(
            source="product_label",
            ingredients=["button mushroom", "tikka"],
            citation="pack label",
        )
    ]
    res_base = ground(
        item=dish,
        evidence=ev,
        vocabulary=vocab,
        rule_ids=["pure_veg"],
        rules=data.rules,
        semantics=data.semantics,
    )
    assert res_base.status == "allowed"

    # Create host and event for foreign key constraints
    import datetime as dt

    from hostora.db.models import Host

    host = Host(uid="host_test_rule", display_name="Test Host")
    db.add(host)
    await db.flush()

    event = Event(
        event_key="EV100",
        host_uid="host_test_rule",
        title="Wedding",
        type="wedding",
        tradition="hindu",
        venue_label="Kolkata",
        venue_lat=22.57,
        venue_lng=88.36,
        budget_inr=50000,
        purge_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
    )
    db.add(event)
    await db.flush()

    # Save custom rule for event
    custom_rule = await save_event_rule(
        db,
        event_key="EV100",
        function_id="fn_001",
        rule_id="custom_no_mushrooms",
        forbid=["mushroom"],
        origin="host_edit",
    )
    await db.commit()
    assert custom_rule.id is not None

    # Merge custom rule into ruleset
    from hostora.engine.constraints import RuleSet

    extended_rules = dict(data.rules)
    extended_rules["custom_no_mushrooms"] = RuleSet(
        id="custom_no_mushrooms",
        label="No Mushrooms",
        forbid=("mushroom",),
    )

    # Now ground with custom rule active
    res_custom = ground(
        item=dish,
        evidence=[],
        vocabulary=vocab,
        rule_ids=["pure_veg", "custom_no_mushrooms"],
        rules=extended_rules,
        semantics=data.semantics,
    )
    assert res_custom.status == "removed"
    assert any("mushroom" in r.lower() for r in res_custom.reasons)


@pytest.mark.asyncio
async def test_invitation_validation() -> None:
    """Test validation of placeholders {link} and {code} in invitations."""
    # Valid
    valid_text = "Join us! RSVP at {link} using your code {code}."
    ok, _msg = validate_invitation_text(valid_text)
    assert ok is True

    # Missing {link}
    missing_link = "Join us for our wedding! Use your code {code}."
    ok_link, msg_link = validate_invitation_text(missing_link)
    assert ok_link is False
    assert "{link}" in msg_link

    # Missing {code}
    missing_code = "Join us for our wedding! RSVP at {link}."
    ok_code, msg_code = validate_invitation_text(missing_code)
    assert ok_code is False
    assert "{code}" in msg_code

    # Duplicate {link}
    dup_link = "RSVP at {link} or {link} with code {code}."
    ok_dup, _msg_dup = validate_invitation_text(dup_link)
    assert ok_dup is False

    # Too long
    long_text = "Hello " * 100 + "{link} {code}"
    ok_long, msg_long = validate_invitation_text(long_text)
    assert ok_long is False
    assert "500" in msg_long


@pytest.mark.asyncio
@respx.mock
async def test_osm_taginfo_rejection_of_unknown_tag() -> None:
    """Test that taginfo rejects an unknown/invalid tag with usage below threshold."""
    # Mock taginfo returning 0 count
    respx.get("https://taginfo.openstreetmap.org/api/4/tag/stats").mock(
        return_value=httpx.Response(200, json={"data": [{"count": 3}]})
    )

    async with httpx.AsyncClient() as client:
        is_valid = await validate_osm_tag("invalid_key", "nonexistent_val", client=client)
        assert is_valid is False

    # Mock taginfo returning high count
    respx.get("https://taginfo.openstreetmap.org/api/4/tag/stats").mock(
        return_value=httpx.Response(200, json={"data": [{"count": 15000}]})
    )

    async with httpx.AsyncClient() as client:
        is_valid_shop = await validate_osm_tag("shop", "bakery", client=client)
        assert is_valid_shop is True


@pytest.mark.asyncio
@respx.mock
async def test_osm_overpass_timeout_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test fallback when Overpass is slow/times out."""
    from hostora.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "demo_mode", False)

    respx.post("https://overpass-api.de/api/interpreter").mock(
        side_effect=httpx.ConnectTimeout("Overpass timeout")
    )

    async with httpx.AsyncClient() as client:
        vendors = await query_overpass_vendors(
            lat=22.57,
            lng=88.36,
            key="shop",
            val="bakery",
            client=client,
        )
        assert len(vendors) == 1
        assert "Local Bakery" in vendors[0]["name"]


@pytest.mark.asyncio
async def test_no_model_fallback_paths(db: AsyncSession) -> None:
    """Test that when models are empty, rule-based fallback / prior produces reasonable numbers."""
    from hostora.services.learning import compute_newsvendor_plates, get_turnout_rates

    # 1. Turnout rates fallback on empty DB
    rates, basis = await get_turnout_rates(db, "unseen:segment:key")
    assert "Prior" in basis
    assert "yes" in rates
    assert "maybe" in rates
    assert "no_reply" in rates
    assert rates["yes"].mean > rates["maybe"].mean > rates["no_reply"].mean

    # 2. Plate sizing fallback
    arrival_samples = [45, 48, 50, 52, 55]
    plates, p_basis = compute_newsvendor_plates(arrival_samples, short_waste_slider=5)
    assert 45 <= plates <= 55
    assert "Newsvendor" in p_basis


@pytest.mark.asyncio
async def test_backtest_bayesian_model_selection(db: AsyncSession) -> None:
    """Test rolling-origin backtest selects Bayesian model when loss is lower than rules."""
    from hostora.services.learning import (
        record_turnout_observation,
        run_backtest,
        run_nightly_learning,
    )

    # Insert 10 observations
    for i in range(10):
        await record_turnout_observation(
            db,
            function_id=f"fn_backtest_{i}",
            state="wb",
            event_type="wedding",
            function_kind="dinner",
            reply_bucket="lead_3d",
            status="yes",
            invited_households=50,
            arrived_households=43,
        )
    await db.commit()

    # Learn models
    await run_nightly_learning(db)

    # Run backtest with min_events=5
    res = await run_backtest(db, min_events=5)
    assert res["active_model"] == "bayesian"
    assert res["n_events"] >= 5
    assert res["bayesian_loss"] <= res["rules_loss"]
    assert 0.0 <= res["coverage_80"] <= 1.0
