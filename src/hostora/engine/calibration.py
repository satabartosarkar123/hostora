"""Choose grounding thresholds from data instead of by hand (conformal risk control).

Given a calibration set of proposed items, each with a confidence score (e.g. verifier
agreement) and whether accepting it would have been unsafe, pick the smallest threshold whose
conformal upper bound on the unsafe-acceptance rate is at most ``alpha``:

    (sum of losses among accepted + 1) / (n + 1) <= alpha

with loss = 1 when an item with score >= threshold is unsafe. Because the loss can only fall
as the threshold rises, the smallest passing threshold maximises automatic approvals while
keeping the guarantee (Angelopoulos et al., "Conformal Risk Control"; Abbasi Yadkori et al.,
"Mitigating LLM Hallucinations via Conformal Abstention", 2024). Calibrate on held-out cases
that match production; rerun whenever models or sources change.
"""

from __future__ import annotations

from collections.abc import Sequence


def conformal_threshold(
    scores: Sequence[float], unsafe: Sequence[bool], alpha: float
) -> float | None:
    """Smallest threshold meeting the bound, or ``None`` if no threshold can (too few
    calibration points for this ``alpha``: need n >= 1/alpha - 1)."""
    if len(scores) != len(unsafe):
        raise ValueError("scores and unsafe must have the same length")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    n = len(scores)
    candidates = [*sorted(set(scores)), float("inf")]
    for t in candidates:
        losses = sum(1 for s, bad in zip(scores, unsafe, strict=True) if s >= t and bad)
        if (losses + 1) / (n + 1) <= alpha:
            return t
    return None


def empirical_risk(scores: Sequence[float], unsafe: Sequence[bool], threshold: float) -> float:
    """Share of all items that would be accepted and unsafe at ``threshold``."""
    if not scores:
        return 0.0
    bad = sum(1 for s, u in zip(scores, unsafe, strict=True) if s >= threshold and u)
    return bad / len(scores)
