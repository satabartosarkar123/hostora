# ADR 0007: Modern Python Tooling Stack

## Context
Fast, reproducible dependency management, strict static typing, property-based testing, and browser E2E verification are required to maintain high engineering quality.

## Decision
Standardize on `uv` (`uv.lock`), `ruff` (lint + format), `mypy --strict`, `pytest` + `pytest-asyncio` + `Hypothesis` + `respx`, `pytest-playwright` + `axe-playwright-python`, and `Locust`.

## Consequences
- Sub-second linting, strict type safety across ORM and Pydantic schemas, and automated property + accessibility testing.

## Alternatives Considered
- Poetry + Flake8 + Black.

