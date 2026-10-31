from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from sqlalchemy import select

from hostora.adapters.commerce import (
    build_buy_options_for_item,
    build_offers_for_item,
    compute_days_left,
)
from hostora.adapters.knowledge.verifier import verify_dish_composition
from hostora.adapters.knowledge.web import fetch_web_evidence
from hostora.data.loader import DataBundle, get_data, get_tuning_value
from hostora.db.models import Event, EventLog, Fn, Household, PlanModel
from hostora.engine.grounding import (
    Evidence,
    GroundedItem,
    GroundingPolicy,
    ProposedItem,
    ground,
)
from hostora.engine.newsvendor import critical_ratio, order_quantity
from hostora.engine.portions import (
    ServingKind,
    consumption_row,
    units_per_guests,
)
from hostora.engine.pricing import PriceQuote, choose_price
from hostora.engine.quantity import (
    Meal,
    round_to_packs,
)
from hostora.planner.prompts import (
    PROPOSAL_SYSTEM_PROMPT,
    ProposedItemProposal,
    build_proposal_user_prompt,
)
from hostora.planner.redact import redact_brief
from hostora.planner.template import DEFAULT_COURSE_KINDS, build_template_explanations
from hostora.schemas.common import RuleId
from hostora.schemas.plan import (
    GroundingInfo,
    PackOut,
    Plan,
    PlanBudget,
    PlanHeadcount,
    PlanItem,
    PlanRequest,
    PriceInfo,
    RemovedItem,
)
from hostora.services.knowledge import get_default_vocabulary
from hostora.services.learning import slider_to_cost_ratio
from hostora.services.summary import get_event_summary

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.adapters.llm.router import LlmRouter

logger = logging.getLogger(__name__)

STANDARD_SERVING_KINDS: dict[str, ServingKind] = {
    "rice_main": ServingKind(
        "rice_main", "main", "g", 300, 150, (1000, 5000), basis="catering guidance"
    ),
    "main_gravy": ServingKind(
        "main_gravy", "main", "g", 250, 120, (1000, 2000), basis="catering guidance"
    ),
    "main_dry": ServingKind(
        "main_dry", "side", "g", 150, 80, (500, 1000), basis="catering guidance"
    ),
    "starter_piece": ServingKind(
        "starter_piece", "starter", "piece", 3, 2, (10, 20), basis="catering guidance"
    ),
    "bread_piece": ServingKind(
        "bread_piece", "bread", "piece", 3, 2, (10, 25), basis="catering guidance"
    ),
    "sweet_piece": ServingKind(
        "sweet_piece", "dessert", "piece", 2, 1, (10, 20), basis="catering guidance"
    ),
    "dessert_liquid": ServingKind(
        "dessert_liquid", "dessert", "ml", 150, 100, (1000,), basis="catering guidance"
    ),
    "drink_cup": ServingKind(
        "drink_cup", "drink", "ml", 200, 150, (1000, 2000), basis="catering guidance"
    ),
    "supply": ServingKind("supply", "supply", "piece", 1, 1, (50, 100), basis="per person"),
    "decor": ServingKind(
        "decor", "decor", "piece", 0, 0, (1,), per_guests=50, basis="per 50 guests"
    ),
}


def _get_serving_kind(kind_id: str, course: str, category: str) -> ServingKind:
    if kind_id in STANDARD_SERVING_KINDS:
        return STANDARD_SERVING_KINDS[kind_id]
    if category in ("supply", "tableware"):
        return STANDARD_SERVING_KINDS["supply"]
    if category in ("decor", "lighting"):
        return STANDARD_SERVING_KINDS["decor"]
    if category == "beverage":
        return STANDARD_SERVING_KINDS["drink_cup"]
    if course == "dessert":
        return STANDARD_SERVING_KINDS["sweet_piece"]
    if course in ("bread", "starter"):
        return STANDARD_SERVING_KINDS["bread_piece"]
    if course == "rice":
        return STANDARD_SERVING_KINDS["rice_main"]
    return STANDARD_SERVING_KINDS["main_gravy"]


