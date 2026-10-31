# Hostora test report

Machine: macOS Darwin arm64, Apple M2, 16 GB RAM · Python: 3.12.12 · Commit: 58deb7f · Date: 2026-10-31

| Gate | Check | Command | Result | Key numbers | Date |
| --- | --- | --- | --- | --- | --- |
| T1 | lockfile | `uv lock --check` | PASS | 107 packages resolved in 22ms | 2026-10-31 |
| T1 | ruff lint | `uv run ruff check .` | PASS | 0 findings | 2026-10-31 |
| T1 | ruff format | `uv run ruff format --check .` | PASS | 117 files formatted | 2026-10-31 |
| T1 | mypy strict | `uv run mypy` | PASS | 0 errors across 92 source files | 2026-10-31 |
| T1 | data check | `uv run hostora data-check` | PASS | 24 catalog items, 28 consumption rows, 8 rules, 7 channels | 2026-10-31 |
| T1 | pre-commit | `uv run pre-commit run --all-files` | PASS | 6/6 hooks passed | 2026-10-31 |
| T1 | doctor | `uv run hostora doctor` | PASS | All offline checks OK, 0 FAIL | 2026-10-31 |
| T1 | debug/TODO check | `grep -rnE "print\(|breakpoint\(|TODO|FIXME" src/` | PASS | 0 occurrences | 2026-10-31 |
| T2 | unit + property | `HYPOTHESIS_PROFILE=release uv run pytest tests/engine tests/unit -v` | PASS | 54 passed, 0 skipped, 2000 examples per property (4.08s) | 2026-10-31 |
| T3 | doctor guard | `uv run hostora doctor` | PASS | Safety guard (`TEST_DATABASE_URL != DATABASE_URL`) OK | 2026-10-31 |
| T3 | alembic migrations | `DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head / check / downgrade base / upgrade head` | PASS | 0 drift (`No new upgrade operations detected`) | 2026-10-31 |
| T3 | integration | `uv run pytest tests/integration -v` | PASS | 21 passed, 0 skipped (270.06s) | 2026-10-31 |
| T3 | coverage | `uv run coverage report --fail-under=85` & `--include='src/hostora/engine/*' --fail-under=95` | PASS | 86% overall (3963 stmts), 96% engine (377 stmts), 77 passed | 2026-10-31 |
| T4 | security suite | `uv run pytest tests/security -v` | PASS | 5 passed (11 security assertions verified) | 2026-10-31 |
| T4 | pip-audit | `uv export --no-dev ... && uvx pip-audit --disable-pip -r /tmp/req.txt` | PASS | 0 known vulnerabilities across 107 packages | 2026-10-31 |
| T4 | detect-secrets | `uvx detect-secrets scan --all-files --exclude-files 'uv\.lock'` | PASS | 0 real secrets leaked in tracked repository files | 2026-10-31 |
| T4 | git history scan | `git log --all -p \| grep -nE 'npg_[A-Za-z0-9]{8,}\|gsk_[A-Za-z0-9]{20,}\|BEGIN [A-Z ]*PRIVATE KEY'` | PASS | 0 matches (no output) | 2026-10-31 |
| T5 | AI safety evals (offline) | `uv run pytest tests/evals -v` | PASS | 150 eval cases (50 realistic briefs + 100 adversarial attack cases), 0 violations, 0 PII in prompts | 2026-10-31 |
| T5 | AI safety evals (live Groq) | `uv run pytest -m llm_live -v` | PASS | 20 cases (10 adversarial + 10 multi-faith briefs) against Groq `gpt-oss-120b`, 0 violations (17.5s) | 2026-10-31 |
| T6 | e2e | `uv run pytest -m e2e --browser chromium --browser webkit` | PASS | 10 passed across Chromium and WebKit (a11y, gate offline, guest RSVP, host flow, planner stream) | 2026-10-31 |
| T6 | page weight | `scripts/check_page_weight.py` | PASS | 6,378 bytes (6.23 KB) / budget 150.00 KB | 2026-10-31 |
| T7 | resilience | `uv run pytest tests/integration/test_resilience.py -v` | PASS | 4 passed (bad DB URL -> 503, Groq 429 fallback -> template, SSE disconnect cleanup, killed plan cleanup) | 2026-10-31 |
| T7 | simulator | `uv run hostora simulate --events 10000 --seed 7` | PASS | Byte-for-byte deterministic across 2 consecutive runs | 2026-10-31 |
| T7 | load (local) | locust 100 users 2 min (`--csv loadtest/results/local`) | PASS | 1,161 reqs, 0 failures (0.00% error rate), 0 5xx errors | 2026-10-31 |
| T8 | container | `docker build -t hostora:test . && docker run ...` | PASS | 83.2 MB image size, non-root user `app`, /healthz + /readyz + /docs 200, 0 secret leaks | 2026-10-31 |
| T9 | manual walkthrough | checklist | | | |
| P1 | smoke (prod) | `pytest -m smoke` | | | |
| P2 | PageSpeed / headers / SSL | external | | scores | |
| P6 | load (prod) | locust 20 users 1 min | | p50, p95 | |

Notes and justifications (skipped items, accepted audit findings):
- T0: Verified `.env` is git-ignored (`git check-ignore .env` -> `.env`) and untracked (`git ls-files | grep -i env` -> `.env.example` only). Distinct `DATABASE_URL` and `TEST_DATABASE_URL` enforced by `assert_distinct_test_db`.

