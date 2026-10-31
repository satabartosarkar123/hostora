from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from hostora.adapters.llm.groq import GroqClient
from hostora.adapters.llm.mock import MockLlmClient
from hostora.adapters.llm.router import LlmRouter
from hostora.data.loader import get_data
from hostora.planner.pipeline import execute_planner_steps
from hostora.planner.redact import assert_clean
from hostora.schemas.common import RuleId
from hostora.schemas.plan import Plan, PlanHeadcount
from hostora.services.knowledge import get_default_vocabulary

if TYPE_CHECKING:
    from hostora.adapters.llm.base import Route


class SpyScriptedClient:
    """Mock LLM client that records all prompts and returns scripted adversarial picks."""

    name: Route = "groq"

    def __init__(self, picks: dict[str, list[str]]) -> None:
        self.picks = picks
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, prompt: str, system: str = "", temperature: float = 0.0) -> str:
        _ = temperature
        self.prompts.append((system, prompt))
        prompt_lower = prompt.lower()
        if "what is usually in" in prompt_lower:
            for k in ("pork", "beef", "chicken", "mutton", "fish", "wine"):
                if k in prompt_lower:
                    return json.dumps({"ingredients": [k, "spices"], "diet": "nonveg"})
            return json.dumps({"ingredients": ["rice", "water"], "diet": "veg"})
        return json.dumps(
            {
                "ingredients": ["rice", "water", "salt"],
                "sources": [
                    {"url": "https://example.org/recipe", "quote": "Cook rice in water with salt."}
                ],
            }
        )

    async def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        _ = max_tokens
        self.prompts.append((system, user))
        if "explanation" in system.lower():
            return json.dumps({"explanations": {"main": "Sized for guests."}})
        full_picks = {
            "starter": self.picks.get("starter", ["hara_bhara_kebab"]),
            "main": self.picks.get("main", ["veg_biryani", "jain_veg_pulao"]),
            "rice": self.picks.get("rice", ["jeera_rice"]),
            "bread": self.picks.get("bread", ["butter_naan"]),
            "side": self.picks.get("side", ["jain_paneer_masala"]),
            "dessert": self.picks.get("dessert", ["gulab_jamun"]),
            "drink": self.picks.get("drink", ["mineral_water", "fresh_lime_soda"]),
            "supply": self.picks.get("supply", ["paper_plates", "ice"]),
            "decor": self.picks.get("decor", ["fairy_lights"]),
        }
        items = []
        for course, picks in full_picks.items():
            for p in picks:
                name = p.replace("_", " ").title()
                diet = (
                    "nonveg"
                    if any(x in p for x in ("pork", "beef", "chicken", "mutton", "fish"))
                    else "veg"
                )
                items.append(
                    {
                        "name": name,
                        "category": "beverage"
                        if course == "drink"
                        else ("supply" if course in ("supply", "decor") else "food"),
                        "course": course,
                        "serving_kind": "main_gravy"
                        if course in ("main", "side")
                        else ("starter_piece" if course == "starter" else "rice_main"),
                        "ingredients": [p.replace("_", " ")],
                        "declared_diet": diet,
                        "why": f"Scripted {course} pick",
                        "search_query": p.replace("_", " "),
                        "price_estimate": [0.3, 0.5],
                        "alternatives": [],
                    }
                )
        return json.dumps({"items": items, **full_picks})


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


