# Hostora test report

Machine: macOS Darwin arm64, Apple M2, 16 GB RAM · Python: 3.12.12 · Commit: 8e0f801 · Date: 2026-11-07

| Gate | Check | Command | Result | Key numbers | Date |
| --- | --- | --- | --- | --- | --- |
| T1 | lockfile | `uv lock --check` | PASS | 109 packages resolved | 2026-11-07 |
| T1 | ruff lint | `uv run ruff check .` | PASS | 0 findings across 155 files | 2026-11-07 |
| T1 | ruff format | `uv run ruff format --check .` | PASS | 155 files already formatted | 2026-11-07 |
| T1 | mypy strict | `uv run mypy` | PASS | 0 errors across 115 source files | 2026-11-07 |
| T1 | data check | `uv run hostora data-check` | PASS | version=908afd37a584 rules=8 channels=7 concepts=27 sources=8 | 2026-11-07 |
| T1 | doctor | `uv run hostora doctor` | PASS | All offline checks OK (including STORE_TOKEN_KEY), 0 FAIL, safety guard OK | 2026-11-07 |
| T1 | days_left check | `grep -rn "days_left" src/` | PASS | 0 occurrences in `src/` | 2026-11-07 |
| T2 | unit + property | `HYPOTHESIS_PROFILE=release uv run pytest tests/engine tests/unit -q` | PASS | 122 passed (engine + unit with Hypothesis release profile) | 2026-11-07 |
| T3 | alembic migrations | `ALEMBIC_USE_TEST_DB=1 uv run alembic check` | PASS | 0 schema drift (`No new upgrade operations detected`, through `0004_stores`) | 2026-11-07 |
| T3 | DB, integration & simulation | `uv run pytest -m "not e2e and not llm_live and not knowledge_live and not store_live" --cov --cov-report=term -q` | PASS | 177 passed, overall coverage 87% (`>= 85%`) | 2026-11-07 |
| T3 | engine coverage | `uv run coverage report --include="src/hostora/engine/*" --fail-under=95` | PASS | 97% engine coverage (`>= 95%`) | 2026-11-07 |
| T4 | security suite | `uv run pytest tests/security -q` | PASS | 6 passed | 2026-11-07 |
| T4 | git history scan | `git log --all -p \| grep -nE "npg_[A-Za-z0-9]{8,}\|gsk_[A-Za-z0-9]{20,}\|BEGIN [A-Z ]*PRIVATE KEY"` | PASS | 0 secrets leaked (`NO_SECRETS_IN_GIT_HISTORY`) | 2026-11-07 |
| T5 | AI safety evals | `uv run pytest tests/evals -m "not llm_live" -q` | PASS | 5 passed (337 cases in grounded_evals.jsonl, 0 unsafe auto-approvals) | 2026-11-07 |
| T5 | conformal calibration | `uv run hostora calibrate --alpha 0.01` | PASS | alpha=0.01, tau=0.670, empirical risk=0.0000 on n=150 | 2026-11-07 |
| T6 | Playwright E2E | `uv run pytest -m e2e --browser chromium --browser webkit -q` | PASS | 10 passed (5 E2E suites × Chromium & WebKit, including 120-min planner & carts + axe a11y) | 2026-11-07 |
| T6 | page weight | `uv run python scripts/check_page_weight.py` | PASS | 6,376 bytes (6.23 KB) / budget 150.00 KB | 2026-11-07 |
| T7 | resilience (incl. all stores down) | `uv run pytest tests/integration/test_resilience.py -q` | PASS | 5 passed (including all connected MCP stores down fallback) | 2026-11-07 |
| T7 | waste simulation | `uv run hostora simulate --events 10000 --seed 7` | PASS | P80 over-order 8.41%, shortage 3.45%, plates saved 37.59/100 | 2026-11-07 |
| T7 | load test | `uv run python scripts/run_locust.py` | PASS | 0 failures (0.00% error rate), exit code 0 | 2026-11-07 |
| T8 | container build | `docker build -t hostora:test .` | PASS | Image `hostora:test` built (21/21 layers), non-root user `app` | 2026-11-07 |
| T9 | manual walkthrough | checklist | PENDING | Manual verification checklist ready | 2026-11-07 |
| P1 | smoke (prod) | `pytest -m smoke` | PENDING | Pending live deployment | 2026-11-07 |
| P2 | PageSpeed / headers / SSL | external | PENDING | Pending live deployment | 2026-11-07 |
| P6 | load (prod) | locust 20 users 1 min | PENDING | Pending live deployment | 2026-11-07 |

Notes and justifications:
- T0: Verified `.env` is git-ignored and untracked. Distinct `DATABASE_URL` and `TEST_DATABASE_URL` verified and enforced by doctor and test fixtures.
- T1–T8: All quality gates passed with zero weakened or xfailed tests. Milestones M5-G, M5-L, and M5-R (real-time ordering, delivery feasibility, MCP store adapters, and one-tap carts) are fully integrated and verified.
