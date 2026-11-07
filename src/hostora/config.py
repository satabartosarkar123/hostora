from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    public_origin: str = "http://localhost:8000"
    database_url: str
    test_database_url: str | None = None

    secret_key: SecretStr
    token_hmac_secret: SecretStr
    phone_enc_key: SecretStr
    store_token_key: SecretStr = SecretStr("")
    pass_signing_key: SecretStr
    pass_public_key: str
    cron_secret: SecretStr

    firebase_project_id: str = ""
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_app_id: str = ""

    groq_api_key: SecretStr | None = None
    groq_model_large: str = "openai/gpt-oss-120b"
    groq_model_small: str = "llama-3.1-8b-instant"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_allowed: bool = False

    demo_mode: bool = False
    data_dir: Path = Path("data")
    sentry_dsn: str | None = None
    log_level: str = "INFO"

    @field_validator("public_origin")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


def to_asyncpg_url(url: str) -> tuple[str, dict[str, object]]:
    """Neon gives `postgresql://...?sslmode=require&channel_binding=require`.
    asyncpg rejects those query params; it wants `ssl` in connect_args."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    connect_args: dict[str, object] = {}
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode in {"require", "verify-ca", "verify-full"}:
        connect_args["ssl"] = "require"
    scheme = "postgresql+asyncpg"
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), "")), connect_args


def db_target(url: str) -> tuple[str, int, str]:
    """Return normalized (host, port, database_name) for a Postgres URL."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "localhost").lower()
    port = parts.port or 5432
    dbname = parts.path.lstrip("/").lower()
    return host, port, dbname


def assert_distinct_test_db(database_url: str, test_database_url: str | None) -> None:
    """Raise ValueError if TEST_DATABASE_URL matches DATABASE_URL's host and database."""
    if not test_database_url or not test_database_url.strip():
        return
    if not database_url or not database_url.strip():
        return
    if db_target(database_url) == db_target(test_database_url):
        raise ValueError(
            "Safety guard triggered: TEST_DATABASE_URL points to the same host and database "
            "as DATABASE_URL. Refusing to run because tests TRUNCATE tables."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
