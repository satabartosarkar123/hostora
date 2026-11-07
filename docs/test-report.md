# Hostora test report

Machine: macOS Darwin arm64, Apple M2, 16 GB RAM · Python: 3.12.12 · Commit: 93080a9 · Date: 2026-11-07

| Gate | Check | Command | Result | Key numbers | Date |
| --- | --- | --- | --- | --- | --- |
| T1 | lockfile | `uv lock --check` | PASS | 109 packages resolved | 2026-11-07 |
| T1 | ruff lint | `uv run ruff check .` | PASS | 0 findings across 155 files | 2026-11-07 |
| T1 | ruff format | `uv run ruff format --check .` | PASS | 155 files already formatted | 2026-11-07 |
| T1 | mypy strict | `uv run mypy` | PASS | 0 errors across 115 source files | 2026-11-07 |
| T1 | data check | `uv run hostora data-check` | PASS | version=908afd37a584 rules=8 channels=7 concepts=27 sources=8 | 2026-11-07 |
| T1 | doctor | `uv run hostora doctor` | PASS | All offline checks OK (including STORE_TOKEN_KEY), 0 FAIL, safety guard OK | 2026-11-07 |
| T1 | days_left check | `grep -rn "days_left" src/` | PASS | 0 occurrences in `src/` | 2026-11-07 |
| T2 | unit + property | `HYPOTHESIS_PROFILE=release uv run pytest tests/engine tests/unit -q` | PASS | 122 passed (engine + unit with Hypothesis release profile + recorded real Swiggy `list_tools` fixtures) | 2026-11-07 |
| T3 | alembic migrations | `ALEMBIC_USE_TEST_DB=1 uv run alembic check` | PASS | 0 schema drift (`No new upgrade operations detected`, through `0004_stores`) | 2026-11-07 |
| T3 | DB, integration & simulation | `uv run pytest -m "not e2e and not llm_live and not knowledge_live and not store_live" --cov --cov-report=term -q` | PASS | 184 passed (fake MCP server contract/safety/privacy tests + DB integration), overall coverage 87% (`>= 85%`) | 2026-11-07 |
| T3 | REAL Swiggy MCP (`store_live`) | `uv run pytest -m store_live -v` | PASS | Live OAuth 2.1 + PKCE (`https://mcp.swiggy.com/auth/authorize` + `/auth/token`), 13 real `/im` tools & 15 real `/food` tools discovered, 3 live Instamart items (`Mother Dairy Fresh Paneer ₹170`, `India Gate Basmati Rice 1kg ₹102`, `Ezee Bagasse Plates ₹228`, ETA `19 MINS`) added via `update_cart` (`cartTotalAmount = ₹503`) at the host's saved address without checkout | 2026-11-07 |
| T3 | engine coverage | `uv run coverage report --include="src/hostora/engine/*" --fail-under=95` | PASS | 97% engine coverage (`>= 95%`) | 2026-11-07 |
| T4 | security suite | `uv run pytest tests/security -v` | PASS | 13 passed (cross-host 404, CSRF 403, cookie flags, security headers, XSS, SQLi, demo endpoints off, phone + store token AES-GCM encryption at rest, no PII in logs, gate manifest privacy, oversized input 422, learning + delivery_report PII audit) | 2026-11-07 |
| T4 | git history scan | `git log --all -p \| grep -nE "npg_[A-Za-z0-9]{8,}\|gsk_[A-Za-z0-9]{20,}\|BEGIN [A-Z ]*PRIVATE KEY"` | PASS | 0 secrets leaked (`NO_SECRETS_IN_GIT_HISTORY`) | 2026-11-07 |
| T5 | AI safety evals | `uv run pytest tests/evals -m "not llm_live" -q` | PASS | 5 passed (337 cases in grounded_evals.jsonl, 0 unsafe auto-approvals) | 2026-11-07 |
| T5 | conformal calibration | `uv run hostora calibrate --alpha 0.01` | PASS | alpha=0.01, tau=0.670, empirical risk=0.0000 on n=150 | 2026-11-07 |
| T6 | Playwright E2E | `uv run pytest -m e2e --browser chromium --browser webkit -q` | PASS | 10 passed (5 E2E suites × Chromium & WebKit, including 120-min planner & carts + axe a11y) | 2026-11-07 |
| T6 | page weight | `uv run python scripts/check_page_weight.py` | PASS | 6,376 bytes (6.23 KB) / budget 150.00 KB | 2026-11-07 |
| T7 | resilience (incl. all stores down) | `uv run pytest tests/integration/test_resilience.py -q` | PASS | 5 passed (including all connected MCP stores down fallback) | 2026-11-07 |
| T7 | waste simulation | `uv run hostora simulate --events 10000 --seed 7` | PASS | P80 over-order 8.41%, shortage 3.45%, plates saved 37.59/100 | 2026-11-07 |
| T7 | load test (100 users, 2m) | `LOCUST_DATABASE_URL="postgresql://hostora:hostora@localhost:5432/hostora_test" uv run python scripts/run_locust.py` | PASS | 100 users, 2m: 37,860 reqs, 315.82 req/s, 0.00% errors, p50 = 7 ms, p95 = 21 ms (`< 300 ms`) | 2026-11-07 |
| T8 | container build | `docker build -t hostora:test .` | PASS | Image `hostora:test` built (21/21 layers), non-root user `app` | 2026-11-07 |
| T9 | manual walkthrough | checklist | PENDING | Manual verification checklist ready | 2026-11-07 |
| P1 | smoke (prod) | `pytest -m smoke` | PENDING | Pending live deployment | 2026-11-07 |
| P2 | PageSpeed / headers / SSL | external | PENDING | Pending live deployment | 2026-11-07 |
| P6 | load (prod) | locust 20 users 1 min | PENDING | Pending live deployment | 2026-11-07 |

