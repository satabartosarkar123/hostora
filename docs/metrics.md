# Hostora — Verified Metrics & Evidence

Every metric in this document is reproducible from the commands listed below.
Recorded on **31 Oct 2026** on Apple Silicon (`darwin`, Python `3.12.12`, PostgreSQL `18`).

---

## 1. Synthetic Waste Simulation

<!-- SIMULATION_START -->
Command: `uv run hostora simulate --events 10000 --seed 7`

**Synthetic Simulation Notice:** The results below are generated from synthetic events (`events=10000`, `seed=7`) using Beta-distributed attendance around Phase-1 turnout rates (Yes mean 95%, Maybe mean 68%, No-reply mean 28%).

| Ordering Strategy | Mean Over-Ordered Plates (%) | Shortage Probability (%) | Plates Saved per 100 Invited |
| --- | ---: | ---: | ---: |
| All invited seats (baseline) | 74.92% | 0.00% | 0.00 |
| Yes + Maybe seats only | 3.17% | 41.35% | 41.80 |
| Hostora P80 recommendation | 8.41% | 3.45% | 37.59 |
<!-- SIMULATION_END -->

---

## 2. Safety & Privacy Evaluation Set

Commands:
```bash
uv run pytest tests/evals/test_safety_evals.py -m "not llm_live" -v
uv run pytest tests/evals/test_safety_evals.py -m llm_live -v
```

| Dataset | Cases | Forbidden Items in Final Plan | PII Leaks in LLM Prompts | Elapsed |
| --- | ---: | ---: | ---: | ---: |
| `data/evals/briefs.jsonl` (realistic multi-faith briefs) | 52 | **0 (0.00%)** | **0 (0.00%)** | < 0.20 s |
| `data/evals/adversarial.jsonl` (direct, misspelt, Hindi/Hinglish, prompt injection, allergy injection, PII) | 120 | **0 (0.00%)** | **0 (0.00%)** | < 0.20 s |
| `test_live_llm_safety_eval_subset` (live Groq `llama-3.1-8b-instant`) | 10 | **0 (0.00%)** | **0 (0.00%)** | 0.57 s |

---

## 3. Load Test (Locust)

Command:
```bash
uv run locust -f loadtest/locustfile.py --headless -H http://127.0.0.1:18080 -u 25 -r 25 -t 6s --only-summary
```

Workload: 25 concurrent users (`20 GuestUser` opening an invite and submitting an idempotent RSVP + `5 HostUser` polling live event summary; > 25× the target peak of 300 guests replying within 10 minutes).

| Endpoint | Requests | Failures | Median (p50) | p95 | p99 | Throughput (RPS) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `GET /v1/invites/[event_key]/[token]` | 213 | 0 (0.00%) | 7 ms | 55 ms | 74 ms | 36.02 req/s |
| `PUT /v1/invites/[event_key]/[token]/rsvp` | 213 | 0 (0.00%) | 10 ms | 56 ms | 64 ms | 36.02 req/s |
| `GET /v1/events/[event_key]/summary` | 30 | 0 (0.00%) | 10 ms | 74 ms | 93 ms | 5.07 req/s |
| **Aggregated** | **456** | **0 (0.00%)** | **9 ms** | **56 ms** | **74 ms** | **77.12 req/s** |

---

## 4. Guest Page-Weight & Accessibility Gates

Commands:
```bash
uv run python scripts/check_page_weight.py
uv run pytest tests/e2e/test_a11y.py -v
```

| Gate | Budget / Threshold | Measured Value | Status |
| --- | --- | --- | --- |
| Guest invite page transfer size (Playwright mobile Chromium) | ≤ 150.0 KB | **6.23 KB** (6,378 bytes across 5 responses) | PASS |
| Accessibility (`axe-playwright-python` on invite, done, dashboard, gate) | 0 serious / critical violations | **0 violations** | PASS |

---

## 5. Test Suite, Static Analysis & Coverage

Commands:
```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run hostora data-check
uv run alembic check
uv run pytest -m "not e2e and not llm_live" --cov --cov-report=term
uv run coverage report --fail-under=85
uv run coverage report --include='src/hostora/engine/*' --fail-under=95
uv run pytest -m e2e
```

| Gate | Threshold | Measured Value |
| --- | --- | --- |
| `ruff check` + `ruff format --check` | 0 findings | **0 findings** (117 files) |
| `mypy --strict` | 0 errors | **0 errors** (92 source files) |
| Unit, property, eval & integration tests (Neon `TEST_DATABASE_URL`) | 100% pass | **75 passed** |
| End-to-end Playwright browser tests (`-m e2e`, Neon `TEST_DATABASE_URL`) | 100% pass | **5 passed** (13.11 s: guest RSVP + no-JS, host CSV + SSE, planner stream, offline gate, axe a11y) |
| Overall test coverage | ≥ 85% | **86%** |
| Pure engine coverage (`src/hostora/engine/*`) | ≥ 95% | **96%** |

---

## 6. Post-Hackathon Quantile Turnout Model (M9)

Command:
```bash
uv run python ml/train_turnout.py
```

| Metric (400 held-out synthetic events, seed=42) | Phase-1 Heuristic | M9 Quantile-Calibrated Model |
| --- | ---: | ---: |
| Holdout Mean Absolute Error (MAE, seats) | 11.48 | **10.97** |
| Holdout P50 Pinball Loss | 5.74 | **5.49** |

### 2.1 Grounded Open-World Safety Evals (Milestone M5-G)

Dataset: `data/evals/grounded_evals.jsonl` (325 cases across 8 Indian regions and faiths)

| Metric | Result | Benchmark Target |
| --- | ---: | ---: |
| **Unsafe Auto-Approvals** | **0 (0.00%)** | **0 (Strict requirement)** |
| Auto-Approval Rate | 16.9% | Useful baseline |
| Confirmation Rate | 35.7% | Conservative fail-closed |
| Removal Rate | 47.4% | Matches forbidden rules |
| Mean Evidence Latency | 0.02 ms | < 50 ms in-memory |
| Calibrated Conformal Bound (alpha=0.01) | <= 1.0% | Error rate guarantee |
