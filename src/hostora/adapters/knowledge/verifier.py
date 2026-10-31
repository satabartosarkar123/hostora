from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from hostora.adapters.llm.base import LlmClient
from hostora.engine.grounding import Evidence, Vocabulary, agreement

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def verify_dish_composition(
    dish_name: str,
    region: str,
    llm: LlmClient,
    vocabulary: Vocabulary,
    samples: int = 3,
) -> Evidence:
    """Run independent verifier samples without seeing the proposer's answer."""
    prompt = (
        f"What is usually in '{dish_name}' as cooked in '{region}'? "
        "List the 3-8 primary culinary ingredients, and specify the dietary classification "
        "(veg, egg, nonveg, jain, or vegan).\n"
        'Return ONLY a JSON object: {"ingredients": ["..."], "diet": "veg|egg|nonveg|jain|vegan"}'
    )

    async def _one_sample() -> tuple[list[str], str]:
        try:
            resp = await llm.complete(
                prompt,
                system="You are an expert culinary verifier. Answer factually with ingredients.",
                temperature=0.7,
            )
            match = re.search(r"\{.*\}", resp, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                ings = [
                    str(x).strip().lower() for x in data.get("ingredients", []) if str(x).strip()
                ]
                dt = str(data.get("diet", "")).strip().lower()
                return ings, dt
        except Exception as e:
            logger.debug("Verifier sample failed for %s: %s", dish_name, e)
        return [], ""

    sample_tasks = [_one_sample() for _ in range(samples)]
    raw_results = await asyncio.gather(*sample_tasks)

    valid_results = [r for r in raw_results if r[0]]
    if not valid_results:
        # Fallback to single attempt or empty
        return Evidence(source="verifier", ingredients=(), agreement=0.0)

    # Calculate restricted tags per sample for consistency / agreement score
    sample_tags: list[frozenset[str]] = []
    for ings, _ in valid_results:
        tags: set[str] = set()
        for i in ings:
            tags.update(vocabulary.scan(i).tags)
        sample_tags.append(frozenset(tags))

    agree_score = agreement(sample_tags)

    # Majority ingredients and diet
    ing_counts: dict[str, int] = {}
    diet_counts: dict[str, int] = {}
    for ings, dt in valid_results:
        for i in ings:
            ing_counts[i] = ing_counts.get(i, 0) + 1
        if dt:
            diet_counts[dt] = diet_counts.get(dt, 0) + 1

    # Keep ingredients present in at least half of the samples
    min_count = max(1, len(valid_results) // 2)
    majority_ings = [k for k, v in ing_counts.items() if v >= min_count]
    majority_diet = max(diet_counts.items(), key=lambda kv: kv[1])[0] if diet_counts else None

    return Evidence(
        source="verifier",
        ingredients=tuple(majority_ings),
        agreement=agree_score,
        diet=majority_diet,
    )
