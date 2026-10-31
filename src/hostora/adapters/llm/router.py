from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, TypeVar

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hostora.adapters.llm.base import LlmClient, LlmClientError, Route
from hostora.adapters.llm.gemini import GeminiClient
from hostora.adapters.llm.groq import GroqClient
from hostora.adapters.llm.mock import MockLlmClient
from hostora.db.models import LlmCache as LlmCacheModel
from hostora.planner.redact import assert_clean

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from hostora.config import Settings

logger = structlog.get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class LlmCacheBackend(Protocol):
    async def get(self, key: str) -> tuple[str, Route] | None: ...
    async def put(self, key: str, route: Route, text: str) -> None: ...


class DbLlmCache:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, key: str) -> tuple[str, Route] | None:
        cutoff = datetime.now(UTC) - timedelta(days=7)
        async with self._sessionmaker() as session, session.begin():
            row = (
                await session.execute(
                    select(LlmCacheModel).where(
                        LlmCacheModel.key == key,
                        LlmCacheModel.created_at >= cutoff,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            route: Route = "gemini" if row.route == "gemini" else "groq"
            return row.text, route

    async def put(self, key: str, route: Route, text: str) -> None:
        now = datetime.now(UTC)
        async with self._sessionmaker() as session, session.begin():
            stmt = pg_insert(LlmCacheModel).values(
                key=key,
                route=route,
                text=text,
                created_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[LlmCacheModel.key],
                set_={"route": route, "text": text, "created_at": now},
            )
            await session.execute(stmt)


class LlmRouter:
    def __init__(
        self,
        clients: Sequence[LlmClient],
        cache: LlmCacheBackend | None = None,
        *,
        data_version: str = "",
    ) -> None:
        self._clients = list(clients)
        self._cache = cache
        self._data_version = data_version
        self._failures: dict[int, int] = {}
        self._open_until: dict[int, float] = {}

    def _is_circuit_open(self, idx: int) -> bool:
        until = self._open_until.get(idx, 0.0)
        if until <= 0.0:
            return False
        if time.monotonic() >= until:
            self._open_until.pop(idx, None)
            self._failures[idx] = 0
            return False
        return True

    def _record_success(self, idx: int) -> None:
        self._failures[idx] = 0
        self._open_until.pop(idx, None)

    def _record_failure(self, idx: int) -> None:
        count = self._failures.get(idx, 0) + 1
        self._failures[idx] = count
        if count >= 3:
            self._open_until[idx] = time.monotonic() + 60.0

    def _cache_key(self, purpose: str, system: str, user: str) -> str:
        raw = f"{self._data_version}\0{purpose}\0{system}\0{user}".encode()
        return hashlib.sha256(raw).hexdigest()

    def select(self) -> tuple[LlmClient | None, Route]:
        for idx, client in enumerate(self._clients):
            if not self._is_circuit_open(idx):
                return client, client.name
        return None, "manual"

    def get_default_client(self) -> LlmClient | None:
        client, _ = self.select()
        return client

    async def complete(
        self,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 800,
        *,
        purpose: str = "default",
        forbidden_terms: Iterable[str] = (),
    ) -> tuple[T, Route] | None:
        if not assert_clean(user, forbidden_terms=forbidden_terms):
            logger.warning("llm_privacy_guard_refused", purpose=purpose)
            return None

        cache_key = self._cache_key(purpose, system, user)
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                cached_text, cached_route = cached
                try:
                    parsed = schema.model_validate_json(cached_text)
                    return parsed, cached_route
                except ValidationError:
                    pass

        for idx, client in enumerate(self._clients):
            if self._is_circuit_open(idx):
                continue

            try:
                raw_json = await client.complete_json(system, user, max_tokens)
            except LlmClientError as exc:
                self._record_failure(idx)
                logger.warning("llm_client_failed", client=client.name, error=str(exc))
                continue
            except Exception as exc:
                self._record_failure(idx)
                logger.warning("llm_client_unexpected_error", client=client.name, error=str(exc))
                continue

            try:
                parsed = schema.model_validate_json(raw_json)
                self._record_success(idx)
                if self._cache is not None:
                    await self._cache.put(cache_key, client.name, raw_json)
                return parsed, client.name
            except ValidationError:
                # One repair call
                schema_json = json.dumps(schema.model_json_schema())
                repair_user = (
                    f"{user}\n\nYour previous output was invalid. "
                    f"Return only JSON matching this schema: {schema_json}"
                )
                try:
                    repaired_json = await client.complete_json(system, repair_user, max_tokens)
                    parsed = schema.model_validate_json(repaired_json)
                    self._record_success(idx)
                    if self._cache is not None:
                        await self._cache.put(cache_key, client.name, repaired_json)
                    return parsed, client.name
                except (LlmClientError, ValidationError, Exception) as exc:
                    self._record_failure(idx)
                    logger.warning("llm_repair_failed", client=client.name, error=str(exc))
                    continue

        return None


def build_llm_router(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    *,
    data_version: str = "",
) -> LlmRouter:
    import os

    is_test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    is_live = os.environ.get("HOSTORA_LLM_LIVE") == "1"
    if is_test and not is_live:
        return LlmRouter([MockLlmClient()], cache=None, data_version=data_version)

    clients: list[LlmClient] = []
    if settings.groq_api_key and settings.groq_api_key.get_secret_value():
        clients.append(
            GroqClient(
                api_key=settings.groq_api_key.get_secret_value(),
                model=settings.groq_model_large,
            )
        )
    if (
        settings.gemini_allowed
        and settings.gemini_api_key
        and settings.gemini_api_key.get_secret_value()
    ):
        clients.append(
            GeminiClient(
                api_key=settings.gemini_api_key.get_secret_value(),
                model=settings.gemini_model,
            )
        )
    if not clients and settings.demo_mode:
        clients.append(MockLlmClient())

    cache = DbLlmCache(sessionmaker) if sessionmaker is not None else None
    return LlmRouter(clients, cache=cache, data_version=data_version)


def get_llm_router() -> LlmRouter:
    from hostora.config import get_settings

    settings = get_settings()
    return build_llm_router(settings)