async def execute_planner_steps(
    plan_id: str,
    function_id: str,
    event_type: str,
    meal: Meal,
    budget_inr: int,
    days_left: int,
    likely: int,
    p80: int,
    headcount: PlanHeadcount,
    rules_list: list[RuleId],
    allergies: list[str],
    hh_names: list[str],
    brief: str,
    router: LlmRouter,
    data: DataBundle | None = None,
    now: datetime | None = None,
    region: str = "India",
    short_waste_slider: int = 5,
    arrival_samples: list[int] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Execute open-world grounded planner steps and stream progress tuples (evt_name, payload)."""
    now = now or datetime.now(UTC)
    data = data or get_data()
    today_dt = now.date()

    yield (
        "stage",
        {"stage": "headcount", "adults": headcount.adults, "children": headcount.children},
    )

    rule_ids = [r.value for r in rules_list]
    rule_labels = [data.rules[r].label for r in rule_ids if r in data.rules]
    yield ("stage", {"stage": "rules", "active_rules": rule_labels})

    # Propose
    yield ("stage", {"stage": "proposing", "message": f"Proposing items for {region}..."})
    redacted = redact_brief(brief, forbidden_terms=hh_names)
    prompt = build_proposal_user_prompt(
        event_type=event_type,
        meal=meal,
        tradition="modern",
        region=region,
        adults=headcount.adults,
        children=headcount.children,
        veg_seats=headcount.veg_seats,
        jain_seats=headcount.jain_seats,
        non_veg_seats=headcount.non_veg_seats,
        rule_labels=rule_labels,
        brief=redacted,
        budget_inr=budget_inr,
    )

    client, route_name = router.select()
    proposals: list[ProposedItemProposal] = []
    if client is not None:
        try:
            raw_json = await client.complete_json(PROPOSAL_SYSTEM_PROMPT, prompt, max_tokens=1500)
            data_json = json.loads(raw_json)
            if "items" in data_json and isinstance(data_json["items"], list):
                for raw_it in data_json["items"]:
                    proposals.append(ProposedItemProposal.model_validate(raw_it))
        except Exception as e:
            logger.warning("Proposal failure: %s", e)

    if not proposals:
        route_name = "template"
        for c, k, name in DEFAULT_COURSE_KINDS:
            proposals.append(
                ProposedItemProposal(
                    name=name,
                    category="food",
                    course=c,
                    serving_kind=k,
                    ingredients=["rice", "salt"],
                    declared_diet="veg",
                    why="Course item",
                    search_query=name.lower(),
                )
            )

    yield (
        "stage",
        {"stage": "evidence", "items_count": len(proposals), "message": "Gathering evidence..."},
    )
    vocab = get_default_vocabulary()

    evidence_by_item: dict[str, list[Evidence]] = {}
    for p in proposals:
        evs: list[Evidence] = []
        if route_name in ("template", "manual"):
            evs.append(
                Evidence(source="host", ingredients=tuple(p.ingredients), diet=p.declared_diet)
            )
        else:
            # External mocked or live calls
            if client is not None:
                try:
                    ver_ev = await verify_dish_composition(p.name, region, client, vocab)
                    evs.append(ver_ev)
                except Exception as e:
                    logger.debug("Verifier lookup error for %s: %s", p.name, e)
                try:
                    web_evs = await fetch_web_evidence(p.name, region, client)
                    evs.extend(web_evs)
                except Exception as e:
                    logger.debug("Web evidence error for %s: %s", p.name, e)
            evs.append(
                Evidence(source="proposer", ingredients=tuple(p.ingredients), diet=p.declared_diet)
            )
        evidence_by_item[p.name] = evs

    yield ("stage", {"stage": "grounding", "message": "Grounding items against rules..."})
    policy_cfg = data.sources.get("policy", {})
    ground_policy = GroundingPolicy(
        quorum=int(policy_cfg.get("quorum", 2)),
        min_agreement=float(policy_cfg.get("min_agreement", 0.67)),
        require_non_model=bool(policy_cfg.get("require_non_model", True)),
    )

    allowed_items: list[tuple[ProposedItemProposal, GroundedItem]] = []
    needs_confirm_items: list[tuple[ProposedItemProposal, GroundedItem]] = []
    removed_items: list[RemovedItem] = []

    for p in proposals:
        item_ev = evidence_by_item.get(p.name, [])
        grounded = ground(
            item=ProposedItem(name=p.name, category=p.category),
            evidence=item_ev,
            vocabulary=vocab,
            rule_ids=rule_ids,
            rules=data.rules,
            confirm=data.confirm,
            semantics=data.semantics,
            policy=ground_policy,
        )
        if grounded.status == "allowed":
            allowed_items.append((p, grounded))
        elif grounded.status == "needs_confirmation":
            needs_confirm_items.append((p, grounded))
        else:
            clean_reasons = list(grounded.reasons)
            removed_items.append(
                RemovedItem(
                    item_id=re.sub(r"[^a-z0-9_]+", "_", p.name.lower()),
                    name=p.name,
                    reason=clean_reasons[0] if clean_reasons else "Disallowed by active rules",
                    reasons=clean_reasons,
                    citations=list(grounded.citations),
                )
            )

    yield ("stage", {"stage": "quantities", "message": "Sizing portions and packs..."})
    plan_items: list[PlanItem] = []
    needs_conf_items: list[PlanItem] = []

    def _build_item(p: ProposedItemProposal, g: GroundedItem) -> PlanItem:
        sk = _get_serving_kind(p.serving_kind, p.course, p.category)
        if sk.per_guests:
            needed = float(units_per_guests(sk, headcount.adults + headcount.children))
            per_head_str = f"1 unit per {sk.per_guests} guests"
        else:
            c_row = consumption_row(sk, p.name, meal)
            cost_short, cost_waste = slider_to_cost_ratio(short_waste_slider)
            cr = critical_ratio(cost_short, cost_waste)
            alpha_val = float(get_tuning_value("elasticity_prior_mean", 0.65))
            tot_guests = max(1, headcount.adults + headcount.children)
            samples = arrival_samples or [tot_guests] * 50
            demand_samples = [float(arr) * c_row.per_adult * (1.0**-alpha_val) for arr in samples]
            needed = order_quantity(demand_samples, cost_short, cost_waste)
            per_head_str = f"Sized via Newsvendor (ratio {cr:.2f}) from arrivals, {sk.basis}"

        pack_plan = round_to_packs(needed, sk.pack_sizes)
        packs_out = [PackOut(size=ps, count=cnt) for ps, cnt in pack_plan.packs]

        quotes = [
            PriceQuote(
                "ai_estimate", p.price_estimate[0], p.price_estimate[1], today_dt, detail="estimate"
            )
        ]
        pc = choose_price(quotes, today_dt)
        unit_mid = pc.mid if pc else 0.4
        est_cost = round(pack_plan.total * unit_mid)

        buy_opts = build_buy_options_for_item(
            p.category, p.search_query or p.name, days_left, data.channels
        )
        offers_list = build_offers_for_item(
            p.category, days_left, data.channels, search_query=p.search_query or p.name
        )

        return PlanItem(
            item_id=re.sub(r"[^a-z0-9_]+", "_", p.name.lower()),
            name=p.name,
            category=p.category,
            course=p.course,
            unit=sk.unit,
            needed=round(needed, 1),
            packs=packs_out,
            buy_total=pack_plan.total,
            est_cost_inr=est_cost,
            per_head_basis=per_head_str,
            allergen_warnings=[],
            offers=offers_list,
            status=g.status,
            serving_kind=sk.id,
            why=p.why or f"Traditional {p.course} item",
            grounding=GroundingInfo(
                supporting=list(g.supporting),
                citations=list(g.citations),
                reasons=list(g.reasons),
                matched=list(g.matched),
            ),
            price=PriceInfo(
                source=pc.source if pc else "ai_estimate",
                low=pc.low if pc else 0.3,
                mid=pc.mid if pc else unit_mid,
                high=pc.high if pc else 0.6,
                observed_on=str(today_dt),
                detail="model",
            ),
            options=buy_opts,
            alternatives=p.alternatives,
        )

    for p, g in allowed_items:
        plan_items.append(_build_item(p, g))
    for p, g in needs_confirm_items:
        needs_conf_items.append(_build_item(p, g))

    # Stream by course for category event
    by_course: dict[str, list[PlanItem]] = {}
    for it in plan_items:
        by_course.setdefault(it.course, []).append(it)
    for c_name, c_items in by_course.items():
        yield (
            "category",
            {
                "course": c_name,
                "items": [it.model_dump(by_alias=True, mode="json") for it in c_items],
            },
        )

    yield ("stage", {"stage": "budget", "message": "Fitting budget..."})
    total_cost = sum(it.est_cost_inr for it in plan_items)
    budget_fit = PlanBudget(
        limit_inr=budget_inr,
        total_inr=total_cost,
        fits=total_cost <= budget_inr,
        changes=[],
    )

    yield ("stage", {"stage": "complete", "message": "Complete"})

    plan_obj = Plan(
        plan_id=plan_id,
        function_id=function_id,
        headcount=headcount,
        rules=rules_list,
        items=plan_items,
        needs_confirmation=needs_conf_items,
        removed=removed_items,
        budget=budget_fit,
        route=route_name,
        explanation=build_template_explanations(headcount, plan_items),
        data_version=data.version,
        created_at=now,
    )
    yield ("done", plan_obj)


async def run_planner_stream(
    session: AsyncSession,
    plan_id: str,
    router: LlmRouter,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream plan generation using execute_planner_steps and save result to DB."""
    plan_row = await session.get(PlanModel, plan_id)
    if plan_row is None:
        raise ValueError(f"Plan {plan_id} not found")
    fn = await session.get(Fn, plan_row.function_id)
    if fn is None:
        raise ValueError(f"Function {plan_row.function_id} not found")
    event = await session.get(Event, fn.event_key)
    if event is None:
        raise ValueError(f"Event {fn.event_key} not found")

    summary = await get_event_summary(session, event.event_key)
    fn_sum = next((f for f in summary.functions if f.function_id == fn.id), None)
    likely = fn_sum.prediction.likely if fn_sum and fn_sum.prediction.likely > 0 else 50
    p80 = fn_sum.prediction.p80 if fn_sum and fn_sum.prediction.p80 > 0 else 60

    adults = int(likely * 0.8)
    children = max(0, likely - adults)
    headcount = PlanHeadcount(
        adults=adults,
        children=children,
        veg_seats=fn_sum.meals.veg if fn_sum else 60,
        jain_seats=fn_sum.meals.jain if fn_sum else 10,
        non_veg_seats=fn_sum.meals.non_veg if fn_sum else 30,
    )

    hh_names = list(
        (
            await session.scalars(
                select(Household.display_name).where(Household.event_key == event.event_key)
            )
        ).all()
    )
    rules_list = [RuleId(r) for r in fn.rules if r in {x.value for x in RuleId}]

    pincode = event.pincode or ""
    region = (
        "West Bengal / Kolkata"
        if pincode.startswith("7")
        else ("Tamil Nadu / Chennai" if pincode.startswith("6") else "India")
    )

    final_plan: Plan | None = None
    async for evt_name, payload in execute_planner_steps(
        plan_id=plan_id,
        function_id=fn.id,
        event_type=event.type,
        meal=cast(Meal, fn.meal if fn.meal in ("lunch", "dinner", "snacks") else "dinner"),
        budget_inr=event.budget_inr,
        days_left=compute_days_left(fn.starts_at),
        likely=likely,
        p80=p80,
        headcount=headcount,
        rules_list=rules_list,
        allergies=[],
        hh_names=hh_names,
        brief=(plan_row.request.get("brief", "") if isinstance(plan_row.request, dict) else "")
        or "",
        router=router,
        region=region,
        short_waste_slider=event.short_waste_slider,
    ):
        if evt_name == "done" and isinstance(payload, Plan):
            final_plan = payload
        yield (evt_name, payload)

    if final_plan:
        plan_row.body = final_plan.model_dump(by_alias=True, mode="json")
        plan_row.status = "done"
        plan_row.route = final_plan.route
        session.add(
            EventLog(
                event_key=event.event_key,
                kind="plan.generated",
                payload={"plan_id": plan_id, "items": len(final_plan.items)},
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def generate_plan_stream(
    session: AsyncSession,
    event_key: str,
    request: PlanRequest,
    router: LlmRouter,
) -> AsyncIterator[dict[str, Any]]:
    """Legacy helper: stream as dicts for API response."""
    fn_res = await session.execute(
        select(Fn).where(Fn.id == request.function_id, Fn.event_key == event_key)
    )
    fn = fn_res.scalar_one_or_none()
    if not fn:
        raise ValueError("Function not found")
    plan_id = f"PLN-{uuid4().hex[:10].upper()}"

    async for evt_name, payload in execute_planner_steps(
        plan_id=plan_id,
        function_id=fn.id,
        event_type="wedding",
        meal=cast(Meal, fn.meal if fn.meal in ("lunch", "dinner", "snacks") else "dinner"),
        budget_inr=500000,
        days_left=request.days_left or 7,
        likely=100,
        p80=115,
        headcount=PlanHeadcount(
            adults=80, children=20, veg_seats=50, jain_seats=10, non_veg_seats=40
        ),
        rules_list=[RuleId(r) for r in fn.rules if r in {x.value for x in RuleId}],
        allergies=[],
        hh_names=[],
        brief=request.brief,
        router=router,
    ):
        if evt_name == "stage":
            yield {"stage": payload.get("stage", ""), "message": payload.get("message", "")}
        elif evt_name == "done" and isinstance(payload, Plan):
            yield {"event": "plan", "plan": payload.model_dump(by_alias=True, mode="json")}
