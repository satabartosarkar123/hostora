.PHONY: install check lint type test test-db e2e fmt db-up db-down migrate run seed

export DATABASE_URL ?= postgresql://hostora:hostora@localhost:5432/hostora
export TEST_DATABASE_URL ?= postgresql://hostora:hostora@localhost:5432/hostora_test

install:
	uv sync --all-groups
	uv run playwright install chromium

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy

test:
	uv run pytest -m "not e2e and not llm_live" --cov --cov-report=term

e2e:
	uv run pytest -m e2e

check: lint type test

db-up:
	docker compose up -d db

db-down:
	docker compose down

migrate:
	uv run alembic upgrade head

run:
	uv run uvicorn --factory hostora.main:create_app --reload --port 8000

seed:
	uv run hostora seed-demo
