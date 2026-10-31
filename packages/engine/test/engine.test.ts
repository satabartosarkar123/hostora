import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  type CatalogItem,
  type RuleSet,
  applyRules,
  bufferFromPrediction,
  expandTags,
  fitBudget,
  generateKeyPairHex,
  makeLinkToken,
  normalizeCode,
  predictArrivals,
  rawQuantity,
  roundToPacks,
  signPass,
  splitHouseholdCode,
  verifyLinkToken,
  verifyPass,
} from "../src";

const RULES: Record<string, RuleSet> = {
  halal: { id: "halal", label: "Halal", forbid: ["pork", "alcohol", "meat_unverified_halal"] },
  jain: {
    id: "jain",
    label: "Jain",
    forbid: ["meat", "fish", "egg", "root_veg", "onion", "garlic"],
  },
  pure_veg: { id: "pure_veg", label: "Pure veg", forbid: ["meat", "fish", "egg"] },
  no_beef: { id: "no_beef", label: "No beef", forbid: ["beef"] },
  no_alcohol: { id: "no_alcohol", label: "No alcohol", forbid: ["alcohol"] },
};

const item = (id: string, tags: string[], category: CatalogItem["category"] = "food"): CatalogItem => ({
  id,
  name: id,
  category,
  course: "main",
  unit: "g",
  packSizes: [500, 1000],
  tags,
  estPricePerUnitInr: 0.5,
  searchQuery: id,
});

describe("codes", () => {
  it("normalises look-alikes and splits a typed code", () => {
    expect(normalizeCode("ab1o2-il9xz")).toBe("AB102119XZ");
    expect(splitHouseholdCode("7KQ2M-9TX4P")).toEqual({ eventKey: "7KQ2M", householdKey: "9TX4P" });
    expect(splitHouseholdCode("SHORT")).toBeNull();
    expect(normalizeCode("ABU12")).toBeNull();
  });

  it("verifies its own link tokens and rejects tampering", async () => {
    const token = await makeLinkToken("s3cret", "7KQ2M", "9TX4P");
    expect(token).toHaveLength(16);
    expect(await verifyLinkToken("s3cret", "7KQ2M", token)).toBe("9TX4P");
    expect(await verifyLinkToken("s3cret", "AAAAA", token)).toBeNull();
    expect(await verifyLinkToken("other", "7KQ2M", token)).toBeNull();
    const tampered = `${token.slice(0, 15)}${token.endsWith("0") ? "1" : "0"}`;
    expect(await verifyLinkToken("s3cret", "7KQ2M", tampered)).toBeNull();
  });
});

describe("constraints", () => {
  const items = [
    item("chicken_biryani", ["declared", "diet:nonveg", "chicken"]),
    item("pork_vindaloo", ["declared", "diet:nonveg", "pork"]),
    item("veg_biryani", ["declared", "diet:veg", "onion", "allergen:dairy"]),
    item("paneer_tikka", ["declared", "diet:veg", "allergen:dairy"]),
    item("mystery_curry", ["chicken"]),
    item("fairy_lights", [], "lighting"),
  ];

  it("halal removes pork and undeclared food, keeps decor", () => {
    const r = applyRules(items, ["halal"], RULES);
    const ids = r.allowed.map((i) => i.id);
    expect(ids).toContain("chicken_biryani");
    expect(ids).toContain("fairy_lights");
    expect(ids).not.toContain("pork_vindaloo");
    expect(ids).not.toContain("mystery_curry");
  });

  it("jain removes meat via implied tags and onion via root_veg", () => {
    const r = applyRules(items, ["jain"], RULES, ["dairy"]);
    expect(r.allowed.map((i) => i.id).sort()).toEqual(["fairy_lights", "paneer_tikka"]);
    expect(r.allergenWarnings).toEqual([{ itemId: "paneer_tikka", allergen: "dairy" }]);
  });

  it("throws on an unknown rule set", () => {
    expect(() => applyRules(items, ["nope"], RULES)).toThrow(/Unknown rule set/);
  });

  it("property: no forbidden tag ever survives", () => {
    const tagPool = [
      "chicken",
      "pork",
      "beef",
      "mutton",
      "prawn",
      "egg",
      "onion",
      "potato",
      "wine",
      "paneer",
    ];
    fc.assert(
      fc.property(
        fc.array(fc.subarray(tagPool), { minLength: 1, maxLength: 30 }),
        fc.subarray(Object.keys(RULES), { minLength: 1 }),
        (tagSets, ruleIds) => {
          const pool = tagSets.map((t, i) => item(`i${i}`, ["declared", "diet:x", ...t]));
          const forbidden = new Set(ruleIds.flatMap((id) => RULES[id]?.forbid ?? []));
          const { allowed } = applyRules(pool, ruleIds, RULES);
          return allowed.every((it) => [...expandTags(it.tags)].every((t) => !forbidden.has(t)));
        },
      ),
    );
  });
});

