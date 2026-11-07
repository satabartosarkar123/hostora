"""`hostora` command-line interface. More commands are added in later milestones."""

from __future__ import annotations

import base64
import secrets
from pathlib import Path
from typing import Annotated, Any

import typer

from hostora.data.loader import DataError, load_data
from hostora.engine.passes import generate_key_pair_hex

app = typer.Typer(help="Hostora developer and operations commands.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Hostora developer and operations commands."""


@app.command("gen-keys")
def gen_keys() -> None:
    """Print fresh secrets as .env lines. Paste into .env locally or into your host's secrets."""
    private_hex, public_hex = generate_key_pair_hex()
    lines = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "TOKEN_HMAC_SECRET": secrets.token_urlsafe(48),
        "PHONE_ENC_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "STORE_TOKEN_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "PASS_SIGNING_KEY": private_hex,
        "PASS_PUBLIC_KEY": public_hex,
        "CRON_SECRET": secrets.token_urlsafe(32),
    }
    for name, value in lines.items():
        typer.echo(f"{name}={value}")


@app.command("data-check")
def data_check(
    data_dir: Annotated[Path, typer.Option(help="Path to data directory")] = Path("data"),
) -> None:
    """Validate rules, traditions, commerce channels, concept map, and sources data files."""
    try:
        bundle = load_data(data_dir)
    except DataError as exc:
        typer.echo(f"Data check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Data version: {bundle.version}\n"
        f"Rules: {len(bundle.rules)}\n"
        f"Semantics: {len(bundle.semantics.substance_tags)} substance tags\n"
        f"Channels: {len(bundle.channels)}\n"
        f"Concepts: {len(bundle.concept_map.get('open_food_facts_parents', {}))}\n"
        f"Sources: {len(bundle.sources)}"
    )


@app.command("knowledge-refresh")
def knowledge_refresh() -> None:
    """Fetch and rebuild vocabulary from Open Food Facts into knowledge_concept/form."""
    import asyncio

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker
    from hostora.services.knowledge import refresh_knowledge

    async def _run() -> dict[str, int]:
        settings = get_settings()
        url = (
            settings.test_database_url
            if (settings.test_database_url and settings.env in ("dev", "test"))
            else settings.database_url
        )
        engine = make_engine(settings, url=url)
        sessionmaker = make_sessionmaker(engine)
        try:
            async with sessionmaker() as session:
                return await refresh_knowledge(session)
        finally:
            await engine.dispose()

    counts = asyncio.run(_run())
    typer.echo(
        f"Knowledge refresh complete: {counts.get('concepts', 0)} concepts, "
        f"{counts.get('forms', 0)} forms."
    )


@app.command("calibrate")
def calibrate(
    alpha: Annotated[float, typer.Option(help="Target risk alpha in (0, 1)")] = 0.01,
) -> None:
    """Run labelled calibration set, compute conformal threshold, and update policy."""
    import asyncio

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker
    from hostora.services.knowledge import calibrate_grounding

    async def _run() -> tuple[float, float, int]:
        settings = get_settings()
        url = (
            settings.test_database_url
            if (settings.test_database_url and settings.env in ("dev", "test"))
            else settings.database_url
        )
        engine = make_engine(settings, url=url)
        sessionmaker = make_sessionmaker(engine)
        try:
            async with sessionmaker() as session:
                return await calibrate_grounding(alpha=alpha, session=session)
        finally:
            await engine.dispose()

    thresh, risk, n = asyncio.run(_run())
    typer.echo(
        f"Conformal calibration complete:\n"
        f"  Alpha: {alpha}\n"
        f"  Min agreement threshold: {thresh:.3f}\n"
        f"  Empirical risk: {risk:.4f}\n"
        f"  Calibration points (n): {n}"
    )


@app.command("explain")
def explain(
    dish: Annotated[str, typer.Argument(help="Dish or item name to explain")],
    rules: Annotated[str, typer.Option(help="Comma-separated rule IDs (e.g. halal,pure_veg)")] = "",
) -> None:
    """Print live evidence, citations, and grounding safety decision for a dish."""
    import asyncio

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker
    from hostora.services.knowledge import explain_dish

    rule_ids = [r.strip() for r in rules.split(",") if r.strip()]

    async def _run() -> Any:
        settings = get_settings()
        url = (
            settings.test_database_url
            if (settings.test_database_url and settings.env in ("dev", "test"))
            else settings.database_url
        )
        engine = make_engine(settings, url=url)
        sessionmaker = make_sessionmaker(engine)
        try:
            async with sessionmaker() as session:
                return await explain_dish(dish_name=dish, rule_ids=rule_ids, session=session)
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    typer.echo(f"Item: {result.name} ({result.category})")
    typer.echo(f"Status: {result.status.upper()}")
    typer.echo(f"Tags: {', '.join(sorted(result.tags)) if result.tags else 'none'}")
    if result.reasons:
        typer.echo("Reasons:\n  - " + "\n  - ".join(result.reasons))
    if result.supporting:
        typer.echo(f"Supporting source families: {', '.join(result.supporting)}")
    if result.citations:
        typer.echo("Citations:\n  - " + "\n  - ".join(result.citations))


