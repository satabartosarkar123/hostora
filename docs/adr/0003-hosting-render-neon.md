# ADR 0003: Free-Tier Hosting on Render + Neon + Hugging Face Backup

## Context
Hackathon and portfolio deployments must run at ₹0/month with no credit card and no 30-day trial expiration.

## Decision
Deploy the Docker container to Render's free web service (Singapore region) backed by Neon free Postgres and Cloudflare free DNS/CDN, with a secondary copy on a Hugging Face Docker Space for demo redundancy.

## Consequences
- Zero monthly cost and no credit card required.
- Render sleeps after 15 minutes of inactivity; mitigated during demos by an external keep-warm ping to `/healthz`.

## Alternatives Considered
- Render free Postgres (expires after 30 days) or Cloud Run (requires billing card).

