from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.db.models import KnowledgeFact
from hostora.engine.grounding import Evidence

logger = logging.getLogger(__name__)


async def get_cached_evidence(
    subject_norm: str,
    session: AsyncSession,
    host_uid: str | None = None,
) -> list[Evidence]:
    """Retrieve non-expired facts for subject_norm."""
    now = datetime.now(UTC)
    query = select(KnowledgeFact).where(
        KnowledgeFact.subject_norm == subject_norm,
        KnowledgeFact.expires_at > now,
    )
    if host_uid:
        query = query.where(
            (KnowledgeFact.host_uid.is_(None)) | (KnowledgeFact.host_uid == host_uid)
        )
    else:
        query = query.where(KnowledgeFact.host_uid.is_(None))

    result = await session.execute(query)
    facts = result.scalars().all()

    evidence_list: list[Evidence] = []
    for f in facts:
        p: dict[str, Any] = f.payload or {}
        ingredients = tuple(p.get("ingredients", []))
        tags = frozenset(p.get("tags", []))
        diet = p.get("diet")
        agreement = float(p.get("agreement", 1.0))
        evidence_list.append(
            Evidence(
                source=f.source,  # type: ignore[arg-type]
                ingredients=ingredients,
                tags=tags,
                diet=diet,
                agreement=agreement,
                citation=f.citation,
            )
        )
    return evidence_list


async def cache_evidence(
    subject_norm: str,
    kind: str,
    evidence: Evidence,
    session: AsyncSession,
    host_uid: str | None = None,
    ttl_days: int = 30,
) -> None:
    """Store an evidence item into knowledge_fact."""
    now = datetime.now(UTC)
    payload = {
        "ingredients": list(evidence.ingredients),
        "tags": list(evidence.tags),
        "diet": evidence.diet,
        "agreement": evidence.agreement,
    }
    fact = KnowledgeFact(
        subject_norm=subject_norm,
        kind=kind,
        source=evidence.source,
        payload=payload,
        citation=evidence.citation or "",
        host_uid=host_uid,
        retrieved_at=now,
        expires_at=now + timedelta(days=ttl_days),
    )
    session.add(fact)
    await session.commit()
