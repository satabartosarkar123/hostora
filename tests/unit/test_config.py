from __future__ import annotations

import base64
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hostora.cli import app as cli_app
from hostora.config import Settings, assert_distinct_test_db, to_asyncpg_url
from hostora.engine.passes import generate_key_pair_hex


def test_to_asyncpg_url_neon_ssl() -> None:
    neon_url = (
        "postgresql://user:pass@ep-cool-host.ap-southeast-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require&application_name=hostora"
    )
    async_url, connect_args = to_asyncpg_url(neon_url)
    assert async_url == (
        "postgresql+asyncpg://user:pass@ep-cool-host.ap-southeast-1.aws.neon.tech/neondb"
        "?application_name=hostora"
    )
    assert connect_args == {"ssl": "require"}


def test_to_asyncpg_url_local_plain() -> None:
    local_url = "postgresql://hostora:hostora@localhost:5432/hostora_test"
    async_url, connect_args = to_asyncpg_url(local_url)
    assert async_url == "postgresql+asyncpg://hostora:hostora@localhost:5432/hostora_test"
    assert connect_args == {}


def test_settings_strips_trailing_slash() -> None:
    s = Settings(
        public_origin="https://hostora.dev/",
        database_url="postgresql://localhost/db",
        secret_key="s",
        token_hmac_secret="t",
        phone_enc_key="p",
        pass_signing_key="k",
        pass_public_key="pub",
        cron_secret="c",
    )
    assert s.public_origin == "https://hostora.dev"


def test_assert_distinct_test_db_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prod_neon = (
        "postgresql://user:pass@ep-prod-branch.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    )
    test_neon = (
        "postgresql://user:pass@ep-test-branch.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    )

    # Distinct Neon branches (different hosts, same database name) -> OK
    assert_distinct_test_db(prod_neon, test_neon)

    # Distinct local databases (same host, different database name) -> OK
    assert_distinct_test_db(
        "postgresql://hostora:hostora@localhost:5432/hostora",
        "postgresql://hostora:hostora@localhost:5432/hostora_test",
    )

    # Same host and database name -> FAIL
    with pytest.raises(ValueError, match="Safety guard triggered"):
        assert_distinct_test_db(prod_neon, prod_neon)

    with pytest.raises(ValueError, match="Safety guard triggered"):
        assert_distinct_test_db(
            "postgresql://user1:p1@localhost:5432/hostora",
            "postgresql+asyncpg://user2:p2@LOCALHOST/hostora?sslmode=require",
        )

    # Also verify `hostora doctor` fails when DATABASE_URL and TEST_DATABASE_URL match
    bad_env = tmp_path / ".env"
    bad_env.write_text(
        "DATABASE_URL=postgresql://u:p@ep-same.aws.neon.tech/neondb\n"
        "TEST_DATABASE_URL=postgresql://u:p@ep-same.aws.neon.tech/neondb\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    runner = CliRunner()
    res = runner.invoke(cli_app, ["doctor", "--env-file", str(bad_env)])
    assert res.exit_code != 0
    assert "[FAIL] Safety guard (TEST_DATABASE_URL != DATABASE_URL)" in res.output


def test_doctor_mismatched_pass_keys_short_phone_key_and_no_secret_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    priv1, _pub1 = generate_key_pair_hex()
    _priv2, pub2 = generate_key_pair_hex()
    short_phone_key = base64.b64encode(b"too-short").decode()
    secret_marker = "SUPER_SECRET_MARKER_VALUE_1234567890"

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DATABASE_URL=postgresql://u:{secret_marker}@ep-prod.aws.neon.tech/neondb\n"
        f"TEST_DATABASE_URL=postgresql://u:{secret_marker}@ep-test.aws.neon.tech/neondb\n"
        f"SECRET_KEY={secret_marker}\n"
        f"TOKEN_HMAC_SECRET={secret_marker}\n"
        f"PHONE_ENC_KEY={short_phone_key}\n"
        f"PASS_SIGNING_KEY={priv1}\n"
        f"PASS_PUBLIC_KEY={pub2}\n"
        f"CRON_SECRET={secret_marker}\n",
        encoding="utf-8",
    )
    for k in (
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "SECRET_KEY",
        "TOKEN_HMAC_SECRET",
        "PHONE_ENC_KEY",
        "PASS_SIGNING_KEY",
        "PASS_PUBLIC_KEY",
        "CRON_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)

    runner = CliRunner()
    res = runner.invoke(cli_app, ["doctor", "--env-file", str(env_file)])
    assert res.exit_code != 0
    assert "[MALFORMED] Env PHONE_ENC_KEY" in res.output
    assert "[MALFORMED] Ed25519 keypair match" in res.output
    # Output must never contain any secret value
    assert secret_marker not in res.output
    assert priv1 not in res.output
    assert short_phone_key not in res.output


def test_env_example_matches_settings_fields() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    example_keys: set[str] = set()
    for raw_line in env_example.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        example_keys.add(key)

    settings_keys = {name.upper() for name in Settings.model_fields}
    assert example_keys == settings_keys
