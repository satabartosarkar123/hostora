from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from hostora.db.models import (
    Cohost,
    Event,
    Host,
    Household,
    Idempotency,
    KnowledgeFact,
    StoreConnection,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from hostora.security.firebase import HostIdentity


async def delete_account(session: AsyncSession, host: HostIdentity) -> None:
    async with session.begin():
        event_keys = (
            await session.scalars(select(Event.event_key).where(Event.host_uid == host.uid))
        ).all()
        if event_keys:
            hh_ids = (
                await session.scalars(
                    select(Household.id).where(Household.event_key.in_(event_keys))
                )
            ).all()
            if hh_ids:
                scopes = [f"rsvp:{hid}" for hid in hh_ids]
                await session.execute(delete(Idempotency).where(Idempotency.scope.in_(scopes)))
        await session.execute(delete(StoreConnection).where(StoreConnection.host_uid == host.uid))
        await session.execute(delete(KnowledgeFact).where(KnowledgeFact.host_uid == host.uid))
        await session.execute(delete(Cohost).where(Cohost.uid == host.uid))
        await session.execute(delete(Host).where(Host.uid == host.uid))
