from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from hostora.errors import ProblemError

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


async def hit(
    session: AsyncSession,
    bucket: str,
    limit: int,
    window_s: int,
    now: datetime,
) -> None:
    window_start = datetime.fromtimestamp(int(now.timestamp()) // window_s * window_s, UTC)
    stmt = text("""
        INSERT INTO attempt (bucket, window_start, count) VALUES (:b, :w, 1)
        ON CONFLICT (bucket) DO UPDATE SET
          count = CASE WHEN attempt.window_start = EXCLUDED.window_start
                       THEN attempt.count + 1 ELSE 1 END,
          window_start = EXCLUDED.window_start
        RETURNING count
    """)
    if session.in_transaction():
        count = int((await session.execute(stmt, {"b": bucket, "w": window_start})).scalar_one())
        await session.commit()
    else:
        async with session.begin():
            count = int(
                (await session.execute(stmt, {"b": bucket, "w": window_start})).scalar_one()
            )
    if count > limit:
        raise ProblemError(
            429,
            "Too many attempts",
            detail=f"retry after {window_s}s",
            headers={"Retry-After": str(window_s)},
        )
