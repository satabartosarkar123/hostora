from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from hostora.db.models import Cohost, Event
from hostora.errors import ProblemError
from hostora.security.csrf import verify_csrf
from hostora.security.firebase import HostIdentity, verify_firebase_token

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession


async def current_host(request: Request) -> HostIdentity:
    settings = request.app.state.settings
    auth_header = request.headers.get("authorization", "")
    if auth_header:
        parts = auth_header.split(" ", 1)
        if len(parts) == 2:
            scheme, cred = parts[0].lower(), parts[1].strip()
            if scheme == "demo" and settings.demo_mode and cred == "demo-host":
                return HostIdentity(uid="demo-host", name="Demo Host")
            if scheme == "bearer" and cred:
                return await verify_firebase_token(cred, settings.firebase_project_id)
        raise ProblemError(401, "Invalid Authorization header")

    uid = request.session.get("uid")
    if isinstance(uid, str) and uid:
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            await verify_csrf(request)
        name = request.session.get("name")
        return HostIdentity(uid=uid, name=name if isinstance(name, str) else None)

    raise ProblemError(401, "Authentication required")


async def require_event_access(
    session: AsyncSession,
    event_key: str,
    host: HostIdentity,
    need: Literal["view", "edit"] = "view",
) -> Event:
    event = (
        await session.execute(
            select(Event).where(Event.event_key == event_key, Event.purged_at.is_(None))
        )
    ).scalar_one_or_none()
    if event is None:
        raise ProblemError(404, "Event not found")
    if event.host_uid == host.uid:
        return event

    cohost = await session.get(Cohost, (event_key, host.uid))
    if cohost is None:
        raise ProblemError(404, "Event not found")
    if need == "edit" and cohost.role != "edit":
        raise ProblemError(404, "Event not found")
    return event
