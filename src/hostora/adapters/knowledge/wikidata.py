from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx

from hostora.data.loader import get_data
from hostora.engine.grounding import Evidence

logger = logging.getLogger(__name__)


async def fetch_wikidata_evidence(
    item_name: str,
    local_names: Sequence[str] = (),
    client: httpx.AsyncClient | None = None,
) -> list[Evidence]:
    """Fetch Wikidata ingredients for dish using wbsearchentities + SPARQL."""
    data = get_data()
    wk_cfg = data.sources.get("wikidata", {})
    user_agent = wk_cfg.get("user_agent", "Hostora/1.0 (non-commercial; contact in /privacy)")
    sparql_url = wk_cfg.get("sparql_url", "https://query.wikidata.org/sparql")
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }

    names_to_try = [item_name, *local_names]
    candidate_qids: list[str] = []

    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=6.0, headers=headers)
        should_close = True

    try:
        # 1. Search entities
        for name in names_to_try:
            if not name.strip():
                continue
            q_param = httpx.URL("", params={"q": name}).params["q"]
            search_url = (
                f"https://www.wikidata.org/w/api.php?action=wbsearchentities"
                f"&search={q_param}&language=en&format=json&limit=3"
            )
            try:
                resp = await client.get(search_url)
                if resp.status_code == 200:
                    payload = resp.json()
                    search_res = payload.get("search", [])
                    for item in search_res:
                        qid = item.get("id")
                        if qid and qid.startswith("Q") and qid not in candidate_qids:
                            candidate_qids.append(qid)
            except Exception as e:
                logger.debug("Wikidata entity search error for %s: %s", name, e)

            if candidate_qids:
                break

        if not candidate_qids:
            return []

        # 2. SPARQL for P186 (made from material) and P527 (has part)
        results: list[Evidence] = []
        for qid in candidate_qids[:2]:
            query = f"""
            SELECT DISTINCT ?ingLabel WHERE {{
              VALUES ?prop {{ wdt:P186 wdt:P527 }}
              wd:{qid} ?prop ?ing .
              SERVICE wikibase:label {{
                bd:serviceParam wikibase:language "en,hi,bn,ta,te,mr,gu,kn,ml,pa,ur".
              }}
            }}
            LIMIT 20
            """
            try:
                sparql_resp = await client.get(
                    sparql_url,
                    params={"query": query, "format": "json"},
                    headers={"Accept": "application/sparql-results+json"},
                )
                if sparql_resp.status_code == 200:
                    data_json = sparql_resp.json()
                    bindings = data_json.get("results", {}).get("bindings", [])
                    ingredients = [
                        b["ingLabel"]["value"]
                        for b in bindings
                        if "ingLabel" in b and "value" in b["ingLabel"]
                    ]
                    if ingredients:
                        results.append(
                            Evidence(
                                source="knowledge_base",
                                ingredients=tuple(ingredients),
                                citation=f"https://www.wikidata.org/wiki/{qid}",
                            )
                        )
            except Exception as e:
                logger.debug("Wikidata SPARQL query error for %s: %s", qid, e)

        return results
    finally:
        if should_close:
            await client.aclose()