describe("quantity", () => {
  const row = {
    itemId: "chicken_biryani",
    meal: "dinner" as const,
    perAdult: 300,
    perChild: 150,
    source: "seed",
  };

  it("follows the formula", () => {
    expect(rawQuantity({ adults: 40, children: 4 }, row, 1, 0.65, 0)).toBe(12600);
    const two = rawQuantity({ adults: 40, children: 4 }, row, 2, 0.65, 0);
    expect(two).toBeCloseTo(12600 * 2 ** -0.65, 6);
  });

  it("caps the buffer at 10%", () => {
    expect(bufferFromPrediction(100, 130)).toBe(0.1);
    expect(bufferFromPrediction(100, 105)).toBeCloseTo(0.05);
    expect(rawQuantity({ adults: 10, children: 0 }, row, 1, 0.65, 0.5)).toBeCloseTo(3300);
  });

  it("property: more guests never means less food", () => {
    fc.assert(
      fc.property(fc.nat(500), fc.nat(500), fc.nat(50), fc.integer({ min: 1, max: 8 }), (a, extra, c, k) => {
        return (
          rawQuantity({ adults: a + extra, children: c }, row, k) >=
          rawQuantity({ adults: a, children: c }, row, k)
        );
      }),
    );
  });

  it("rounds to the smallest buildable pack total", () => {
    expect(roundToPacks(12600, [500, 1000])).toEqual({ packs: [{ size: 1000, count: 13 }], total: 13000 });
    expect(roundToPacks(1100, [500, 750])).toEqual({
      packs: [
        { size: 750, count: 1 },
        { size: 500, count: 1 },
      ],
      total: 1250,
    });
    expect(roundToPacks(0, [500])).toEqual({ packs: [], total: 0 });
  });

  it("property: pack total covers the need with less than one smallest pack of waste", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 50_000 }),
        fc.uniqueArray(fc.constantFrom(100, 200, 250, 500, 750, 1000, 2000), { minLength: 1 }),
        (qty, sizes) => {
          const plan = roundToPacks(qty, sizes);
          const sumPacks = plan.packs.reduce((s, p) => s + p.size * p.count, 0);
          return (
            plan.total >= qty &&
            sumPacks === plan.total &&
            plan.total - qty < Math.min(...sizes) + 1e-9 + Math.max(...sizes)
          );
        },
      ),
    );
  });
});

describe("prediction", () => {
  it("orders low <= likely <= p80 <= high", () => {
    const p = predictArrivals({ yes: 410, maybe: 60, noReply: 60 });
    expect(p.likely).toBe(Math.round(410 * 0.96 + 60 * 0.7 + 60 * 0.3));
    expect(p.low).toBeLessThanOrEqual(p.likely);
    expect(p.likely).toBeLessThanOrEqual(p.p80);
    expect(p.p80).toBeLessThanOrEqual(p.high);
  });

  it("rejects negative seats", () => {
    expect(() => predictArrivals({ yes: -1, maybe: 0, noReply: 0 })).toThrow(RangeError);
  });
});

describe("budget", () => {
  it("swaps the biggest saving first, then drops low-priority optional lines", () => {
    const r = fitBudget(
      [
        {
          itemId: "biryani_premium",
          qty: 13,
          unitPriceInr: 600,
          mustHave: true,
          priority: 9,
          alternatives: [{ itemId: "biryani_std", unitPriceInr: 400 }],
        },
        { itemId: "mocktails", qty: 40, unitPriceInr: 60, mustHave: false, priority: 5, alternatives: [] },
        { itemId: "photo_booth", qty: 1, unitPriceInr: 3000, mustHave: false, priority: 1, alternatives: [] },
      ],
      8000,
    );
    expect(r.changes[0]).toMatch(/^swap biryani_premium -> biryani_std/);
    expect(r.lines.map((l) => l.itemId)).toEqual(["biryani_std", "mocktails"]);
    expect(r.totalInr).toBe(13 * 400 + 40 * 60);
    expect(r.fits).toBe(true);
  });
});

describe("passes", () => {
  it("signs, verifies offline, and rejects forgery and expiry", async () => {
    const { privateKeyHex, publicKeyHex } = await generateKeyPairHex();
    const claims = { eventKey: "7KQ2M", passId: "01JC9ZPASS", seats: 4, exp: 2_000_000_000 };
    const token = await signPass(claims, privateKeyHex);
    expect(await verifyPass(token, publicKeyHex, 1_900_000_000)).toEqual(claims);
    expect(await verifyPass(token.replace(".4.", ".9."), publicKeyHex, 1_900_000_000)).toBeNull();
    expect(await verifyPass(token, publicKeyHex, 2_100_000_000)).toBeNull();
    const other = await generateKeyPairHex();
    expect(await verifyPass(token, other.publicKeyHex, 1_900_000_000)).toBeNull();
  });
});
