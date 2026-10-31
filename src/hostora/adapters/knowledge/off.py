from __future__ import annotations

import logging
from typing import Any

import httpx

from hostora.data.loader import get_data
from hostora.engine.grounding import Evidence

logger = logging.getLogger(__name__)


async def fetch_off_taxonomy(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch Open Food Facts ingredient taxonomy."""
    data = get_data()
    off_cfg = data.sources.get("open_food_facts", {})
    tax_url = off_cfg.get(
        "taxonomy_url", "https://static.openfoodfacts.org/data/taxonomies/ingredients.json"
    )
    user_agent = off_cfg.get("user_agent", "Hostora/1.0 (non-commercial; contact in /privacy)")

    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0, headers={"User-Agent": user_agent})
        should_close = True

    try:
        resp = await client.get(tax_url)
        resp.raise_for_status()
        raw_tax: dict[str, Any] = resp.json()
        return raw_tax
    finally:
        if should_close:
            await client.aclose()


async def search_off_product_evidence(
    query: str,
    client: httpx.AsyncClient | None = None,
) -> list[Evidence]:
    """Search Open Food Facts products for packaged food evidence."""
    data = get_data()
    off_cfg = data.sources.get("open_food_facts", {})
    search_url = off_cfg.get("product_search_url", "https://world.openfoodfacts.org/api/v2/search")
    user_agent = off_cfg.get("user_agent", "Hostora/1.0 (non-commercial; contact in /privacy)")

    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=6.0, headers={"User-Agent": user_agent})
        should_close = True

    try:
        fields = (
            "code,product_name,ingredients_text,ingredients,"
            "ingredients_analysis_tags,labels_tags,allergens_tags"
        )
        params = {
            "search_terms": query,
            "fields": fields,
            "page_size": "2",
        }
        resp = await client.get(search_url, params=params)
        if resp.status_code != 200:
            return []

        payload = resp.json()
        products = payload.get("products", [])
        evidence_list: list[Evidence] = []

        for p in products:
            code = p.get("code") or ""
            if not code:
                continue

            # Extract ingredient names
            ingredients: list[str] = []
            for ing in p.get("ingredients", []):
                txt = ing.get("text") or ing.get("id")
                if txt:
                    # Clean up prefix like "en:"
                    clean_txt = txt.split(":")[-1].replace("-", " ")
                    ingredients.append(clean_txt)

            if not ingredients and p.get("ingredients_text"):
                # Split comma-separated text
                ingredients = [i.strip() for i in p["ingredients_text"].split(",") if i.strip()]

            tags: set[str] = set()
            labels = p.get("labels_tags", []) or []
            if any("halal" in lab for lab in labels):
                tags.add("confirmed:halal")
            if any("vegetarian" in lab for lab in labels):
                tags.add("confirmed:composition")

            analysis = p.get("ingredients_analysis_tags", []) or []
            for an in analysis:
                if "en:non-vegetarian" in an:
                    tags.add("meat")
                elif "en:vegan" in an:
                    tags.add("confirmed:vegan")

            evidence_list.append(
                Evidence(
                    source="product_label",
                    ingredients=tuple(ingredients),
                    tags=frozenset(tags),
                    citation=f"off:{code}",
                )
            )

        return evidence_list
    except Exception as e:
        logger.debug("OFF product search failed for %s: %s", query, e)
        return []
    finally:
        if should_close:
            await client.aclose()
