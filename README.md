# Hostora

Confirmed headcount in, a priced, diet-safe food and shopping plan out, for Indian weddings of any faith and home parties.

> Status: starter repository. The domain engine is implemented and tested; the rest is built milestone by milestone from [AGENTS.md](AGENTS.md).

## Quick start

```bash
uv sync --all-groups
uv run pytest            # engine tests, including property-based tests
uv run ruff check . && uv run mypy
uv run hostora gen-keys  # prints fresh secrets for .env
```

## What's here

- `src/hostora/engine/`: household codes and signed links, diet and faith rule filtering, quantity and pack-size maths, turnout prediction, budget fitting, Ed25519 gate passes. Pure Python, no I/O.
- `data/`: seed catalog, consumption rates, rule sets, tradition templates, shopping channels. Values marked `seed-unvalidated` are illustrative only.
- `AGENTS.md`: the full build specification (architecture decisions, schema, routes, milestones, deployment and the hackathon kit).

Stack: Python 3.12, FastAPI, Jinja2 + HTMX, SQLAlchemy 2 + Postgres (Neon), Firebase Auth, Groq/Gemini with a deterministic fallback, deployed on free tiers (Render + Neon).
