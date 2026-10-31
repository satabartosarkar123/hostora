# ADR 0006: Realtime Updates via Server-Sent Events (SSE)

## Context
The host dashboard and food planner need live streaming updates as guests reply or as plan categories are computed.

## Decision
Use Server-Sent Events (`sse-starlette`) with an in-process `Broadcaster` on a single instance, and a `PgNotifyBroadcaster` (`LISTEN/NOTIFY`) when scaling to multiple instances in M9.

## Consequences
- Works seamlessly with HTMX (`hx-ext="sse"`) and standard HTTP proxies without custom WebSocket state management.

## Alternatives Considered
- WebSockets or polling.
