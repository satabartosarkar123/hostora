export interface PlanLine {
  itemId: string;
  qty: number;
  unitPriceInr: number;
  mustHave: boolean;
  /** lower numbers are dropped first when nothing else fits the budget */
  priority: number;
  alternatives: { itemId: string; unitPriceInr: number }[];
}

export interface BudgetResult {
  lines: PlanLine[];
  totalInr: number;
  fits: boolean;
  changes: string[];
}

export function lineCost(line: PlanLine): number {
  return line.qty * line.unitPriceInr;
}

function sum(lines: readonly PlanLine[]): number {
  return lines.reduce((acc, l) => acc + lineCost(l), 0);
}

/** Greedy: take the biggest-saving swap until it fits, then drop optional lines by priority. */
export function fitBudget(input: readonly PlanLine[], budgetInr: number): BudgetResult {
  const lines: PlanLine[] = input.map((l) => ({ ...l, alternatives: [...l.alternatives] }));
  const changes: string[] = [];

  while (sum(lines) > budgetInr) {
    let bestIdx = -1;
    let bestAlt = -1;
    let bestSaving = 0;
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i] as PlanLine;
      for (let j = 0; j < l.alternatives.length; j++) {
        const a = l.alternatives[j] as { itemId: string; unitPriceInr: number };
        const saving = (l.unitPriceInr - a.unitPriceInr) * l.qty;
        if (saving > bestSaving) {
          bestSaving = saving;
          bestIdx = i;
          bestAlt = j;
        }
      }
    }
    if (bestIdx < 0) break;
    const l = lines[bestIdx] as PlanLine;
    const a = l.alternatives[bestAlt] as { itemId: string; unitPriceInr: number };
    changes.push(`swap ${l.itemId} -> ${a.itemId} (saves ₹${Math.round(bestSaving)})`);
    lines[bestIdx] = {
      ...l,
      itemId: a.itemId,
      unitPriceInr: a.unitPriceInr,
      alternatives: l.alternatives.filter((_, k) => k !== bestAlt),
    };
  }

  const dropped = new Set<number>();
  const optional = lines
    .map((l, i) => ({ l, i }))
    .filter(({ l }) => !l.mustHave)
    .sort((x, y) => x.l.priority - y.l.priority);
  for (const { l, i } of optional) {
    if (sum(lines.filter((_, k) => !dropped.has(k))) <= budgetInr) break;
    dropped.add(i);
    changes.push(`drop ${l.itemId}`);
  }

  const kept = lines.filter((_, k) => !dropped.has(k));
  const totalInr = sum(kept);
  return { lines: kept, totalInr, fits: totalInr <= budgetInr, changes };
}
