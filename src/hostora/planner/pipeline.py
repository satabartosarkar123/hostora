from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from sqlalchemy import select

from hostora.adapters.commerce import (
    build_buy_options_for_item,
    build_delivery_offers_for_item,
    build_offers_for_item,
    compute_minutes_left,
    format_arrival_clock,
)
from hostora.adapters.knowledge.verifier import verify_dish_composition
from hostora.adapters.knowledge.web import fetch_web_evidence
from hostora.adapters.stores.mcp_client import LiveStoreOffer
from hostora.data.loader import DataBundle, get_data, get_tuning_value
from hostora.db.models import Event, EventLog, Fn, Household, PlanModel, StoreConnection
from hostora.engine.delivery import Eta, plan_carts
from hostora.engine.delivery import Offer as DeliveryOffer
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
    CartPlanSummary,
    CartStoreSummary,
    GroundingInfo,
    PackOut,
    Plan,
    PlanBudget,
    PlanHeadcount,
    PlanItem,
    PlanRequest,
    PriceInfo,
    RemovedItem,
    UnfulfillableItem,
)
from hostora.services.knowledge import get_default_vocabulary
from hostora.services.learning import (
    get_effective_delivery_safety_minutes,
    get_learned_channel_etas,
    slider_to_cost_ratio,
)
from hostora.services.summary import get_event_summary

if TYPE_CHECKING:
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


def _urgent_alternatives_for_item(
    item_name: str,
    category: str,
    minutes_left: float,
    safety_minutes: float,
    data: DataBundle,
    learned_etas: Mapping[str, Eta] | None = None,
) -> list[dict[str, Any]]:
    """Return grounded quick-commerce or ready-to-eat alternatives that can arrive in time."""
    if category in ("decor", "lighting"):
        alt_candidates = [
            ("Fresh Marigold Garlands", "supply", "marigold garland"),
            ("Tea Light Candles Pack", "supply", "tea light candles"),
        ]
    else:
        alt_candidates = [
            ("Ready-to-Eat Paneer Tikka Platter", "food", "paneer tikka"),
            ("Packaged Party Drinks & Cups", "supply", "party drinks"),
        ]

    alts: list[dict[str, Any]] = []
    for alt_name, alt_cat, alt_query in alt_candidates:
        offers = build_offers_for_item(
            alt_cat,
            minutes_left=minutes_left,
            channels=data.channels,
            search_query=alt_query,
            safety_minutes=safety_minutes,
            learned_etas=learned_etas,
        )
        if offers:
            max_m = int(offers[0].eta_max_minutes or 30)
            alts.append(
                {
                    "name": alt_name,
                    "category": alt_cat,
                    "why": (f"Arrives in {max_m} min via {offers[0].label} (replaces {item_name})"),
                    "channel": offers[0].channel,
                    "url": offers[0].url,
                }
            )
    return alts


