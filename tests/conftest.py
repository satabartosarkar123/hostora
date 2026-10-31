from __future__ import annotations

import base64
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport
from hypothesis import settings as hypothesis_settings
from sqlalchemy import text

from hostora.config import Settings, assert_distinct_test_db
from hostora.db.models import Base
from hostora.db.session import make_engine, make_sessionmaker
from hostora.engine.passes import generate_key_pair_hex
from hostora.main import create_app
from hostora.security import firebase as firebase_mod

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

hypothesis_settings.register_profile("release", max_examples=2000, deadline=None)
hypothesis_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))

_MIGRATED_URLS: set[str] = set()


def _read_dotenv_map() -> dict[str, str]:
    env_path = Path(".env")
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def _test_db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or _read_dotenv_map().get("TEST_DATABASE_URL", "")


def _prod_db_url() -> str:
    return os.environ.get("DATABASE_URL") or _read_dotenv_map().get("DATABASE_URL", "")


@pytest.fixture
def settings() -> Settings:
    priv_hex, pub_hex = generate_key_pair_hex()
    db_url = _test_db_url() or "postgresql://hostora:hostora@localhost:5432/hostora_test"
    return Settings(
        env="test",
        public_origin="http://test",
        database_url=db_url,
        test_database_url=db_url,
        secret_key=secrets.token_urlsafe(48),
        token_hmac_secret=secrets.token_urlsafe(48),
        phone_enc_key=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        pass_signing_key=priv_hex,
        pass_public_key=pub_hex,
        cron_secret=secrets.token_urlsafe(32),
        firebase_project_id="hostora-test",
        firebase_api_key="fake-api-key",
        firebase_auth_domain="hostora-test.firebaseapp.com",
        firebase_app_id="1:123:web:abc",
        demo_mode=True,
        data_dir=Path("data"),
    )


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[AsyncSession]:
    test_url = _test_db_url()
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is not set; skipping db test")

    assert_distinct_test_db(_prod_db_url(), test_url)

    if test_url not in _MIGRATED_URLS:
        env = os.environ.copy()
        env["DATABASE_URL"] = test_url
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env=env,
        )
        _MIGRATED_URLS.add(test_url)

    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with sessionmaker() as truncate_session, truncate_session.begin():
        await truncate_session.execute(
            text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")
        )

    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.close()
    await engine.dispose()


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    try:
        yield application
    finally:
        if getattr(application.state, "engine", None) is not None:
            await application.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def host_headers() -> dict[str, str]:
    return {"Authorization": "Demo demo-host"}


@pytest.fixture
def fake_firebase(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> Callable[[str, str | None], str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class _FakeKey:
        key = public_key

    def _fake_get_signing_key(_token: str) -> Any:  # Any: mock PyJWK
        return _FakeKey()

    monkeypatch.setattr(firebase_mod._jwks, "get_signing_key_from_jwt", _fake_get_signing_key)

    def _sign(uid: str, name: str | None = "Test Host") -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {  # Any: JWT payload
            "sub": uid,
            "aud": settings.firebase_project_id,
            "iss": f"https://securetoken.google.com/{settings.firebase_project_id}",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }
        if name is not None:
            payload["name"] = name
        return jwt.encode(payload, private_key, algorithm="RS256")

    return _sign


@pytest.fixture
def live_server(settings: Settings, db: AsyncSession) -> Iterator[str]:
    _ = db
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])

    origin = f"http://127.0.0.1:{port}"
    server_settings = settings.model_copy(update={"public_origin": origin})
    server_app = create_app(server_settings)
    config = uvicorn.Config(
        server_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{origin}/healthz", timeout=0.5)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)

    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