@app.command("seed-demo")
def seed_demo() -> None:
    """Seed or reset the deterministic demo event DEM01."""
    import asyncio
    import json

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker
    from hostora.services.demo import seed_demo_event

    async def _run() -> dict[str, object]:
        settings = get_settings()
        engine = make_engine(settings)
        sessionmaker = make_sessionmaker(engine)
        try:
            async with sessionmaker() as session:
                res: dict[str, object] = await seed_demo_event(session, settings)
                return res
        finally:
            await engine.dispose()

    info = asyncio.run(_run())
    typer.echo(json.dumps(info, indent=2))


@app.command("simulate")
def simulate(
    events: Annotated[int, typer.Option(help="Number of synthetic events to simulate")] = 10000,
    seed: Annotated[int, typer.Option(help="Random seed for deterministic simulation")] = 7,
    metrics_path: Annotated[Path, typer.Option(help="Path to docs/metrics.md")] = Path(
        "docs/metrics.md"
    ),
) -> None:
    """Run synthetic catering waste simulation and update docs/metrics.md."""
    from hostora.engine.simulate import run_waste_simulation

    summary = run_waste_simulation(events=events, seed=seed)
    typer.echo(summary.markdown_table)

    start_marker = "<!-- SIMULATION_START -->"
    end_marker = "<!-- SIMULATION_END -->"
    section = (
        f"{start_marker}\n"
        f"Command: `uv run hostora simulate --events {events} --seed {seed}`\n\n"
        f"{summary.markdown_table}\n"
        f"{end_marker}"
    )

    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    if metrics_path.exists():
        existing = metrics_path.read_text(encoding="utf-8")
        if start_marker in existing and end_marker in existing:
            before = existing.split(start_marker, 1)[0]
            after = existing.split(end_marker, 1)[1]
            metrics_path.write_text(before + section + after, encoding="utf-8")
            return
    metrics_path.write_text(
        f"# Hostora — Verified Metrics\n\n## 1. Synthetic Waste Simulation\n\n{section}\n",
        encoding="utf-8",
    )


def _check_secret_status(raw: str | None, *, kind: str = "secret") -> str:
    if raw is None or not raw.strip():
        return "MISSING"
    val = raw.strip()
    if kind == "b64_32":
        try:
            decoded = base64.b64decode(val, validate=True)
            return "OK" if len(decoded) == 32 else "MALFORMED"
        except ValueError:
            return "MALFORMED"
    if kind == "hex_32":
        try:
            decoded = bytes.fromhex(val)
            return "OK" if len(decoded) == 32 else "MALFORMED"
        except ValueError:
            return "MALFORMED"
    if kind == "db_url":
        return "OK" if val.startswith(("postgresql://", "postgresql+asyncpg://")) else "MALFORMED"
    return "OK" if len(val) >= 16 else "MALFORMED"


