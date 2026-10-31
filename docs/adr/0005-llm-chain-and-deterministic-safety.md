# ADR 0005: Safety-First LLM Chain with Deterministic Engine Filter

## Context
Hosts rely on dietary and faith rules (halal, Jain, pure veg, no beef, no pork, no alcohol, eggless, vegan) that must never be violated by an LLM hallucination or prompt injection.

## Decision
Use a fallback chain: Groq (primary) -> Gemini (only when `GEMINI_ALLOWED=true` and only with PII-redacted text) -> deterministic popularity template. LLMs only propose item IDs from the pre-filtered catalog; output is validated with Pydantic, restricted to allowed IDs, and re-verified by `hostora.engine.apply_rules`. Quantities, pack rounding, and budget fitting are computed deterministically by `hostora.engine`.

## Consequences
- Provable dietary safety (0 violations across adversarial evaluations).
- Graceful degradation when API keys are absent or rate-limited.

## Alternatives Considered
- Letting the LLM compute quantities or output free-form dish names (unsafe and unverified).

