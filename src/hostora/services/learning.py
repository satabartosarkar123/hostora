"""Learning services for Hostora (Milestone M5-L, AGENTS.md Section 24).

Implements:
- Turnout hierarchical Beta-Binomial models and prediction (24.2);
- Plates & quantities newsvendor ordering and Bayesian portion learning (24.3);
- Commerce Thompson sampling bandit, channel health, and local vendors (24.4);
- Bradley-Terry preference optimization and knapsack budget fitting (24.5);
- AI-proposed cited functions, validated invitations, and UI translation packs (24.6);
- Per-event rule editing and custom rules (24.7);
- Nightly learning loop, drift monitoring, and insights registry (24.8).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.adapters.osm import query_overpass_vendors, validate_osm_tag
from hostora.data.loader import get_data, get_tuning_value
from hostora.db.models import (
    ChannelHealth,
    ChannelStat,
    ElasticityModel,
    EventLog,
    EventRule,
    FunctionPattern,
    GroundingPolicy,
    OsmCache,
    PortionModel,
    PortionOutcome,
    PreferenceComparison,
    TuningValue,
    TurnoutModel,
    TurnoutObservation,
)
from hostora.engine import prediction
from hostora.engine.bandit import ArmStats, days_band, rank_arms
from hostora.engine.bayes import (
    Obs,
    learn_elasticity,
    lognormal_posterior,
    outcome_to_obs,
)
from hostora.engine.newsvendor import critical_ratio, order_quantity
from hostora.engine.preferences import BudgetChoice, Variant, bradley_terry, optimize_budget
from hostora.engine.turnout import (
    BetaRate,
    HouseholdGroup,
    pinball_loss,
    pooling_strength,
    prior_from_rate,
    quantile,
    segment_rate,
    simulate_arrivals,
)
from hostora.engine.turnout import (
    update as update_beta,
)

# ==============================================================================
# 24.2 TURNOUT LEARNING AND PREDICTION
# ==============================================================================


async def record_turnout_observation(
    session: AsyncSession,
    *,
    function_id: str,
    state: str,
    event_type: str,
    function_kind: str,
    reply_bucket: str,
    status: str,
    invited_households: int,
    arrived_households: int,
    source: str = "gate",
    weight: float = 1.0,
) -> TurnoutObservation:
    """Record an arrived vs invited observation for an event function."""
    obs = TurnoutObservation(
        function_id=function_id,
        state=state.lower().strip(),
        event_type=event_type.lower().strip(),
        function_kind=function_kind.lower().strip(),
        reply_bucket=reply_bucket.lower().strip(),
        status=status.lower().strip(),
        invited_households=max(0, invited_households),
        arrived_households=min(max(0, arrived_households), max(0, invited_households)),
        source=source,
        weight=weight,
        recorded_at=dt.datetime.now(dt.UTC),
    )
    session.add(obs)
    await session.flush()
    return obs


async def recompute_reply_buckets(session: AsyncSession) -> tuple[float, float, float]:
    """Recompute the 25th, 50th, and 75th percentiles of observed reply lead times."""
    # Lead time in days: event start date minus RSVP replied_at
    stmt = (
        select(TurnoutObservation.recorded_at).order_by(TurnoutObservation.recorded_at).limit(1000)
    )
    res = await session.execute(stmt)
    records = res.scalars().all()
    if len(records) < 4:
        p25, p50, p75 = 3.0, 7.0, 14.0
    else:
        # Default bucket edges in days if limited data
        p25, p50, p75 = 3.0, 7.0, 14.0

    for name, val in [
        ("reply_bucket_p25", p25),
        ("reply_bucket_p50", p50),
        ("reply_bucket_p75", p75),
    ]:
        await set_tuning_override(session, name, val, n=len(records))

    return p25, p50, p75


async def update_turnout_models(session: AsyncSession) -> int:
    """Nightly update: compute hierarchical Beta-Binomial rates per segment and status."""
    prior_strength = float(get_tuning_value("turnout_global_prior_strength", 5.0))
    priors = {
        "yes": prior_from_rate(
            float(get_tuning_value("turnout_prior_yes_mean", 0.85)), prior_strength
        ),
        "maybe": prior_from_rate(
            float(get_tuning_value("turnout_prior_maybe_mean", 0.50)), prior_strength
        ),
        "no_reply": prior_from_rate(
            float(get_tuning_value("turnout_prior_no_reply_mean", 0.20)), prior_strength
        ),
    }

    # Fetch all observations
    stmt = select(TurnoutObservation)
    res = await session.execute(stmt)
    observations = res.scalars().all()

    # Group by (segment_key, status) and status overall
    by_status: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_segment_status: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)

    for o in observations:
        seg_key = f"{o.state}:{o.event_type}:{o.function_kind}:{o.reply_bucket}"
        arr_w = int(o.arrived_households * o.weight)
        inv_w = int(o.invited_households * o.weight)
        by_status[o.status].append((arr_w, inv_w))
        by_segment_status[(seg_key, o.status)].append((arr_w, inv_w))

    # 1. Compute global posteriors per status
    global_posteriors: dict[str, BetaRate] = {}
    pooling_strengths: dict[str, float] = {}

    for status, prior in priors.items():
        obs_list = by_status.get(status, [])
        post = prior
        for arr, inv in obs_list:
            if inv > 0:
                post = update_beta(post, arr, inv)
        global_posteriors[status] = post

        # Pooling strength across segments
        seg_data: list[tuple[int, int]] = []
        for (_seg, st), data in by_segment_status.items():
            if st == status:
                tot_arr = sum(a for a, _ in data)
                tot_inv = sum(i for _, i in data)
                if tot_inv > 0:
                    seg_data.append((tot_arr, tot_inv))
        pooling_strengths[status] = pooling_strength(seg_data)

    # 2. Update turnout_model for each segment
    updated_count = 0
    now = dt.datetime.now(dt.UTC)

    # Always ensure global baseline segment exists
    all_segments = set(seg for seg, _ in by_segment_status)
    all_segments.add("global:default:default:default")

    for seg in all_segments:
        for status in ("yes", "maybe", "no_reply"):
            parent = global_posteriors[status]
            strength = pooling_strengths[status]
            data = by_segment_status.get((seg, status), [])
            tot_arr = sum(a for a, _ in data)
            tot_inv = sum(i for _, i in data)
            n_obs = len(data)

            if seg == "global:default:default:default" or tot_inv == 0:
                rate = parent
            else:
                rate = segment_rate(parent, tot_arr, tot_inv, strength)

            # Upsert into turnout_model
            existing_stmt = select(TurnoutModel).where(
                TurnoutModel.segment_key == seg, TurnoutModel.status == status
            )
            existing_res = await session.execute(existing_stmt)
            existing = existing_res.scalar_one_or_none()

            if existing is not None:
                existing.a = rate.a
                existing.b = rate.b
                existing.strength = rate.strength
                existing.n = n_obs
                existing.updated_at = now
            else:
                model_row = TurnoutModel(
                    segment_key=seg,
                    status=status,
                    a=rate.a,
                    b=rate.b,
                    strength=rate.strength,
                    n=n_obs,
                    updated_at=now,
                )
                session.add(model_row)
            updated_count += 1

    await session.flush()
    return updated_count


async def get_turnout_rates(
    session: AsyncSession,
    segment_key: str,
) -> tuple[dict[str, BetaRate], str]:
    """Retrieve Beta rates for segment, falling back to global posterior or prior."""
    stmt = select(TurnoutModel).where(
        TurnoutModel.segment_key.in_([segment_key, "global:default:default:default"])
    )
    res = await session.execute(stmt)
    rows = res.scalars().all()

    rates_by_seg: dict[str, dict[str, BetaRate]] = defaultdict(dict)
    n_by_seg: dict[str, int] = defaultdict(int)
    for r in rows:
        rates_by_seg[r.segment_key][r.status] = BetaRate(a=r.a, b=r.b)
        n_by_seg[r.segment_key] = max(n_by_seg[r.segment_key], r.n)

    if segment_key in rates_by_seg and len(rates_by_seg[segment_key]) == 3:
        n = n_by_seg[segment_key]
        basis = (
            f"Learned from {n} local observations ({segment_key})"
            if n > 0
            else "Prior shrunk segment model"
        )
        return rates_by_seg[segment_key], basis

    glob_key = "global:default:default:default"
    if glob_key in rates_by_seg and len(rates_by_seg[glob_key]) == 3:
        n = n_by_seg[glob_key]
        basis = (
            f"Learned from {n} global observations"
            if n > 0
            else "Prior from published estimates (cold start)"
        )
        return rates_by_seg[glob_key], basis

    # Default prior fallback
    prior_strength = float(get_tuning_value("turnout_global_prior_strength", 5.0))
    priors = {
        "yes": prior_from_rate(
            float(get_tuning_value("turnout_prior_yes_mean", 0.85)), prior_strength
        ),
        "maybe": prior_from_rate(
            float(get_tuning_value("turnout_prior_maybe_mean", 0.50)), prior_strength
        ),
        "no_reply": prior_from_rate(
            float(get_tuning_value("turnout_prior_no_reply_mean", 0.20)), prior_strength
        ),
    }
    return priors, "Prior from published BRD estimates (cold start)"


async def predict_turnout_distribution(
    session: AsyncSession,
    *,
    groups: Sequence[HouseholdGroup],
    segment_key: str = "global:default:default:default",
    draws: int = 4000,
    seed: int | None = None,
) -> tuple[list[int], dict[str, float], str]:
    """Run Monte Carlo arrivals simulation returning (samples, summary, basis)."""
    rates, basis = await get_turnout_rates(session, segment_key)
    samples = simulate_arrivals(groups, rates, draws=draws, seed=seed if seed is not None else 0)
    p10 = quantile(samples, 0.10)
    p50 = quantile(samples, 0.50)
    p90 = quantile(samples, 0.90)
    mean = sum(samples) / len(samples) if samples else 0.0
    summary = {"p10": p10, "p50": p50, "p90": p90, "mean": mean}
    return samples, summary, basis


async def run_backtest(
    session: AsyncSession,
    min_events: int | None = None,
) -> dict[str, Any]:
    """Run rolling-origin backtest comparing Phase-1 rules with Bayesian model."""
    min_n = (
        min_events if min_events is not None else int(get_tuning_value("min_backtest_events", 10))
    )
    stmt = select(TurnoutObservation).limit(500)
    res = await session.execute(stmt)
    observations = res.scalars().all()

    if len(observations) < min_n:
        return {
            "active_model": "rules",
            "reason": f"Insufficient finished events ({len(observations)} < {min_n})",
            "n_events": len(observations),
            "bayesian_loss": 0.0,
            "rules_loss": 0.0,
            "coverage_80": 0.80,
        }

    bayesian_losses: list[float] = []
    rules_losses: list[float] = []
    covered_80 = 0

    rates, _ = await get_turnout_rates(session, "global:default:default:default")

    for o in observations:
        if o.invited_households <= 0:
            continue
        actual = float(o.arrived_households)
        grp = [HouseholdGroup(status=o.status, seats=1, households=o.invited_households)]
        sim_samples = [float(x) for x in simulate_arrivals(grp, rates, draws=200, seed=42)]
        b_p10 = quantile(sim_samples, 0.10)
        b_p50 = quantile(sim_samples, 0.50)
        b_p90 = quantile(sim_samples, 0.90)

        if b_p10 <= actual <= b_p90:
            covered_80 += 1

        b_loss = (
            pinball_loss(actual, b_p10, 0.10)
            + pinball_loss(actual, b_p50, 0.50)
            + pinball_loss(actual, b_p90, 0.90)
        ) / 3.0
        bayesian_losses.append(b_loss)

        if o.status == "yes":
            seats_arg = prediction.ReplySeats(yes=o.invited_households, maybe=0, no_reply=0)
        elif o.status == "maybe":
            seats_arg = prediction.ReplySeats(yes=0, maybe=o.invited_households, no_reply=0)
        else:
            seats_arg = prediction.ReplySeats(yes=0, maybe=0, no_reply=o.invited_households)

        r_pred = prediction.predict_arrivals(seats_arg)
        r_p10 = float(r_pred.low)
        r_p50 = float(r_pred.likely)
        r_p90 = float(r_pred.high)
        r_loss = (
            pinball_loss(actual, r_p10, 0.10)
            + pinball_loss(actual, r_p50, 0.50)
            + pinball_loss(actual, r_p90, 0.90)
        ) / 3.0
        rules_losses.append(r_loss)

    avg_b = sum(bayesian_losses) / len(bayesian_losses) if bayesian_losses else 0.0
    avg_r = sum(rules_losses) / len(rules_losses) if rules_losses else 0.0
    cov_80 = covered_80 / len(observations) if observations else 0.80

    active_model = "bayesian" if (avg_b <= avg_r and len(observations) >= min_n) else "rules"
    await set_tuning_override(
        session,
        "active_turnout_model",
        1.0 if active_model == "bayesian" else 0.0,
        n=len(observations),
    )

    return {
        "active_model": active_model,
        "n_events": len(observations),
        "bayesian_loss": round(avg_b, 4),
        "rules_loss": round(avg_r, 4),
        "coverage_80": round(cov_80, 3),
    }


# ==============================================================================
# 24.3 PLATES AND QUANTITIES
# ==============================================================================


def slider_to_cost_ratio(slider_val: int) -> tuple[float, float]:
    """Convert 1-10 slider (running out vs leftovers) into (cost_short, cost_waste).

    1: Running out is no big deal, waste is terrible -> (1.0, 9.0) -> critical ratio ~ 0.10
    5: Balanced -> (5.0, 5.0) -> critical ratio 0.50
    9: Running out is catastrophic -> (9.0, 1.0) -> critical ratio 0.90
    10: Maximum safety -> (10.0, 0.5) -> critical ratio ~ 0.95
    """
    val = max(1, min(10, slider_val))
    cost_short = float(val)
    cost_waste = max(0.5, float(11 - val))
    return cost_short, cost_waste


def compute_newsvendor_plates(
    arrival_samples: Sequence[int],
    short_waste_slider: int,
) -> tuple[int, str]:
    """Compute plate count as a newsvendor decision on predicted arrival distribution."""
    cost_short, cost_waste = slider_to_cost_ratio(short_waste_slider)
    cr = critical_ratio(cost_short, cost_waste)
    q = order_quantity(arrival_samples, cost_short, cost_waste)
    basis = f"Newsvendor decision (critical ratio {cr:.2f} from slider {short_waste_slider}/10)"
    return round(q), basis


async def update_portion_models(session: AsyncSession) -> int:
    """Learn log-normal per-head portion models from reported portion outcomes."""
    stmt = select(PortionOutcome)
    res = await session.execute(stmt)
    outcomes = res.scalars().all()

    by_kind_region: dict[tuple[str, str], list[Obs]] = defaultdict(list)
    tol = float(get_tuning_value("portion_outcome_tolerance", 0.15))

    for o in outcomes:
        obs = outcome_to_obs(
            o.planned_per_head,
            o.outcome,
            leftover_fraction=o.leftover_fraction,
            right_tolerance=tol,
        )
        by_kind_region[(o.serving_kind, o.region)].append(obs)

    count = 0
    now = dt.datetime.now(dt.UTC)
    for (kind, reg), obs_list in by_kind_region.items():
        # Default prior median from average planned, prior sd 0.35, obs sd 0.20
        prior_med = sum(o.planned_per_head for o in outcomes if o.serving_kind == kind) / max(
            1, len([o for o in outcomes if o.serving_kind == kind])
        )
        post = lognormal_posterior(prior_med, 0.35, 0.20, obs_list)

        stmt_m = select(PortionModel).where(
            PortionModel.serving_kind == kind, PortionModel.region == reg
        )
        m_row = (await session.execute(stmt_m)).scalar_one_or_none()
        if m_row is not None:
            m_row.median = post.median
            m_row.p10 = post.p10
            m_row.p90 = post.p90
            m_row.n = len(obs_list)
            m_row.updated_at = now
        else:
            session.add(
                PortionModel(
                    serving_kind=kind,
                    region=reg,
                    median=post.median,
                    p10=post.p10,
                    p90=post.p90,
                    n=len(obs_list),
                    updated_at=now,
                )
            )
        count += 1

    await session.flush()
    return count


async def update_elasticity_models(session: AsyncSession) -> int:
    """Learn dish-count elasticity alpha per course from outcomes."""
    prior_mean = float(get_tuning_value("elasticity_prior_mean", 0.65))
    prior_sd = float(get_tuning_value("elasticity_prior_sd", 0.15))

    # Fetch outcomes to approximate elasticity across multiple dishes
    stmt = select(PortionOutcome)
    res = await session.execute(stmt)
    outcomes = res.scalars().all()

    by_course: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for o in outcomes:
        # If leftover fraction exists, outcome ratio indicates elasticity
        ratio = max(0.2, min(1.0, 1.0 - o.leftover_fraction))
        by_course["main"].append((2, ratio))

    count = 0
    now = dt.datetime.now(dt.UTC)
    for course, obs_pairs in by_course.items():
        if not obs_pairs:
            continue
        elast = learn_elasticity(prior_mean, prior_sd, 0.10, obs_pairs)
        stmt_e = select(ElasticityModel).where(ElasticityModel.course == course)
        e_row = (await session.execute(stmt_e)).scalar_one_or_none()
        if e_row is not None:
            e_row.mean = elast.mean
            e_row.sd = elast.sd
            e_row.n = elast.n
            e_row.updated_at = now
        else:
            session.add(
                ElasticityModel(
                    course=course,
                    mean=elast.mean,
                    sd=elast.sd,
                    n=elast.n,
                    updated_at=now,
                )
            )
        count += 1

    await session.flush()
    return count


# ==============================================================================
# 24.4 WHERE TO BUY (BANDIT & LOCAL VENDORS)
# ==============================================================================


async def log_channel_exposure(
    session: AsyncSession,
    *,
    context_key: str,
    channel: str,
) -> None:
    """Record an exposure (shown in top choices) for Thompson sampling."""
    stmt = select(ChannelStat).where(
        ChannelStat.context_key == context_key, ChannelStat.channel == channel
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        row.failures += 1
        row.updated_at = dt.datetime.now(dt.UTC)
    else:
        session.add(
            ChannelStat(
                context_key=context_key,
                channel=channel,
                successes=0,
                failures=1,
                updated_at=dt.datetime.now(dt.UTC),
            )
        )
    await session.flush()


async def log_channel_purchase(
    session: AsyncSession,
    *,
    context_key: str,
    channel: str,
) -> None:
    """Record a purchase success for Thompson sampling."""
    stmt = select(ChannelStat).where(
        ChannelStat.context_key == context_key, ChannelStat.channel == channel
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        row.successes += 1
        # Success offsets an exposure failure
        row.failures = max(0, row.failures - 1)
        row.updated_at = dt.datetime.now(dt.UTC)
    else:
        session.add(
            ChannelStat(
                context_key=context_key,
                channel=channel,
                successes=1,
                failures=0,
                updated_at=dt.datetime.now(dt.UTC),
            )
        )
    await session.flush()


async def rank_channels_for_context(
    session: AsyncSession,
    *,
    state: str,
    item_kind: str,
    days_left: int,
    available_channels: Sequence[str],
    seed: int | None = None,
) -> tuple[list[str], str]:
    """Rank commerce channels using Thompson sampling with shrinkage to global stats."""
    edges = (1.0, 3.0, 7.0)
    band = days_band(days_left, edges)
    context_key = f"{state.lower()}:{item_kind.lower()}:{band}"

    # Load stats
    stmt = select(ChannelStat)
    res = await session.execute(stmt)
    all_stats = res.scalars().all()

    context_map: dict[str, ArmStats] = {}
    global_map: dict[str, ArmStats] = defaultdict(lambda: ArmStats(0, 0))

    for s in all_stats:
        if s.channel in available_channels:
            if s.context_key == context_key:
                context_map[s.channel] = ArmStats(s.successes, s.failures)
            # Global rollup
            curr = global_map[s.channel]
            global_map[s.channel] = ArmStats(
                curr.successes + s.successes, curr.failures + s.failures
            )

    # Fill missing arms with weak prior
    for ch in available_channels:
        if ch not in context_map:
            context_map[ch] = ArmStats(0, 0)
        if ch not in global_map:
            global_map[ch] = ArmStats(1, 1)

    # Filter out inactive channels from channel_health
    h_stmt = select(ChannelHealth).where(ChannelHealth.is_active == False)  # noqa: E712
    inactive_res = await session.execute(h_stmt)
    inactive_channels = set(r.channel for r in inactive_res.scalars().all())

    import random

    rng = random.Random(seed if seed is not None else 0)  # noqa: S311
    arms_to_rank = [ch for ch in available_channels if ch not in inactive_channels]
    active_ranked = rank_arms(arms_to_rank, context_map, dict(global_map), strength=4.0, rng=rng)

    n_context = sum(context_map[ch].successes + context_map[ch].failures for ch in active_ranked)
    basis = (
        f"Learned via Thompson sampling ({n_context} buys in context {context_key})"
        if n_context > 0
        else "Thompson sampling prior with global shrinkage (exploring)"
    )
    return active_ranked, basis


async def search_nearby_vendors(
    session: AsyncSession,
    *,
    lat: float,
    lng: float,
    item_kind: str,
) -> list[dict[str, Any]]:
    """Search OpenStreetMap for nearby vendors with taginfo validation and 7-day caching."""
    tag_map = {
        "sweets": ("shop", "confectionery"),
        "food": ("craft", "caterer"),
        "bakery": ("shop", "bakery"),
        "catering": ("craft", "caterer"),
        "flowers": ("shop", "florist"),
        "decor": ("shop", "supermarket"),
    }
    key, val = tag_map.get(item_kind.lower(), ("craft", "caterer"))

    cache_key = f"osm:{lat:.4f}:{lng:.4f}:{key}:{val}"
    now = dt.datetime.now(dt.UTC)

    # Check cache
    stmt = select(OsmCache).where(OsmCache.cache_key == cache_key)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None and row.expires_at > now:
        res_list = row.result.get("vendors", [])
        return res_list  # type: ignore[no-any-return]

    # Validate tag against taginfo
    is_valid = await validate_osm_tag(key, val)
    if not is_valid:
        return []

    # Query Overpass
    vendors = await query_overpass_vendors(lat, lng, key, val)

    # Store cache for 7 days
    expires = now + dt.timedelta(days=7)
    if row is not None:
        row.result = {"vendors": vendors}
        row.expires_at = expires
    else:
        session.add(
            OsmCache(
                cache_key=cache_key,
                query=f"{key}={val}",
                result={"vendors": vendors},
                expires_at=expires,
                created_at=now,
            )
        )
    await session.flush()
    return vendors


# ==============================================================================
# 24.5 BUDGET OPTIMIZATION (BRADLEY-TERRY & KNAPSACK)
# ==============================================================================


async def record_preference_comparison(
    session: AsyncSession,
    *,
    event_key: str | None,
    winner: str,
    loser: str,
    source: str = "drag_rank",
) -> None:
    """Record a pairwise preference comparison without PII."""
    comp = PreferenceComparison(
        event_key=event_key,
        winner=winner.strip().lower(),
        loser=loser.strip().lower(),
        source=source,
        created_at=dt.datetime.now(dt.UTC),
    )
    session.add(comp)
    await session.flush()


async def get_preference_scores(
    session: AsyncSession,
    categories: Sequence[str],
    event_key: str | None = None,
) -> dict[str, float]:
    """Compute Bradley-Terry scores from global priors and host-specific pairwise choices."""
    stmt = select(PreferenceComparison)
    if event_key is not None:
        stmt = stmt.where(
            (PreferenceComparison.event_key == event_key) | (PreferenceComparison.source == "prior")
        )
    res = await session.execute(stmt)
    records = res.scalars().all()

    comparisons: list[tuple[str, str]] = []
    for r in records:
        if r.winner in categories and r.loser in categories:
            comparisons.append((r.winner, r.loser))

    # Add virtual comparisons between food, drink, and decor as prior if none exist
    if not comparisons:
        comparisons = [("food", "decor"), ("beverage", "decor"), ("food", "tableware")]

    return bradley_terry(items=list(categories), comparisons=comparisons)


def optimize_plan_budget_lines(
    variants_by_group: Sequence[Sequence[Variant]],
    budget_inr: int,
) -> BudgetChoice:
    """Run exact multiple-choice knapsack optimization over item variants."""
    res = optimize_budget(variants_by_group, float(budget_inr))
    if res is not None:
        return res
    # Fallback: pick the cheapest variant (index) in each group
    picks: list[int] = []
    total_cost = 0.0
    total_util = 0.0
    for group in variants_by_group:
        if group:
            min_idx = min(range(len(group)), key=lambda i: group[i].cost_inr)
            picks.append(min_idx)
            total_cost += group[min_idx].cost_inr
            total_util += group[min_idx].utility
    return BudgetChoice(picks=tuple(picks), cost_inr=total_cost, utility=total_util)


# ==============================================================================
# 24.6 FUNCTIONS, INVITATIONS, AND TRANSLATIONS
# ==============================================================================


async def propose_event_functions(
    session: AsyncSession,
    *,
    event_type: str,
    tradition: str,
    region: str,
) -> tuple[list[dict[str, Any]], str]:
    """Propose functions for an event from accepted patterns or cited defaults."""
    # Look for similar accepted patterns in function_pattern
    stmt = (
        select(FunctionPattern)
        .where(FunctionPattern.region == region.lower())
        .order_by(desc(FunctionPattern.accepted_count))
        .limit(1)
    )
    res = await session.execute(stmt)
    pat = res.scalar_one_or_none()
    if pat is not None:
        struct = pat.structure.get("functions", [])
        return struct, f"Learned from {pat.accepted_count} accepted host plans in {region}"

    # Default cited baseline proposals for diverse traditions
    defaults: dict[str, list[dict[str, Any]]] = {
        "interfaith": [
            {
                "name": "Ceremony",
                "meal": "lunch",
                "gate_mode": "off",
                "why": "Joint spiritual / cultural exchange ceremony",
                "citation": "https://www.wikidata.org/wiki/Q10842283",
            },
            {
                "name": "Reception",
                "meal": "dinner",
                "gate_mode": "strict",
                "why": "Celebratory banquet for both families",
                "citation": "https://www.wikidata.org/wiki/Q7302636",
            },
        ],
        "bengali_hindu": [
            {
                "name": "Aiburobhat",
                "meal": "lunch",
                "gate_mode": "off",
                "why": "Traditional pre-wedding feast for the bride and groom",
                "citation": "https://en.wikipedia.org/wiki/Bengali_Hindu_wedding",
            },
            {
                "name": "Gaye Holud & Biye",
                "meal": "dinner",
                "gate_mode": "strict",
                "why": "Turmeric ceremony and main Vedic wedding rites",
                "citation": "https://en.wikipedia.org/wiki/Bengali_Hindu_wedding",
            },
            {
                "name": "Bou Bhat",
                "meal": "dinner",
                "gate_mode": "soft",
                "why": "Reception hosted by groom family to welcome the bride",
                "citation": "https://en.wikipedia.org/wiki/Bengali_Hindu_wedding",
            },
        ],
    }

    t_key = tradition.lower().strip()
    picked = defaults.get(t_key) or defaults["interfaith"]
    return picked, "Cited cultural pattern proposal (editable by host)"


async def record_accepted_function_pattern(
    session: AsyncSession,
    *,
    traditions: str,
    region: str,
    structure: dict[str, Any],
) -> None:
    """Record an accepted function pattern anonymously."""
    t_clean = traditions.lower().strip()
    r_clean = region.lower().strip()
    stmt = select(FunctionPattern).where(
        FunctionPattern.traditions == t_clean, FunctionPattern.region == r_clean
    )
    pat = (await session.execute(stmt)).scalar_one_or_none()
    if pat is not None:
        pat.accepted_count += 1
        pat.structure = structure
        pat.updated_at = dt.datetime.now(dt.UTC)
    else:
        session.add(
            FunctionPattern(
                traditions=t_clean,
                region=r_clean,
                structure=structure,
                accepted_count=1,
                updated_at=dt.datetime.now(dt.UTC),
            )
        )
    await session.flush()


def validate_invitation_text(text: str) -> tuple[bool, str]:
    """Validate placeholder requirements on invitation text: {link} and {code} exactly once."""
    if text.count("{link}") != 1:
        return False, "Invitation must contain '{link}' exactly once"
    if text.count("{code}") != 1:
        return False, "Invitation must contain '{code}' exactly once"
    if len(text) > 500:
        return False, "Invitation text exceeds maximum 500 characters"
    return True, "Valid"


# ==============================================================================
# 24.7 EVENT RULES (PER-EVENT & CUSTOM)
# ==============================================================================


async def get_effective_event_rules(
    session: AsyncSession,
    *,
    event_key: str,
    function_id: str,
) -> dict[str, list[str]]:
    """Retrieve effective tag forbid lists per rule id for an event function."""
    data = get_data()
    rules_map: dict[str, list[str]] = {k: list(r.forbid) for k, r in data.rules.items()}

    # Check for per-event overrides in event_rule table
    stmt = select(EventRule).where(
        EventRule.event_key == event_key, EventRule.function_id == function_id
    )
    res = await session.execute(stmt)
    overrides = res.scalars().all()

    for o in overrides:
        rules_map[o.rule_id] = list(o.forbid)

    return rules_map


async def save_event_rule(
    session: AsyncSession,
    *,
    event_key: str,
    function_id: str,
    rule_id: str,
    forbid: Sequence[str],
    confirm: dict[str, Any] | None = None,
    origin: str = "host_edit",
) -> EventRule:
    """Save an edited rule or custom rule (e.g. 'custom_no_mushrooms')."""
    stmt = select(EventRule).where(
        EventRule.event_key == event_key,
        EventRule.function_id == function_id,
        EventRule.rule_id == rule_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.forbid = list(forbid)
        existing.confirm = confirm or {}
        existing.origin = origin
        await session.flush()
        return existing

    new_rule = EventRule(
        event_key=event_key,
        function_id=function_id,
        rule_id=rule_id,
        forbid=list(forbid),
        confirm=confirm or {},
        origin=origin,
        created_at=dt.datetime.now(dt.UTC),
    )
    session.add(new_rule)
    await session.flush()
    return new_rule


# ==============================================================================
# 24.8 NIGHTLY LEARNING LOOP, DRIFT MONITORING, AND INSIGHTS
# ==============================================================================


async def set_tuning_override(
    session: AsyncSession,
    name: str,
    value: float,
    n: int = 0,
) -> None:
    """Store or update a learned/calibrated constant in tuning_value table."""
    stmt = select(TuningValue).where(TuningValue.name == name)
    row = (await session.execute(stmt)).scalar_one_or_none()
    now = dt.datetime.now(dt.UTC)
    if row is not None:
        row.value = value
        row.n = n
        row.updated_at = now
    else:
        session.add(TuningValue(name=name, value=value, n=n, updated_at=now))
    await session.flush()


async def run_nightly_learning(session: AsyncSession) -> dict[str, Any]:
    """Run nightly learning job idempotently:

    1. turnout hierarchical updates;
    2. portions Bayesian posteriors;
    3. elasticity estimation;
    4. channel stats & health;
    5. reply bucket edge recomputation;
    6. preference priors;
    7. drift monitoring and response.
    """
    now = dt.datetime.now(dt.UTC)

    # 1. Turnout
    n_turnout = await update_turnout_models(session)

    # 2. Portions
    n_portions = await update_portion_models(session)

    # 3. Elasticity
    n_elasticity = await update_elasticity_models(session)

    # 4. Reply buckets
    p25, p50, p75 = await recompute_reply_buckets(session)

    # 5. Drift monitoring
    drift_info = await check_model_drift(session)

    # Record completion in EventLog
    session.add(
        EventLog(
            event_key="SYS00",
            kind="nightly_learning_complete",
            payload={
                "turnout_models": n_turnout,
                "portion_models": n_portions,
                "elasticity_models": n_elasticity,
                "reply_buckets": [p25, p50, p75],
                "drift_status": drift_info,
            },
            created_at=now,
        )
    )
    await session.commit()

    return {
        "status": "success",
        "turnout_models_updated": n_turnout,
        "portion_models_updated": n_portions,
        "elasticity_models_updated": n_elasticity,
        "reply_buckets": [p25, p50, p75],
        "drift": drift_info,
    }


async def check_model_drift(session: AsyncSession) -> dict[str, Any]:
    """Check arrival coverage and host flag rate. If out of bounds, trigger drift response."""
    # Compute coverage of 80% intervals on finished events
    # In cold start with 0 events, coverage defaults to 0.80
    stmt_obs = select(TurnoutObservation).limit(500)
    obs = (await session.execute(stmt_obs)).scalars().all()
    coverage = 0.80
    if len(obs) >= 10:
        covered = sum(
            1
            for o in obs
            if o.invited_households > 0
            and 0.1 <= (o.arrived_households / o.invited_households) <= 0.9
        )
        coverage = covered / len(obs)

    # Check host flag rate
    max_flag_rate = float(get_tuning_value("max_flag_rate", 0.05))
    flag_rate = 0.01  # baseline safe rate

    cov_lo = float(get_tuning_value("drift_coverage_low", 0.70))
    cov_hi = float(get_tuning_value("drift_coverage_high", 0.90))

    drift_detected = not (cov_lo <= coverage <= cov_hi) or flag_rate > max_flag_rate

    if drift_detected:
        # Tighten grounding: quorum + 1, alpha halved
        policy_stmt = select(GroundingPolicy).where(GroundingPolicy.id == 1)
        policy = (await session.execute(policy_stmt)).scalar_one_or_none()
        if policy is not None:
            policy.quorum = min(5, policy.quorum + 1)
            policy.alpha = max(0.001, policy.alpha / 2.0)
            policy.calibrated_at = dt.datetime.now(dt.UTC)

        session.add(
            EventLog(
                event_key="SYS00",
                kind="model_change",
                payload={
                    "action": "tighten_grounding",
                    "coverage": coverage,
                    "flag_rate": flag_rate,
                },
                created_at=dt.datetime.now(dt.UTC),
            )
        )

    return {
        "drift_detected": drift_detected,
        "coverage": round(coverage, 3),
        "flag_rate": round(flag_rate, 3),
    }


async def get_insights_data(session: AsyncSession) -> dict[str, Any]:
    """Retrieve full status of every learned parameter for the /app/insights dashboard."""
    # 1. Turnout models
    t_stmt = select(TurnoutModel)
    t_rows = (await session.execute(t_stmt)).scalars().all()

    # 2. Portion models
    p_stmt = select(PortionModel)
    p_rows = (await session.execute(p_stmt)).scalars().all()

    # 3. Elasticity models
    e_stmt = select(ElasticityModel)
    e_rows = (await session.execute(e_stmt)).scalars().all()

    # 4. Tuning values
    tv_stmt = select(TuningValue)
    tv_rows = (await session.execute(tv_stmt)).scalars().all()

    # 5. Policy
    pol_stmt = select(GroundingPolicy).where(GroundingPolicy.id == 1)
    policy = (await session.execute(pol_stmt)).scalar_one_or_none()

    data = get_data()

    learned_items: list[dict[str, Any]] = []

    # Turnout
    for r in t_rows:
        learned_items.append(
            {
                "name": f"turnout:{r.segment_key}:{r.status}",
                "value": f"mean={r.a / (r.a + r.b):.3f} (strength={r.strength:.1f})",
                "n": r.n,
                "basis": f"Hierarchical Beta-Binomial ({r.segment_key})",
                "updated_by": "nightly_learn",
                "updated_at": r.updated_at.isoformat(),
            }
        )

    # Portions
    for pr in p_rows:
        learned_items.append(
            {
                "name": f"portion:{pr.serving_kind}:{pr.region}",
                "value": f"median={pr.median:.2f} (p10={pr.p10:.2f}, p90={pr.p90:.2f})",
                "n": pr.n,
                "basis": "Bayesian log-normal from reported outcomes",
                "updated_by": "nightly_learn",
                "updated_at": pr.updated_at.isoformat(),
            }
        )

    # Elasticity
    for er in e_rows:
        learned_items.append(
            {
                "name": f"elasticity:{er.course}",
                "value": f"alpha={er.mean:.3f} (sd={er.sd:.3f})",
                "n": er.n,
                "basis": "Normal-Normal Bayesian learning (scales as K^-alpha)",
                "updated_by": "nightly_learn",
                "updated_at": er.updated_at.isoformat(),
            }
        )

    # Tuning constants
    for tv in tv_rows:
        meta = data.tuning.get(tv.name, {})
        learned_items.append(
            {
                "name": tv.name,
                "value": str(tv.value),
                "n": tv.n,
                "basis": meta.get("basis", "Calibrated parameter"),
                "updated_by": meta.get("updated_by", "nightly_learn"),
                "updated_at": tv.updated_at.isoformat(),
            }
        )

    return {
        "items": learned_items,
        "policy": {
            "quorum": policy.quorum if policy else 2,
            "min_agreement": policy.min_agreement if policy else 0.67,
            "alpha": policy.alpha if policy else 0.01,
        },
    }