@app.command("doctor")
def doctor(
    online: Annotated[
        bool, typer.Option("--online", help="Also probe live PUBLIC_ORIGIN /healthz and /readyz")
    ] = False,
    env_file: Annotated[Path, typer.Option(help="Path to .env file")] = Path(".env"),
) -> None:
    """Verify environment, secrets (reporting only OK/MISSING/MALFORMED), data, and DB readiness."""
    import asyncio
    import os
    import sys

    import httpx
    from sqlalchemy import text

    from hostora.config import assert_distinct_test_db, to_asyncpg_url
    from hostora.engine.passes import PassClaims, sign_pass, verify_pass

    failures = 0

    def _report(label: str, status: str, detail: str = "") -> None:
        nonlocal failures
        suffix = f" ({detail})" if detail else ""
        typer.echo(f"[{status}] {label}{suffix}")
        if status in ("FAIL", "MISSING", "MALFORMED"):
            failures += 1

    # 1. Python version & .gitignore
    py_ok = sys.version_info >= (3, 12)
    _report(
        "Python >= 3.12",
        "OK" if py_ok else "FAIL",
        f"{sys.version_info.major}.{sys.version_info.minor}",
    )

    gitignore_text = (
        Path(".gitignore").read_text(encoding="utf-8") if Path(".gitignore").exists() else ""
    )
    env_ignored = ".env" in gitignore_text.splitlines()
    _report(".env in .gitignore", "OK" if env_ignored else "FAIL")

    # 2. Data bundle check
    try:
        bundle = load_data(Path("data"))
        _report("Data bundle", "OK", f"version={bundle.version[:12]} rules={len(bundle.rules)}")
    except DataError as exc:
        _report("Data bundle", "FAIL", str(exc))

    # 3. Parse .env (if present) merged with environment variables — never print secret values
    env_map: dict[str, str] = {}
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_map[k.strip()] = v.strip().strip("'").strip('"')
    for k, v in os.environ.items():
        env_map[k] = v

    required_checks = [
        ("DATABASE_URL", "db_url"),
        ("TEST_DATABASE_URL", "db_url"),
        ("SECRET_KEY", "secret"),
        ("TOKEN_HMAC_SECRET", "secret"),
        ("PHONE_ENC_KEY", "b64_32"),
        ("STORE_TOKEN_KEY", "b64_32"),
        ("PASS_SIGNING_KEY", "hex_32"),
        ("PASS_PUBLIC_KEY", "hex_32"),
        ("CRON_SECRET", "secret"),
    ]
    for var_name, kind in required_checks:
        st = _check_secret_status(env_map.get(var_name), kind=kind)
        _report(f"Env {var_name}", st)

    # Safety guard: TEST_DATABASE_URL must not point to the same host and database as DATABASE_URL
    db_url = env_map.get("DATABASE_URL", "").strip()
    test_db_url = env_map.get("TEST_DATABASE_URL", "").strip()
    try:
        assert_distinct_test_db(db_url, test_db_url)
        _report("Safety guard (TEST_DATABASE_URL != DATABASE_URL)", "OK")
    except ValueError as exc:
        _report("Safety guard (TEST_DATABASE_URL != DATABASE_URL)", "FAIL", str(exc))

    # Verify Ed25519 key pair match if both are syntactically OK
    priv_hex = env_map.get("PASS_SIGNING_KEY", "").strip()
    pub_hex = env_map.get("PASS_PUBLIC_KEY", "").strip()
    if (
        _check_secret_status(priv_hex, kind="hex_32") == "OK"
        and _check_secret_status(pub_hex, kind="hex_32") == "OK"
    ):
        try:
            claims = PassClaims("DOC01", "DOCTEST001", 2, 2_000_000_000)
            tok = sign_pass(claims, priv_hex)
            matched = verify_pass(tok, pub_hex, 1_900_000_000) == claims
            _report("Ed25519 keypair match", "OK" if matched else "MALFORMED")
        except Exception:
            _report("Ed25519 keypair match", "MALFORMED")

    # Optional variables (informational only; do not increment failure count)
    for opt_var in (
        "FIREBASE_PROJECT_ID",
        "FIREBASE_API_KEY",
        "FIREBASE_AUTH_DOMAIN",
        "FIREBASE_APP_ID",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "SENTRY_DSN",
    ):
        present = bool(env_map.get(opt_var, "").strip())
        typer.echo(f"[{'OK' if present else 'OPTIONAL_UNSET'}] Env {opt_var}")

    # 4. Database connectivity check
    async def _ping_url(url_to_ping: str) -> bool:
        from sqlalchemy.ext.asyncio import create_async_engine

        async_url, connect_args = to_asyncpg_url(url_to_ping)
        eng = create_async_engine(async_url, connect_args=connect_args)
        try:
            async with eng.connect() as conn:
                val = (await conn.execute(text("SELECT 1"))).scalar_one()
                return bool(val == 1)
        finally:
            await eng.dispose()

    for label, target_url in (
        ("Database SELECT 1 (DATABASE_URL)", db_url),
        ("Database SELECT 1 (TEST_DATABASE_URL)", test_db_url),
    ):
        if _check_secret_status(target_url, kind="db_url") == "OK":
            try:
                db_ok = asyncio.run(_ping_url(target_url))
                _report(label, "OK" if db_ok else "FAIL")
            except Exception as exc:
                _report(label, "FAIL", type(exc).__name__)

    # 5. Online checks (Firebase, Groq, and deployed PUBLIC_ORIGIN)
    if online:
        fb_proj = env_map.get("FIREBASE_PROJECT_ID", "").strip()
        try:
            jwks_r = httpx.get(
                "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com",
                timeout=10.0,
            )
            _report(
                "Online Firebase JWKS",
                "OK" if jwks_r.status_code == 200 and "keys" in jwks_r.json() else "FAIL",
                f"HTTP {jwks_r.status_code}",
            )
        except Exception as exc:
            _report("Online Firebase JWKS", "FAIL", type(exc).__name__)

        if fb_proj:
            try:
                oid_r = httpx.get(
                    f"https://securetoken.google.com/{fb_proj}/.well-known/openid-configuration",
                    timeout=10.0,
                )
                _report(
                    f"Online Firebase OpenID ({fb_proj})",
                    "OK" if oid_r.status_code == 200 else "FAIL",
                    f"HTTP {oid_r.status_code}",
                )
            except Exception as exc:
                _report(f"Online Firebase OpenID ({fb_proj})", "FAIL", type(exc).__name__)

        groq_key = env_map.get("GROQ_API_KEY", "").strip()
        if groq_key:
            try:
                groq_r = httpx.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    timeout=10.0,
                )
                _report(
                    "Online Groq API (/openai/v1/models)",
                    "OK" if groq_r.status_code == 200 else "FAIL",
                    f"HTTP {groq_r.status_code}",
                )
            except Exception as exc:
                _report("Online Groq API (/openai/v1/models)", "FAIL", type(exc).__name__)

        origin = env_map.get("PUBLIC_ORIGIN", "http://localhost:8000").rstrip("/")
        if origin.startswith(("http://localhost", "http://127.0.0.1")):
            typer.echo(
                f"[PENDING] Online deployment ({origin}; Render deployment not configured yet)"
            )
        else:
            for path in ("/healthz", "/readyz"):
                try:
                    resp = httpx.get(f"{origin}{path}", timeout=10.0)
                    _report(
                        f"Online GET {origin}{path}",
                        "OK" if resp.status_code == 200 else "FAIL",
                        f"HTTP {resp.status_code}",
                    )
                except Exception as exc:
                    _report(f"Online GET {origin}{path}", "FAIL", type(exc).__name__)

    if failures > 0:
        raise typer.Exit(code=1)


