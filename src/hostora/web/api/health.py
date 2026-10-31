from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from hostora.data.loader import get_data
from hostora.schemas.common import EventType

router = APIRouter(tags=["health"])


@router.get("/v1/health")
async def v1_health() -> dict[str, Any]:  # Any: JSON response dict
    data = get_data()
    return {"ok": True, "dataVersion": data.version}


@router.get("/v1/config/public")
async def v1_public_config() -> dict[str, Any]:  # Any: JSON response dict
    data = get_data()
    return {
        "dataVersion": data.version,
        "traditions": data.traditions.get("traditions", {}),
        "rules": {k: {"id": r.id, "label": r.label} for k, r in data.rules.items()},
        "eventTypes": [e.value for e in EventType],
        "channels": {
            k: {"id": c.id, "label": c.label, "kind": c.kind} for k, c in data.channels.items()
        },
    }