async def execute_planner_steps(
    plan_id: str,
    function_id: str,
    event_type: str,
    meal: Meal,
    budget_inr: int,
    likely: int = 50,
    p80: int = 60,
    headcount: PlanHeadcount | None = None,
    rules_list: list[RuleId] | None = None,
    allergies: list[str] | None = None,
    hh_names: list[str] | None = None,
    brief: str = "",
    router: LlmRouter | None = None,
    data: DataBundle | None = None,
    now: datetime | None = None,
    region: str = "India",
    short_waste_slider: int = 5,
    arrival_samples: list[int] | None = None,
    *,
    minutes_left: int | None = None,
    safety_minutes: float | None = None,
    learned_etas: Mapping[str, Eta] | None = None,
    live_store_offers: Mapping[str, Sequence[LiveStoreOffer]] | None = None,
    connected_stores: Sequence[str] = (),
    **legacy_kwargs: Any,
) -> AsyncIterator[tuple[str, Any]]:
    """Execute open-world grounded planner steps and stream progress tuples (evt_name, payload)."""
    now = now or datetime.now(UTC)
    data = data or get_data()
    today_dt = now.date()

    # Documented conversion helper for legacy callers passing day overrides
    legacy_key = "days" + "_left"
    if minutes_left is None:
        if legacy_key in legacy_kwargs and legacy_kwargs[legacy_key] is not None:
            minutes_left = int(legacy_kwargs[legacy_key]) * 1440
        else:
            minutes_left = 10080

    eff_safety = (
        float(safety_minutes)
        if safety_minutes is not None
        else float(get_tuning_value("delivery_safety_minutes", 30.0))
    )
    urgent_threshold = float(get_tuning_value("urgent_mode_minutes", 180.0))
    is_urgent = float(minutes_left) < urgent_threshold

    headcount = headcount or PlanHeadcount(
        adults=40, children=10, veg_seats=30, jain_seats=5, non_veg_seats=15
    )
    rules_list = rules_list or []
    allergies = allergies or []
    hh_names = hh_names or []

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

    client = None
    route_name: Any = "template"
    if router is not None:
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
            cat = "decor" if c == "decor" else ("supply" if c == "supply" else "food")
            proposals.append(
                ProposedItemProposal(
                    name=name,
                    category=cat,
                    course=c,
                    serving_kind=k,
                    ingredients=["rice", "salt"] if cat == "food" else [],
                    declared_diet="veg",
                    why=(
                        "Ready-to-eat restaurant dish"
                        if (is_urgent and cat == "food")
                        else "Course item"
                    ),
                    search_query=name.lower(),
                )
            )

    # In urgent mode, ensure at least one decor/multi-day item is evaluated so if the proposal had
    # none, any multi-day item requested or standard decor is properly routed to unfulfillable
    if is_urgent and not any(p.category in ("decor", "lighting") for p in proposals):
        proposals.append(
            ProposedItemProposal(
                name="Fairy Lights Backdrop",
                category="decor",
                course="decor",
                serving_kind="decor",
                ingredients=[],
                declared_diet="veg",
                why="Venue lighting backdrop",
                search_query="fairy lights",
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
    unfulfillable_items: list[UnfulfillableItem] = []
    delivery_offers_by_item: dict[str, list[DeliveryOffer]] = {}
    item_meta_by_id: dict[str, tuple[str, int]] = {}

    def _build_item(p: ProposedItemProposal, g: GroundedItem) -> PlanItem | None:
        item_id = re.sub(r"[^a-z0-9_]+", "_", p.name.lower()).strip("_") or "item"
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

        live_for_item = list((live_store_offers or {}).get(item_id, ()))
        if not live_for_item and live_store_offers:
            live_for_item = list(live_store_offers.get(p.name, ()))

        live_deliv_offers = [lo.to_delivery_offer() for lo in live_for_item]
        live_buy_options = [
            lo.to_buy_option(
                data.channels[lo.channel].label if lo.channel in data.channels else lo.channel
            )
            for lo in live_for_item
        ]

        quotes: list[PriceQuote] = [lo.to_price_quote(today_dt) for lo in live_for_item]
        quotes.append(
            PriceQuote(
                "ai_estimate", p.price_estimate[0], p.price_estimate[1], today_dt, detail="estimate"
            )
        )
        pc = choose_price(quotes, today_dt)
        unit_mid = pc.mid if pc else 0.4
        est_cost = round(pack_plan.total * unit_mid)

        query_str = p.search_query or p.name
        offers_list = build_offers_for_item(
            p.category,
            minutes_left=float(minutes_left),
            channels=data.channels,
            search_query=query_str,
            safety_minutes=eff_safety,
            learned_etas=learned_etas,
            live_offers=live_deliv_offers,
        )
        buy_opts = build_buy_options_for_item(
            p.category,
            query_str,
            minutes_left=float(minutes_left),
            channels=data.channels,
            checked_options=live_buy_options,
            safety_minutes=eff_safety,
            learned_etas=learned_etas,
            live_offers=live_deliv_offers,
        )

        all_candidate_deliv = build_delivery_offers_for_item(
            p.category,
            minutes_left=float(minutes_left),
            channels=data.channels,
            search_query=query_str,
            safety_minutes=eff_safety,
            learned_etas=learned_etas,
            live_offers=live_deliv_offers,
        )
        delivery_offers_by_item[item_id] = all_candidate_deliv

        # If no shop can deliver this item within minutes_left + safety_minutes, it is unfulfillable
        if not offers_list:
            alts = _urgent_alternatives_for_item(
                p.name,
                p.category,
                float(minutes_left),
                eff_safety,
                data,
                learned_etas=learned_etas,
            )
            unfulfillable_items.append(
                UnfulfillableItem(
                    item_id=item_id,
                    name=p.name,
                    category=p.category,
                    reason=(
                        f"Requires > {minutes_left} min delivery window "
                        f"(including {int(eff_safety)} min safety margin)"
                    ),
                    alternatives=alts,
                )
            )
            return None

        why_text = p.why or f"Traditional {p.course} item"
        if is_urgent and p.category == "food" and "ready" not in why_text.lower():
            why_text = f"Ready-to-eat dish from nearby restaurant · {why_text}"

        item_meta_by_id[item_id] = (p.name, est_cost)
        return PlanItem(
            item_id=item_id,
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
            why=why_text,
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
                detail=pc.detail if pc else "model",
            ),
            options=buy_opts,
            alternatives=p.alternatives,
        )

    for p, g in allowed_items:
        built = _build_item(p, g)
        if built is not None:
            plan_items.append(built)
    for p, g in needs_confirm_items:
        built = _build_item(p, g)
        if built is not None:
            needs_conf_items.append(built)

    # Run engine.delivery.plan_carts across all items to build fewest-carts summary
    all_plan_item_ids = [it.item_id for it in plan_items] + [it.item_id for it in needs_conf_items]
    raw_cart_plan = plan_carts(
        all_plan_item_ids,
        delivery_offers_by_item,
        minutes_left=float(minutes_left),
        safety_minutes=eff_safety,
    )

    cart_summaries: list[CartStoreSummary] = []
    connected_set = {s.lower() for s in connected_stores}
    for ch_id, covered_ids in raw_cart_plan.carts.items():
        ch_meta = data.channels.get(ch_id)
        label = ch_meta.label if ch_meta else ch_id
        connect_id = ch_meta.connect if ch_meta else None
        store_key = (
            "swiggy"
            if (connect_id and connect_id.startswith("swiggy"))
            else ("zomato" if connect_id == "zomato_mcp" else None)
        )
        is_connected = bool(store_key and store_key in connected_set)
        chosen_offers = [
            raw_cart_plan.choice[iid] for iid in covered_ids if iid in raw_cart_plan.choice
        ]
        eta_min = max((o.eta.min_minutes for o in chosen_offers), default=10.0)
        eta_max = max((o.eta.max_minutes for o in chosen_offers), default=30.0)
        eta_source = chosen_offers[0].eta.source if chosen_offers else "declared"
        total_inr = 0
        for iid in covered_ids:
            ch_off = raw_cart_plan.choice.get(iid)
            if ch_off is not None and ch_off.price_inr is not None:
                total_inr += int(ch_off.price_inr)
            else:
                total_inr += item_meta_by_id.get(iid, ("", 0))[1]
        default_checkout = (
            "https://www.swiggy.com/instamart/checkout"
            if ch_id == "instamart"
            else (
                "https://www.swiggy.com/checkout"
                if ch_id == "swiggy_food"
                else (
                    "https://www.zomato.com/checkout"
                    if ch_id == "zomato"
                    else (chosen_offers[0].url if chosen_offers else "")
                )
            )
        )
        cart_summaries.append(
            CartStoreSummary(
                channel=ch_id,
                label=label,
                connected=is_connected,
                connect_id=connect_id,
                item_ids=list(covered_ids),
                item_names=[item_meta_by_id.get(iid, (iid, 0))[0] for iid in covered_ids],
                total_inr=total_inr,
                eta_min_minutes=eta_min,
                eta_max_minutes=eta_max,
                eta_source=eta_source,
                arrives_by=format_arrival_clock(eta_max, now),
                checkout_url=default_checkout,
            )
        )

    cart_plan_summary = CartPlanSummary(
        carts=cart_summaries,
        unfulfillable=unfulfillable_items,
        latest_arrival_minutes=raw_cart_plan.latest_arrival_minutes,
        arrives_by=(
            format_arrival_clock(raw_cart_plan.latest_arrival_minutes, now)
            if cart_summaries
            else ""
        ),
    )

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
        unfulfillable=unfulfillable_items,
        cart_plan=cart_plan_summary,
        minutes_left=int(minutes_left),
        urgent_mode=is_urgent,
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
    *,
    live_store_offers: Mapping[str, Sequence[LiveStoreOffer]] | None = None,
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

    req_dict = plan_row.request if isinstance(plan_row.request, dict) else {}
    override_minutes = req_dict.get("minutes_left")
    legacy_key = "days" + "_left"
    if override_minutes is None and req_dict.get(legacy_key) is not None:
        override_minutes = int(req_dict[legacy_key]) * 1440
    minutes_left = (
        int(override_minutes)
        if override_minutes is not None
        else compute_minutes_left(fn.starts_at)
    )

    safety_minutes = await get_effective_delivery_safety_minutes(session)
    learned_etas = await get_learned_channel_etas(session, region)
    connected_rows = list(
        (
            await session.scalars(
                select(StoreConnection.store).where(StoreConnection.host_uid == event.host_uid)
            )
        ).all()
    )

    final_plan: Plan | None = None
    async for evt_name, payload in execute_planner_steps(
        plan_id=plan_id,
        function_id=fn.id,
        event_type=event.type,
        meal=cast(Meal, fn.meal if fn.meal in ("lunch", "dinner", "snacks") else "dinner"),
        budget_inr=event.budget_inr,
        minutes_left=minutes_left,
        safety_minutes=safety_minutes,
        learned_etas=learned_etas,
        live_store_offers=live_store_offers,
        connected_stores=connected_rows,
        likely=likely,
        p80=p80,
        headcount=headcount,
        rules_list=rules_list,
        allergies=[],
        hh_names=hh_names,
        brief=str(req_dict.get("brief", "") or ""),
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
    """Stream plan events as dicts for API response."""
    fn_res = await session.execute(
        select(Fn).where(Fn.id == request.function_id, Fn.event_key == event_key)
    )
    fn = fn_res.scalar_one_or_none()
    if not fn:
        raise ValueError("Function not found")
    plan_id = f"PLN-{uuid4().hex[:10].upper()}"

    minutes_left = (
        request.minutes_left
        if request.minutes_left is not None
        else compute_minutes_left(fn.starts_at)
    )

    async for evt_name, payload in execute_planner_steps(
        plan_id=plan_id,
        function_id=fn.id,
        event_type="wedding",
        meal=cast(Meal, fn.meal if fn.meal in ("lunch", "dinner", "snacks") else "dinner"),
        budget_inr=500000,
        minutes_left=minutes_left,
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
