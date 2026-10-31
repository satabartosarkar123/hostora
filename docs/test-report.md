# Hostora test report

Machine: macOS Darwin arm64, Apple M2, 16 GB RAM · Python: 3.12.12 · Commit: 25e0e4a · Date: 2026-10-31

| Gate | Check | Command | Result | Key numbers | Date |
| --- | --- | --- | --- | --- | --- |
| T1 | lockfile | `uv lock --check` | PASS | 107 packages resolved in 4ms | 2026-10-31 |
| T1 | ruff lint | `uv run ruff check .` | PASS | 0 findings across 133 files | 2026-10-31 |
| T1 | ruff format | `uv run ruff format --check .` | PASS | 133 files already formatted | 2026-10-31 |
| T1 | mypy strict | `uv run mypy` | PASS | 0 errors across 103 source files | 2026-10-31 |
| T1 | data check | `uv run hostora data-check` | PASS | version=6016d397a1c6 rules=8 channels=7 concepts=27 sources=6 | 2026-10-31 |
| T1 | pre-commit | `uv run pre-commit run --all-files` | PASS | 6/6 hooks passed | 2026-10-31 |
| T1 | doctor | `uv run hostora doctor` | PASS | All offline checks OK, 0 FAIL, safety guard OK | 2026-10-31 |
| T1 | debug/TODO check | `grep -rnE "breakpoint\(" src/` | PASS | 0 occurrences | 2026-10-31 |
| T2 | unit + property | `HYPOTHESIS_PROFILE=release uv run pytest tests/engine tests/unit -v` | PASS | 81 passed in 10.81s (45 engine + 36 unit) | 2026-10-31 |
| T3 | doctor guard | `uv run hostora doctor` | PASS | Safety guard (`TEST_DATABASE_URL != DATABASE_URL`) OK | 2026-10-31 |
| T3 | alembic migrations | `DATABASE_URL=$TEST_DATABASE_URL uv run alembic check` | PASS | 0 schema drift (`No new upgrade operations detected`) | 2026-10-31 |
| T3 | integration | `uv run pytest tests/integration -v` | PASS | 25 passed in 279.92s | 2026-10-31 |
| T3 | coverage | `uv run pytest tests/engine --cov=hostora.engine` | PASS | 96% engine coverage (729 stmts, 0 missing in core) | 2026-10-31 |
| T4 | security suite | `uv run pytest tests/security -v` | PASS | 5 passed in 59.45s (11 security assertions verified) | 2026-10-31 |
| T4 | pip-audit | `uvx pip-audit` | PASS | 0 known vulnerabilities across dependencies | 2026-10-31 |
| T4 | git history scan | `git log -p \| grep -E "AKIA\|ghp_\|AIza\|Bearer "` | PASS | 0 secrets leaked (only docs/test code matched) | 2026-10-31 |
| T5 | AI safety evals | `uv run pytest tests/evals -v` | PASS | 6 passed in 21.95s (325 cases in grounded_evals.jsonl, 0 unsafe auto-approvals, PII-free) | 2026-10-31 |
| T5 | conformal calibration | `uv run hostora calibrate --alpha 0.01` | PASS | alpha=0.01, tau=0.670, empirical risk=0.0000 on n=150 | 2026-10-31 |
| T6 | Playwright E2E | `uv run pytest tests/e2e -v` | PASS | 5 passed in 103.09s (a11y, gate offline, guest RSVP, host flow, planner flow) | 2026-10-31 |
| T6 | page weight | `python scripts/check_page_weight.py` | PASS | 6,376 bytes (6.23 KB) / budget 150.00 KB | 2026-10-31 |
| T7 | waste simulation | `uv run hostora simulate --events 10000 --seed 7` | PASS | P80 over-order 8.41%, shortage 3.45%, plates saved 37.59/100 | 2026-10-31 |
| T7 | load test | `locust -f loadtest/locustfile.py --headless -u 10 -r 2 -t 15s` | PASS | 35 reqs, 0 failures (0.00% error rate), exit code 0 | 2026-10-31 |
| T8 | container build | `docker build -t hostora:test .` | PASS | Image hostora:test built, non-root user `app`, hostora CLI verified | 2026-10-31 |
| T9 | manual walkthrough | checklist | PENDING | Manual verification checklist ready | 2026-10-31 |
| P1 | smoke (prod) | `pytest -m smoke` | PENDING | Pending live deployment | 2026-10-31 |
| P2 | PageSpeed / headers / SSL | external | PENDING | Pending live deployment | 2026-10-31 |
| P6 | load (prod) | locust 20 users 1 min | PENDING | Pending live deployment | 2026-10-31 |

Notes and justifications:
- T0: Verified `.env` is git-ignored and untracked. Distinct `DATABASE_URL` and `TEST_DATABASE_URL` verified and enforced by doctor and test fixtures.
- T1–T8: All quality gates passed with zero skipped, weakened, or xfailed tests.
