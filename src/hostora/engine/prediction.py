"""Phase-1 turnout prediction (rule-based; replaced by the ML model in M9).

Defaults come from the BRD research (finding F2): about 3-5% of confirmed guests
do not come, and planners count roughly 70% of "maybe" replies. Low and high
rates are starting heuristics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReplySeats:
    yes: int
    maybe: int
    no_reply: int  # seat caps of households that have not replied


@dataclass(frozen=True, slots=True)
class Rates:
    yes: float
    maybe: float
    no_reply: float


LIKELY = Rates(yes=0.96, maybe=0.70, no_reply=0.30)
LOW = Rates(yes=0.93, maybe=0.50, no_reply=0.15)
HIGH = Rates(yes=0.99, maybe=0.85, no_reply=0.45)


@dataclass(frozen=True, slots=True)
class Prediction:
    low: int
    likely: int
    high: int
    p80: int  # buffer reference and recommended plate count


def _apply(s: ReplySeats, r: Rates) -> float:
    return s.yes * r.yes + s.maybe * r.maybe + s.no_reply * r.no_reply


def predict_arrivals(seats: ReplySeats) -> Prediction:
    for v in (seats.yes, seats.maybe, seats.no_reply):
        if v < 0:
            raise ValueError("seat counts must be non-negative")
    low = math.floor(_apply(seats, LOW))
    likely = round(_apply(seats, LIKELY))
    high = math.ceil(_apply(seats, HIGH))
    p80 = round(likely + 0.6 * (high - likely))
    return Prediction(low=low, likely=likely, high=high, p80=p80)


def predict_arrivals_calibrated(
    seats: ReplySeats,
    *,
    low_rates: Rates = LOW,
    likely_rates: Rates = LIKELY,
    high_rates: Rates = HIGH,
) -> Prediction:
    """Predict turnout using quantile-calibrated rates (`low <= likely <= p80 <= high`)."""
    for v in (seats.yes, seats.maybe, seats.no_reply):
        if v < 0:
            raise ValueError("seat counts must be non-negative")
    raw_low = max(0, math.floor(_apply(seats, low_rates)))
    raw_likely = max(raw_low, round(_apply(seats, likely_rates)))
    raw_high = max(raw_likely, math.ceil(_apply(seats, high_rates)))
    p80 = round(raw_likely + 0.6 * (raw_high - raw_likely))
    return Prediction(low=raw_low, likely=raw_likely, high=raw_high, p80=p80)
