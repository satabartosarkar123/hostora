/** Seats by reply state for one function. noReply = seat caps of households that have not replied. */
export interface ReplySeats {
  yes: number;
  maybe: number;
  noReply: number;
}

export interface Rates {
  yes: number;
  maybe: number;
  noReply: number;
}

/** Phase 1 defaults from the BRD (F2). Low and high are starting heuristics, replaced by the model. */
export const LIKELY: Rates = { yes: 0.96, maybe: 0.7, noReply: 0.3 };
export const LOW: Rates = { yes: 0.93, maybe: 0.5, noReply: 0.15 };
export const HIGH: Rates = { yes: 0.99, maybe: 0.85, noReply: 0.45 };

export interface Prediction {
  low: number;
  likely: number;
  high: number;
  /** used as the buffer reference and as the recommended plate count */
  p80: number;
}

function apply(s: ReplySeats, r: Rates): number {
  return s.yes * r.yes + s.maybe * r.maybe + s.noReply * r.noReply;
}

export function predictArrivals(seats: ReplySeats): Prediction {
  for (const v of [seats.yes, seats.maybe, seats.noReply]) {
    if (!Number.isFinite(v) || v < 0) throw new RangeError("seat counts must be non-negative");
  }
  const low = Math.floor(apply(seats, LOW));
  const likely = Math.round(apply(seats, LIKELY));
  const high = Math.ceil(apply(seats, HIGH));
  const p80 = Math.round(likely + 0.6 * (high - likely));
  return { low, likely, high, p80 };
}
