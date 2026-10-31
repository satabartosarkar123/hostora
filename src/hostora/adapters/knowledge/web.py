from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx

from hostora.adapters.llm.base import LlmClient
from hostora.engine.grounding import Evidence, quote_supported

logger = logging.getLogger(__name__)


async def fetch_web_evidence(
    dish_name: str,
    region: str = "",
    llm: LlmClient | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[Evidence]:
    """Search-grounded extraction: query web/LLM with search grounding, extract quotes & URLs.

    Uses `quote_supported` to filter out any ingredients not literally found in quotes.
    """
    if llm is None:
        return []

    prompt = (
        f"Search for authentic culinary recipes of '{dish_name}' in region '{region}'. "
        "Extract the exact main ingredients, and provide exact verbatim quotes with URL citations "
        "where each ingredient is mentioned.\n"
        'Format as JSON: {"ingredients": ["..."], '
        '"sources": [{"url": "https://...", "quote": "..."}]}'
    )

    try:
        raw_resp = await llm.complete(
            prompt, system="You are a factual culinary researcher with web search grounding."
        )
        # Try parsing JSON or structured response
        import json
        import re

        match = re.search(r"\{.*\}", raw_resp, re.DOTALL)
        if not match:
            return []

        data = json.loads(match.group(0))
        ingredients: Sequence[str] = data.get("ingredients", [])
        sources = data.get("sources", [])

        if not sources or not ingredients:
            return []

        results: list[Evidence] = []
        for src in sources:
            url = src.get("url")
            quote = src.get("quote")
            if not url or not quote:
                continue

            supported, _ = quote_supported(ingredients, [quote])
            if supported:
                results.append(
                    Evidence(
                        source="web",
                        ingredients=supported,
                        citation=url,
                    )
                )

        return results
    except Exception as e:
        logger.debug("Web grounding failed for %s: %s", dish_name, e)
        return []
