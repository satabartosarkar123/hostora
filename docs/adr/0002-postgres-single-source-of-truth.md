# ADR 0002: Postgres (Neon) as Single Source of Truth

## Context
Event RSVP submissions require strict idempotency, concurrency safety when multiple family members submit simultaneously, and an audit trail for turnout modelling.

## Decision
Use Postgres 16+ (Neon) as the single source of truth via SQLAlchemy 2 async ORM and `asyncpg`. Enforce consistency with `SELECT ... FOR UPDATE` on the household row, an `idempotency` table keyed by `(scope, key)`, and an append-only `event_log` written in the same database transaction.

## Consequences
- Exactly-once RSVP processing without needing an external message queue.
- Neon's free tier requires no credit card and does not expire after 30 days.

## Alternatives Considered
- Distributed actors + async queue consumer (adds operational complexity and multiple state stores).