@pytest.mark.asyncio
async def test_realistic_briefs_eval_set() -> None:
    briefs = _load_jsonl(Path("data/evals/briefs.jsonl"))
    assert len(briefs) >= 50

    data = get_data()
    now = datetime.now(UTC)

    for case in briefs:
        case_id = str(case["id"])
        rule_strs = [str(r) for r in case["rules"]]  # type: ignore[attr-defined]
        rules_list = [RuleId(r) for r in rule_strs]
        budget_inr = int(case["budget_inr"])  # type: ignore[call-overload]
        brief = str(case["brief"])
        meal = str(case["meal"])
        event_type = str(case["event_type"])

        router = LlmRouter([MockLlmClient()])
        headcount = PlanHeadcount(
            adults=80,
            children=10,
            veg_seats=90 if ("pure_veg" in rule_strs or "vegan" in rule_strs) else 45,
            jain_seats=90 if "jain" in rule_strs else 0,
            non_veg_seats=0 if any(r in ("pure_veg", "vegan", "jain") for r in rule_strs) else 45,
        )

        final_plan: Plan | None = None
        async for ev_name, payload in execute_planner_steps(
            plan_id=f"pl_{case_id}",
            function_id=f"fn_{case_id}",
            event_type=event_type,
            meal=meal,
            budget_inr=budget_inr,
            days_left=5,
            likely=90,
            p80=102,
            headcount=headcount,
            rules_list=rules_list,
            allergies=[],
            hh_names=["Sharma Family", "Khan Household"],
            brief=brief,
            router=router,
            data=data,
            now=now,
        ):
            if ev_name == "done" and isinstance(payload, Plan):
                final_plan = payload

        assert final_plan is not None, f"No plan produced for {case_id}"
        assert len(final_plan.items) > 0, f"Empty items for {case_id}"

        # Safety invariant: every item in plan.items must pass active rules
        vocab = get_default_vocabulary()
        forbidden_tags = {tag for r in rule_strs if r in data.rules for tag in data.rules[r].forbid}
        for it in final_plan.items:
            scan = vocab.scan(it.name)
            assert not (scan.tags & forbidden_tags), (
                f"Rule violation in {case_id}: {it.name} has {scan.tags & forbidden_tags}"
            )


@pytest.mark.asyncio
async def test_adversarial_safety_and_privacy_eval_set() -> None:
    adversarial = _load_jsonl(Path("data/evals/adversarial.jsonl"))
    assert len(adversarial) >= 100

    data = get_data()
    now = datetime.now(UTC)
    default_hh_names = [
        "Sharma Family",
        "Khan Household",
        "Iyer Parivar",
        "Fernandes Family",
        "Patel Kutumb",
        "Gupta Residence",
        "Mukherjee Bari",
        "Dsouza Villa",
        "Mehta Ghar",
        "Reddy Niwas",
        "Ansari Manzil",
        "Joshi Family",
        "Nair Tharavadu",
        "Oswal Parivar",
        "Kapoor Family",
        "Syed Household",
        "Chatterjee Family",
        "Gill Family",
        "Shah Kutumb",
        "Qureshi Family",
        "Agarwal Parivar",
        "Menon Family",
        "Thomas Family",
        "Mirza Household",
        "Verma Family",
    ]

    for case in adversarial:
        case_id = str(case["id"])
        rule_strs = [str(r) for r in case["rules"]]  # type: ignore[attr-defined]
        rules_list = [RuleId(r) for r in rule_strs]
        brief = str(case["brief"])
        allergies = [str(a) for a in case.get("allergies", [])]  # type: ignore[attr-defined]
        mock_picks = case["mock_picks"]
        pii_tokens = [str(t) for t in case.get("pii_tokens", [])]  # type: ignore[attr-defined]

        spy = SpyScriptedClient(picks=mock_picks)  # type: ignore[arg-type]
        router = LlmRouter([spy])

        headcount = PlanHeadcount(
            adults=50,
            children=5,
            veg_seats=55 if ("pure_veg" in rule_strs or "vegan" in rule_strs) else 25,
            jain_seats=55 if "jain" in rule_strs else 0,
            non_veg_seats=0 if any(r in ("pure_veg", "vegan", "jain") for r in rule_strs) else 30,
        )

        final_plan: Plan | None = None
        async for ev_name, payload in execute_planner_steps(
            plan_id=f"pl_{case_id}",
            function_id=f"fn_{case_id}",
            event_type="wedding",
            meal="dinner",
            budget_inr=60000,
            days_left=3,
            likely=55,
            p80=62,
            headcount=headcount,
            rules_list=rules_list,
            allergies=allergies,
            hh_names=default_hh_names,
            brief=brief,
            router=router,
            data=data,
            now=now,
        ):
            if ev_name == "done" and isinstance(payload, Plan):
                final_plan = payload

        assert final_plan is not None, f"No plan produced for {case_id}"

        # 1. Zero forbidden items in final_plan.items
        vocab = get_default_vocabulary()
        forbidden_tags = {tag for r in rule_strs if r in data.rules for tag in data.rules[r].forbid}
        for it in final_plan.items:
            scan = vocab.scan(it.name)
            assert not (scan.tags & forbidden_tags), (
                f"Safety violation in {case_id}: {it.name} has {scan.tags & forbidden_tags}"
            )

        # 2. Zero PII in any prompt sent to the LLM client
        assert len(spy.prompts) > 0, f"Spy client was not called for {case_id}"
        for _sys_prompt, user_prompt in spy.prompts:
            assert assert_clean(user_prompt, forbidden_terms=default_hh_names), (
                f"PII leaked in prompt for {case_id}: {user_prompt}"
            )
            for token in pii_tokens:
                assert token.lower() not in user_prompt.lower(), (
                    f"PII token {token!r} leaked in prompt for {case_id}"
                )


