from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from hostora.config import to_asyncpg_url
from hostora.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_db_url() -> tuple[str, dict[str, object]]:
    raw = os.environ.get("DATABASE_URL")
    try:
        from hostora.config import get_settings

        settings = get_settings()
        if os.environ.get("ALEMBIC_USE_TEST_DB") == "1" and settings.test_database_url:
            raw = settings.test_database_url
        elif not raw:
            raw = settings.database_url
    except Exception:
        if not raw:
            raw = config.get_main_option("sqlalchemy.url") or ""
    return to_asyncpg_url(raw or "")


def run_migrations_offline() -> None:
    url, _ = get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text("SELECT pg_advisory_lock(727270)"))
    try:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(727270)"))
        connection.commit()


async def run_async_migrations() -> None:
    url, connect_args = get_db_url()
    connectable = create_async_engine(url, connect_args=connect_args, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
