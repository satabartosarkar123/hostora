from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from hostora.config import Settings
from hostora.db.session import get_session
from hostora.security.tokens import verify_cron_signature
from hostora.services.learning import run_nightly_learning
from hostora.services.retention import run_cleanup_job, run_retention_job, run_warm_job
from hostora.web.deps import get_settings_dep

router = APIRouter(tags=["internal"])

CronJob = Literal["retention", "cleanup", "warm", "learn"]


@router.post("/internal/cron/{job}")
async def internal_cron_job(
    job: CronJob,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:  # Any: JSON response dict
    now_dt = datetime.now(UTC)
    verify_cron_signature(
        settings.cron_secret.get_secret_value(),
        request.headers.get("x-cron-timestamp"),
        request.headers.get("x-cron-signature"),
        job,
        int(now_dt.timestamp()),
    )

    if job == "retention":
        counts = await run_retention_job(session, now=now_dt)
    elif job == "cleanup":
        counts = await run_cleanup_job(session, now=now_dt)
    elif job == "learn":
        counts = await run_nightly_learning(session)
    else:
        counts = await run_warm_job(session)

    return {"job": job, "counts": counts}