@pytest.mark.llm_live
@pytest.mark.asyncio
async def test_live_llm_safety_eval_subset() -> None:
    from hostora.config import get_settings

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        try:
            s = get_settings()
            if s.groq_api_key is not None:
                api_key = s.groq_api_key.get_secret_value().strip()
        except Exception:
            api_key = ""
    if not api_key:
        pytest.skip("GROQ_API_KEY is not set for llm_live test")

    adversarial = _load_jsonl(Path("data/evals/adversarial.jsonl"))[:10]
    data = get_data()
    now = datetime.now(UTC)
    s = get_settings()
    router = LlmRouter([GroqClient(api_key=api_key, model=s.groq_model_large)])

    for case in adversarial:
        case_id = str(case["id"])
        rule_strs = [str(r) for r in case["rules"]]  # type: ignore[attr-defined]
        rules_list = [RuleId(r) for r in rule_strs]
        brief = str(case["brief"])
        headcount = PlanHeadcount(
            adults=40, children=5, veg_seats=25, jain_seats=0, non_veg_seats=20
        )

        final_plan: Plan | None = None
        async for ev_name, payload in execute_planner_steps(
            plan_id=f"pl_live_{case_id}",
            function_id=f"fn_live_{case_id}",
            event_type="wedding",
            meal="dinner",
            budget_inr=60000,
            days_left=3,
            likely=45,
            p80=52,
            headcount=headcount,
            rules_list=rules_list,
            allergies=[],
            hh_names=["Sharma Family", "Khan Household"],
            brief=brief,
            router=router,
            data=data,
            now=now,
        ):
            if ev_name == "done" and isinstance(payload, Plan):
                final_plan = payload

        assert final_plan is not None
        vocab = get_default_vocabulary()
        forbidden_tags = {tag for r in rule_strs if r in data.rules for tag in data.rules[r].forbid}
        for it in final_plan.items:
            scan = vocab.scan(it.name)
            assert not (scan.tags & forbidden_tags), (
                f"Safety violation in {case_id}: {it.name} has {scan.tags & forbidden_tags}"
            )


@pytest.mark.asyncio
async def test_planner_fallback_to_template_when_no_llm_key() -> None:
    """T5 #3: Planner with empty router/no keys falls back to deterministic template route."""
    data = get_data()
    now = datetime.now(UTC)
    router = LlmRouter([])  # Empty client list simulates no LLM API keys configured

    headcount = PlanHeadcount(
        adults=50,
        children=5,
        veg_seats=30,
        jain_seats=0,
        non_veg_seats=25,
    )

    final_plan: Plan | None = None
    async for ev_name, payload in execute_planner_steps(
        plan_id="pl_fallback_test",
        function_id="fn_fallback_test",
        event_type="wedding",
        meal="dinner",
        budget_inr=50000,
        days_left=3,
        likely=55,
        p80=62,
        headcount=headcount,
        rules_list=[RuleId.HALAL],
        allergies=[],
        hh_names=["Sharma"],
        brief="halal dinner reception",
        router=router,
        data=data,
        now=now,
    ):
        if ev_name == "done" and isinstance(payload, Plan):
            final_plan = payload

    assert final_plan is not None
    assert final_plan.route in ("template", "manual")
    assert len(final_plan.items) > 0
    # Every item must pass rules
    vocab = get_default_vocabulary()
    forbidden_tags = {tag for r in ["halal"] if r in data.rules for tag in data.rules[r].forbid}
    for it in final_plan.items:
        scan = vocab.scan(it.name)
        assert not (scan.tags & forbidden_tags)


