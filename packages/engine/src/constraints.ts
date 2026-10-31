export type Unit = "g" | "ml" | "piece";
export type Category = "food" | "beverage" | "decor" | "lighting" | "tableware" | "supply";
export type Course =
  | "starter"
  | "main"
  | "rice"
  | "bread"
  | "side"
  | "dessert"
  | "drink"
  | "supply"
  | "decor";

export interface CatalogItem {
  id: string;
  name: string;
  category: Category;
  course: Course;
  unit: Unit;
  packSizes: number[];
  /** e.g. ["declared", "diet:nonveg", "chicken", "allergen:dairy"] */
  tags: string[];
  estPricePerUnitInr: number;
  searchQuery: string;
}

export interface RuleSet {
  id: string;
  label: string;
  forbid: string[];
}

/** Tags implied by other tags. Applied before filtering so a data-entry slip cannot leak. */
export const IMPLIED_TAGS: Readonly<Record<string, readonly string[]>> = {
  beef: ["meat"],
  pork: ["meat"],
  mutton: ["meat"],
  chicken: ["meat"],
  prawn: ["fish"],
  onion: ["root_veg"],
  garlic: ["root_veg"],
  potato: ["root_veg"],
  carrot: ["root_veg"],
  beer: ["alcohol"],
  wine: ["alcohol"],
};

export interface FilterOutcome {
  allowed: CatalogItem[];
  removed: { itemId: string; reason: string }[];
  allergenWarnings: { itemId: string; allergen: string }[];
}

export function expandTags(tags: readonly string[]): Set<string> {
  const out = new Set(tags);
  const queue = [...tags];
  while (queue.length > 0) {
    const tag = queue.pop() as string;
    for (const implied of IMPLIED_TAGS[tag] ?? []) {
      if (!out.has(implied)) {
        out.add(implied);
        queue.push(implied);
      }
    }
  }
  return out;
}

/** Closed world: food and drink must carry "declared" and one "diet:*" tag, or they are dropped. */
export function isDeclared(tags: ReadonlySet<string>): boolean {
  if (!tags.has("declared")) return false;
  for (const t of tags) if (t.startsWith("diet:")) return true;
  return false;
}

export function applyRules(
  items: readonly CatalogItem[],
  ruleIds: readonly string[],
  rules: Readonly<Record<string, RuleSet>>,
  allergies: readonly string[] = [],
): FilterOutcome {
  const forbidden = new Set<string>();
  for (const id of ruleIds) {
    const rule = rules[id];
    if (!rule) throw new Error(`Unknown rule set: ${id}`);
    for (const t of rule.forbid) forbidden.add(t);
  }

  const result: FilterOutcome = { allowed: [], removed: [], allergenWarnings: [] };
  for (const item of items) {
    const tags = expandTags(item.tags);
    const isConsumable = item.category === "food" || item.category === "beverage";
    if (isConsumable && !isDeclared(tags)) {
      result.removed.push({ itemId: item.id, reason: "undeclared" });
      continue;
    }
    let hit: string | undefined;
    for (const t of tags) {
      if (forbidden.has(t)) {
        hit = t;
        break;
      }
    }
    if (hit !== undefined) {
      result.removed.push({ itemId: item.id, reason: `forbidden:${hit}` });
      continue;
    }
    for (const a of allergies) {
      if (tags.has(`allergen:${a}`)) result.allergenWarnings.push({ itemId: item.id, allergen: a });
    }
    result.allowed.push(item);
  }
  return result;
}