@app.command("learn")
def learn_cmd() -> None:
    """Run nightly learning job idempotently (turnout, portions, elasticity, channel stats)."""
    import asyncio

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker
    from hostora.services.learning import run_nightly_learning

    async def _run() -> dict[str, Any]:
        settings = get_settings()
        engine = make_engine(settings)
        maker = make_sessionmaker(engine)
        try:
            async with maker() as session:
                return await run_nightly_learning(session)
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    typer.echo(f"Nightly learning complete: {result}")


@app.command("backtest")
def backtest_cmd(
    min_events: Annotated[int | None, typer.Option(help="Minimum events for backtest")] = None,
) -> None:
    """Run rolling-origin evaluation comparing Phase-1 rules with Bayesian model."""
    import asyncio

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker
    from hostora.services.learning import run_backtest

    async def _run() -> dict[str, Any]:
        settings = get_settings()
        engine = make_engine(settings)
        maker = make_sessionmaker(engine)
        try:
            async with maker() as session:
                return await run_backtest(session, min_events=min_events)
        finally:
            await engine.dispose()

    res = asyncio.run(_run())
    typer.echo(
        f"Backtest result:\n"
        f"  Active model: {res.get('active_model')}\n"
        f"  Events evaluated: {res.get('n_events')}\n"
        f"  Bayesian pinball loss: {res.get('bayesian_loss')}\n"
        f"  Rules pinball loss: {res.get('rules_loss')}\n"
        f"  80% interval coverage: {res.get('coverage_80')}"
    )


@app.command("insights")
def insights_cmd() -> None:
    """Print the status of every learned quantity: value, n, last update, and basis."""
    import asyncio

    from hostora.config import get_settings
    from hostora.db.session import make_engine, make_sessionmaker
    from hostora.services.learning import get_insights_data

    async def _run() -> dict[str, Any]:
        settings = get_settings()
        engine = make_engine(settings)
        maker = make_sessionmaker(engine)
        try:
            async with maker() as session:
                return await get_insights_data(session)
        finally:
            await engine.dispose()

    data = asyncio.run(_run())
    items = data.get("items", [])
    typer.echo(f"{'Quantity Name':<35} {'Value':<30} {'n':<6} {'Basis'}")
    typer.echo("-" * 100)
    for it in items:
        typer.echo(f"{it['name']:<35} {str(it['value'])[:28]:<30} {it['n']:<6} {it['basis']}")


if __name__ == "__main__":
    app()
