# ADR 0001: FastAPI Modular Monolith

## Context
Hostora needs to serve lightweight guest invitation pages that work on slow Android phones without JavaScript, an interactive host dashboard with live updates, a versioned JSON API (`/v1`), Server-Sent Events (SSE), and internal cron hooks—all on free hosting tiers without a credit card.

## Decision
Build a single Python FastAPI modular monolith that serves server-rendered pages (Jinja2 + HTMX + Alpine.js), the `/v1` JSON API (with OpenAPI docs at `/docs`), SSE streams, and internal cron endpoints. Client-side JavaScript is used only where the browser must perform local work: the offline Ed25519 QR gate and Firebase Google sign-in.

## Consequences
- Single deployable container on a free tier.
- Guest pages stay under 150 KB and work even with JavaScript disabled.
- The entire application, deterministic engine, and test suite live in one Python codebase.

## Alternatives Considered
- Separate SPA frontend + backend microservice (increases bundle weight, deployment complexity, and CORS overhead).