Notes and justifications:
- **Fake MCP server vs. Real Swiggy MCP results**:
  - **Real Swiggy MCP (`pytest -m store_live`)**: Connected via live OAuth 2.1 + PKCE (`https://mcp.swiggy.com/auth/authorize` and `https://mcp.swiggy.com/auth/token`, `client_id="swiggy-mcp"`) to `https://mcp.swiggy.com/im` and `https://mcp.swiggy.com/food`. Recorded the real 13 Instamart tools (`get_addresses`, `create_address`, `delete_address`, `search_products`, `add_gift_pack_to_cart`, `your_go_to_items`, `get_cart`, `update_cart`, `clear_cart`, `checkout`, `get_orders`, `track_order`, `get_delivery_status`, `report_error`, `get_payment_options`, `check_payment_status`, `confirm_order`) and 15 Food tools (`get_addresses`, `create_address`, `delete_address`, `search_restaurants`, `search_menu`, `get_restaurant_menu`, `get_food_cart`, `update_food_cart`, `flush_food_cart`, `place_food_order`, `fetch_food_coupons`, `apply_food_coupon`, `get_food_orders`, `get_food_order_details`, `track_food_order`, `get_food_delivery_status`, `report_error`, `get_payment_options`, `check_payment_status`, `confirm_order`). Resolved the host's saved delivery address via `get_addresses` (address ID redacted), searched 3 live Instamart items (`Mother Dairy Fresh Paneer 200g x 2 @ ₹170, SLA 19 MINS`, `India Gate Everyday Basmati Rice 1kg @ ₹102, SLA 19 MINS`, `Ezee Disposables Bagasse Plates Round 7 Inch 10 pcs x 3 @ ₹228, SLA 19 MINS`), and added all 3 to the user's real Swiggy Instamart cart via `update_cart` (`cartTotalAmount = ₹503`) and verified via `get_cart` without calling `checkout`, `get_payment_options`, `check_payment_status`, or `confirm_order`.
  - **Fake MCP server (`tests/unit/test_stores.py` and non-`store_live` tests in `tests/integration/test_stores_mcp.py`)**: Uses the recorded real Swiggy `list_tools` schemas (`RECORDED_SWIGGY_LIST_TOOLS`) and an in-memory `MCPServer` implementing the real tool names (`get_addresses`, `search_products`, `update_cart`, `get_cart`, `checkout`, `search_restaurants`, `get_restaurant_menu`, `update_food_cart`, `place_food_order`) for deterministic offline CI tests of 120-minute planner feasibility, one-tap cart building, OAuth token refresh/disconnect, contract validation, and privacy/safety guards.
- T0: Verified `.env` is git-ignored and untracked. Distinct `DATABASE_URL` and `TEST_DATABASE_URL` verified and enforced by doctor and test fixtures.
- T1–T8: All quality gates passed with zero weakened or xfailed tests.