@pytest.mark.llm_live
@pytest.mark.asyncio
async def test_live_llm_realistic_briefs_diverse_traditions() -> None:
    """T5 #4: 10 realistic briefs (Hindu, Muslim, Christian, Sikh, Jain, interfaith, birthday)

    run through the real Groq planner; every plan passes apply_rules.
    """
    from hostora.config import get_settings

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        try:
            s = get_settings()
            if s.groq_api_key is not None:
                api_key = s.groq_api_key.get_secret_value().strip()
        except Exception:
            api_key = ""
    if not api_key:
        pytest.skip("GROQ_API_KEY is not set for llm_live test")

    diverse_cases = [
        ("Hindu wedding reception", "wedding", "dinner", ["pure_veg"], 80000),
        ("Muslim walima dinner", "wedding", "dinner", ["halal", "no_alcohol"], 90000),
        ("Christian wedding lunch", "wedding", "lunch", ["no_pork"], 65000),
        ("Sikh anand karaj lunch", "wedding", "lunch", ["pure_veg", "no_alcohol"], 70000),
        ("Jain wedding dinner", "wedding", "dinner", ["jain", "no_alcohol"], 75000),
        ("Interfaith dinner halal + veg", "wedding", "dinner", ["halal"], 85000),
        ("Kids 10th birthday party", "birthday", "snacks", ["eggless"], 30000),
        ("Family anniversary dinner", "anniversary", "dinner", ["no_beef", "no_pork"], 50000),
        ("Housewarming puja lunch", "housewarming", "lunch", ["pure_veg"], 40000),
        ("Office Diwali party dinner", "office", "dinner", ["no_alcohol"], 60000),
    ]

    data = get_data()
    now = datetime.now(UTC)
    s = get_settings()
    router = LlmRouter([GroqClient(api_key=api_key, model=s.groq_model_large)])

    for idx, (brief, ev_type, meal, rules_strs, budget) in enumerate(diverse_cases, start=1):
        rules_list = [RuleId(r) for r in rules_strs]
        headcount = PlanHeadcount(
            adults=40,
            children=5,
            veg_seats=45 if "pure_veg" in rules_strs or "jain" in rules_strs else 25,
            jain_seats=45 if "jain" in rules_strs else 0,
            non_veg_seats=0 if "pure_veg" in rules_strs or "jain" in rules_strs else 20,
        )

        final_plan: Plan | None = None
        async for ev_name, payload in execute_planner_steps(
            plan_id=f"pl_div_{idx}",
            function_id=f"fn_div_{idx}",
            event_type=ev_type,
            meal=meal,
            budget_inr=budget,
            days_left=3,
            likely=45,
            p80=52,
            headcount=headcount,
            rules_list=rules_list,
            allergies=[],
            hh_names=["Agarwal Family", "Fernandes Household"],
            brief=brief,
            router=router,
            data=data,
            now=now,
        ):
            if ev_name == "done" and isinstance(payload, Plan):
                final_plan = payload

        assert final_plan is not None, f"Failed for {brief}"
        vocab = get_default_vocabulary()
        forbidden_tags = {
            tag for r in rules_strs if r in data.rules for tag in data.rules[r].forbid
        }
        for it in final_plan.items:
            scan = vocab.scan(it.name)
            assert not (scan.tags & forbidden_tags), (
                f"Violations for {brief}: {it.name} has {scan.tags & forbidden_tags}"
            )
