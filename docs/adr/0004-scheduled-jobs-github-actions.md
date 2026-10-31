# ADR 0004: Scheduled Jobs via GitHub Actions Cron Workflows

## Context
Retention purges, rate-limit/cache cleanup, and encrypted Postgres backups must run on a schedule without paying for a dedicated background worker instance.

## Decision
Run scheduled jobs as GitHub Actions cron workflows that call HMAC-signed `/internal/cron/{job}` endpoints (`X-Cron-Timestamp`, `X-Cron-Signature`), and run `pg_dump` encrypted with AES-256-CBC uploaded as a 30-day workflow artifact.

## Consequences
- No always-on background worker needed.
- Cryptographically verified cron execution.

## Alternatives Considered
- Celery / arq Beat worker (requires an extra paid instance + Redis).
