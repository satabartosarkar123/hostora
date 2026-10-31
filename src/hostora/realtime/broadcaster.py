from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import asyncpg

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class Broadcaster:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)

    @asynccontextmanager
    async def subscribe(self, key: str) -> AsyncIterator[asyncio.Queue[str]]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._subs[key].add(q)
        try:
            yield q
        finally:
            self._subs[key].discard(q)
            if not self._subs[key]:
                del self._subs[key]

    def _deliver_local(self, key: str, message: str) -> None:
        for q in list(self._subs.get(key, ())):
            if q.full():
                q.get_nowait()
            q.put_nowait(message)

    async def publish(self, key: str, message: str) -> None:
        self._deliver_local(key, message)


class PgNotifyBroadcaster(Broadcaster):
    """Multi-instance broadcaster backed by Postgres LISTEN/NOTIFY on channel `hostora_events`."""

    CHANNEL = "hostora_events"

    def __init__(self, dsn: str) -> None:
        super().__init__()
        self._dsn = dsn
        self._conn: Any = None

    async def start(self) -> None:
        if self._conn is not None:
            return
        from hostora.config import to_asyncpg_url

        async_url, connect_args = to_asyncpg_url(self._dsn)
        dsn = async_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        ssl_arg = connect_args.get("ssl")
        self._conn = (
            await asyncpg.connect(dsn, ssl=ssl_arg)
            if ssl_arg is not None
            else await asyncpg.connect(dsn)
        )
        await self._conn.add_listener(self.CHANNEL, self._on_notify)

    def _on_notify(
        self,
        _connection: Any,
        _pid: int,
        _channel: str,
        payload: object,
    ) -> None:
        if not isinstance(payload, str):
            return
        try:
            data = json.loads(payload)
            key = str(data["key"])
            message = str(data["message"])
        except (ValueError, KeyError, TypeError):
            return
        self._deliver_local(key, message)

    async def publish(self, key: str, message: str) -> None:
        if self._conn is None:
            self._deliver_local(key, message)
            return
        payload = json.dumps({"key": key, "message": message})
        await self._conn.execute("SELECT pg_notify($1, $2)", self.CHANNEL, payload)

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.remove_listener(self.CHANNEL, self._on_notify)
            finally:
                await self._conn.close()
                self._conn = None
