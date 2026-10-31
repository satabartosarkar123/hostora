export type Meal = "lunch" | "dinner" | "snacks";

export interface ConsumptionRow {
  itemId: string;
  meal: Meal;
  /** grams, millilitres or pieces per adult, as the item's unit */
  perAdult: number;
  perChild: number;
  source: string;
}

export interface Headcount {
  adults: number;
  children: number;
}

/** Dish-share exponent; 1 would split a course evenly. Fitted from caterer data later. */
export const DEFAULT_ALPHA = 0.65;
export const MAX_BUFFER = 0.1;

export function bufferFromPrediction(p50: number, p80: number): number {
  if (p50 <= 0) return 0;
  return Math.min(Math.max((p80 - p50) / p50, 0), MAX_BUFFER);
}

/** Q = (adults * perAdult + children * perChild) * k^-alpha * (1 + buffer) */
export function rawQuantity(
  headcount: Headcount,
  row: ConsumptionRow,
  dishesInCourse: number,
  alpha = DEFAULT_ALPHA,
  buffer = 0,
): number {
  if (headcount.adults < 0 || headcount.children < 0) {
    throw new RangeError("headcount must be non-negative");
  }
  const base = headcount.adults * row.perAdult + headcount.children * row.perChild;
  const share = Math.max(1, dishesInCourse) ** -alpha;
  const b = Math.min(Math.max(buffer, 0), MAX_BUFFER);
  return base * share * (1 + b);
}

export interface PackPlan {
  packs: { size: number; count: number }[];
  total: number;
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

/**
 * Smallest total >= qty buildable from the pack sizes; ties broken by fewer packs.
 * Unbounded knapsack over units of gcd(packSizes).
 */
export function roundToPacks(qty: number, packSizes: readonly number[]): PackPlan {
  const sizes = [...new Set(packSizes.map((s) => Math.round(s)))].filter((s) => s > 0).sort((a, b) => a - b);
  if (sizes.length === 0) throw new Error("packSizes must contain a positive size");
  if (qty <= 0) return { packs: [], total: 0 };

  const g = sizes.reduce((acc, s) => gcd(acc, s));
  const units = sizes.map((s) => s / g);
  const target = Math.ceil(qty / g);
  const limit = target + Math.max(...units);

  if (limit > 200_000) {
    const big = sizes[sizes.length - 1] as number;
    const count = Math.ceil(qty / big);
    return { packs: [{ size: big, count }], total: count * big };
  }

  const fewest = new Array<number>(limit + 1).fill(Number.POSITIVE_INFINITY);
  const last = new Array<number>(limit + 1).fill(-1);
  fewest[0] = 0;
  for (let v = 1; v <= limit; v++) {
    for (let i = 0; i < units.length; i++) {
      const u = units[i] as number;
      const prev = fewest[v - u];
      if (u <= v && prev !== undefined && prev + 1 < (fewest[v] as number)) {
        fewest[v] = prev + 1;
        last[v] = i;
      }
    }
  }

  let best = -1;
  for (let v = target; v <= limit; v++) {
    if ((fewest[v] as number) < Number.POSITIVE_INFINITY) {
      best = v;
      break;
    }
  }

  const counts = new Map<number, number>();
  for (let v = best; v > 0; ) {
    const i = last[v] as number;
    const size = sizes[i] as number;
    counts.set(size, (counts.get(size) ?? 0) + 1);
    v -= units[i] as number;
  }
  const packs = [...counts.entries()].sort((a, b) => b[0] - a[0]).map(([size, count]) => ({ size, count }));
  return { packs, total: best * g };
}
