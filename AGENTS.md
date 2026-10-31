# Hostora — Build Specification for AI Coding Agents

Version 1.0 · 31 Oct 2026 · Owner: Satabarto
Companion documents: Business Requirements Document (BRD) and Technical Design Document (TDD), both in the project's docs.

This file is the single source of truth for building Hostora. It is written for an AI coding agent (Gemini CLI, Claude Code, Cursor or similar) working inside this repository. Humans should read section 0 first.

---

## 0. How to use this file (for the human)

1. Unzip the starter repository. `packages/engine` (codes, safety rules, quantities, prediction, budget, gate passes) is already implemented and tested: 15 tests pass.
2. Open the repository in your coding agent and give it exactly this prompt, one milestone per session:

   > Read AGENTS.md completely. Implement milestone **M1** only. Follow section 1 rules. Stop when every acceptance check for M1 passes, then report what you built, the commands you ran and their output.

3. Review the diff, run the acceptance checks yourself, commit, then move to the next milestone.
4. For a hackathon, build the **Hackathon cut** (section 17): M0 to M6 plus the demo seed. That is a complete, deployable product on Cloudflare's free plan with no credit card.

---

## 1. Rules for the agent (must follow)

1. Read this whole file before writing code. When this file and your own preference differ, this file wins.
2. Work on one milestone at a time, in order. Do not start the next milestone until every acceptance check of the current one passes.
3. Use the exact paths, names, routes, bindings and environment variable names given here. Do not rename.
4. Do not change the behaviour of `packages/engine`. If you need new behaviour, add a function and a test; never weaken an existing test.
5. Never invent an external API. Where this file says "verify at build time", check the provider's current docs; if you cannot, keep the value in a data/config file so a human can fix it without code changes.
6. Every external call goes through an adapter in `services/edge/src/adapters/`. Every adapter has a mock used by tests and by demo mode.
7. No secrets in code or git. Secrets are Wrangler secrets or GitHub encrypted secrets; `.env.example` lists names only.
8. LLMs propose items and words only. Quantities, prices and safety-rule checks always come from `packages/engine`. A plan item that fails `applyRules` must never reach a response, whatever the LLM returns.
9. Guest-facing pages must stay under the size budget (section 11.4) and work on a slow Android phone.
10. TypeScript strict mode everywhere; no `any` without a comment explaining why.
11. Run `pnpm check` before declaring a milestone done. It must be green.
12. If something in this file is impossible or contradictory, stop and report the exact conflict instead of guessing.

---

## 2. What we are building

**Problem in one line:** Indian hosts buy food and supplies for a guess instead of a count, so they overspend and waste.

**Product:** a web app (installable PWA) that

- sends invitations with a unique code per household; guests reply with no login;
- shows the host a live count and a predicted turnout range per function (for example a reception, a nikah, a birthday dinner);
- turns the predicted headcount into a priced shopping and food plan, filtered by the host's dietary and faith rules (halal, Jain, pure veg, no beef, no pork, no alcohol, eggless, vegan);
- links each item to where it can be bought, chosen by how many days are left;
- runs an optional QR gate at the venue that works offline.

Any faith, any community, civil and interfaith weddings, and home parties are equally first-class. Tradition templates only name functions; they never set dietary rules. The host chooses rules.

Phase A is non-commercial: no payments, no affiliate tags, no ads (TDD section 17).

---

## 3. Architecture decisions for this build

These refine the TDD for reliability on free tiers and for agent-friendly implementation. Record each as an ADR file in `docs/adr/` during M0.

| ADR | Decision | Why |
| --- | --- | --- |
| 001 | The planner runs inside the edge Worker in TypeScript, using `packages/engine`. Python is used only in `ml/` for training and evaluation, run from GitHub Actions. | No Cloud Run on the request path: Cloud Run needs a billing account with a card. One runtime for request handling. |
| 002 | The host app is Vite + React + TypeScript as an installable PWA; an Android build via Capacitor comes later (M9). | Web-first judging, simpler build, one codebase the agent can test with Playwright. |
| 003 | One Durable Object per event (`EventActor`) and one per host (`HostDirectory`), both SQLite-backed. Postgres (Neon) is optional until M7. | The whole hackathon cut runs on Cloudflare's free plan with no other service. |
| 004 | Scheduled jobs (backups, price snapshots, model training) run as GitHub Actions cron workflows. | Free, no extra cloud account. |
| 005 | Neon is reached with the `@neondatabase/serverless` HTTP driver from the queue consumer. | No Hyperdrive setup needed; simple. |
| 006 | One Worker (`services/edge`) contains the API, both Durable Object classes, the queue consumer and the cron handler. Each web app is a separate static-assets Worker. | Fewer deployables; Durable Objects and API share types. |

---

## 4. Stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Package manager | pnpm workspaces | Node 22 LTS |
| Language | TypeScript 5.x strict | Python 3.12 only in `ml/` |
| Lint and format | Biome | One tool for both |
| Unit tests | Vitest; `@cloudflare/vitest-pool-workers` for the Worker | fast-check for property tests |
| E2E tests | Playwright | Chromium, mobile emulation |
| Edge runtime | Cloudflare Workers, Durable Objects (SQLite), Queues, KV, R2, Workers AI | Wrangler 3.x or 4.x |
| HTTP framework | Hono | |
| Validation | Zod 3.x (pin `^3.23`) | Shared in `packages/schema` |
| Guest web | Vite + Preact | Under 150 KB gzipped |
| Host web | Vite + React 18 + React Router + TanStack Query + vite-plugin-pwa | |
| Auth | Firebase Authentication (Google sign-in) on the client; `jose` to verify ID tokens in the Worker | Free up to 50,000 MAU |
| LLMs | Groq (OpenAI-compatible API) primary, Workers AI fallback, deterministic template last | Model names are config |
| QR | `uqr` (render), `@zxing/browser` (scan) | |
| Crypto | WebCrypto (HMAC, AES-GCM); `@noble/ed25519` v2 for passes | |
| Database (M7) | Neon Postgres via `@neondatabase/serverless` | |
| CI/CD | GitHub Actions, `cloudflare/wrangler-action` | |

---

## 5. Repository file tree (exact)

Files marked ✅ exist in the starter. Everything else is created by the milestone named in brackets.

```
hostora/
├── AGENTS.md                              ✅ this file
├── README.md                              [M0]
├── package.json                           ✅
├── pnpm-workspace.yaml                    ✅
├── tsconfig.base.json                     ✅
├── biome.json                             ✅
├── .gitignore                             ✅
├── .nvmrc                                 ✅
├── .env.example                           ✅
├── docs/
│   └── adr/0001..0006-*.md                [M0]
├── .github/workflows/
│   ├── ci.yml                             [M0]
│   ├── deploy.yml                         [M3]
│   └── nightly.yml                        [M7]
├── data/                                  ✅ seed data (section 9)
│   ├── catalog/catalog.csv                ✅
│   ├── consumption/consumption.csv        ✅
│   ├── rules/rules.yaml                   ✅
│   ├── templates/traditions.yaml          ✅
│   ├── commerce/channels.yaml             ✅
│   └── evals/
│       ├── briefs.jsonl                   [M8]
│       └── adversarial.jsonl              [M8]
├── scripts/
│   ├── build-data.mjs                     [M1]
│   ├── gen-keys.mjs                       [M2]
│   └── check-budget.mjs                   [M3]
├── packages/
│   ├── engine/                            ✅ implemented and tested
│   │   ├── package.json, tsconfig.json
│   │   ├── src/{codes,constraints,quantity,prediction,budget,passes,index}.ts
│   │   └── test/engine.test.ts
│   └── schema/                            [M1]
│       ├── package.json, tsconfig.json
│       └── src/index.ts
├── services/
│   └── edge/                              [M2, M5, M6, M7]
│       ├── package.json, tsconfig.json, wrangler.jsonc, vitest.config.ts
│       ├── src/
│       │   ├── index.ts                   Worker entry: fetch, queue, scheduled
│       │   ├── env.ts                     Env type
│       │   ├── app.ts                     Hono app and routes
│       │   ├── errors.ts                  HttpError, problem+json
│       │   ├── auth/firebase.ts           ID token verification (+ demo auth)
│       │   ├── auth/tokens.ts             link tokens, gate tokens
│       │   ├── crypto/phone.ts            AES-GCM encrypt/decrypt, HMAC hash
│       │   ├── actors/EventActor.ts
│       │   ├── actors/HostDirectory.ts
│       │   ├── actors/migrations.ts
│       │   ├── planner/pipeline.ts
│       │   ├── planner/template.ts        deterministic fallback plan
│       │   ├── planner/prompts.ts
│       │   ├── adapters/llm.ts            router: groq -> workers-ai -> template
│       │   ├── adapters/groq.ts
│       │   ├── adapters/workersAi.ts
│       │   ├── adapters/mockLlm.ts
│       │   ├── adapters/commerce.ts       channel links from channels.yaml
│       │   ├── adapters/whatsapp.ts       wa.me link builder
│       │   ├── queue/consumer.ts          [M7]
│       │   ├── db/neon.ts                 [M7]
│       │   ├── demo/seed.ts
│       │   └── generated/data.json        written by scripts/build-data.mjs
│       └── test/*.test.ts
├── apps/
│   ├── guest-web/                         [M3]
│   │   ├── package.json, tsconfig.json, vite.config.ts, wrangler.jsonc, index.html
│   │   └── src/{main.tsx,App.tsx,api.ts,i18n.ts}
│   │       ├── pages/{Invite,EnterCode,Landing}.tsx
│   │       ├── components/{RsvpForm,MealStepper,PassCard,CalendarButton}.tsx
│   │       └── i18n/{en,hi}.json
│   └── host/                              [M4, M5, M6]
│       ├── package.json, tsconfig.json, vite.config.ts, wrangler.jsonc, index.html
│       └── src/
│           ├── main.tsx, App.tsx, routes.tsx, theme.css
│           ├── lib/{api.ts,auth.ts,live.ts,csv.ts}
│           ├── gate/{db.ts,verify.ts,sync.ts,Scanner.tsx}
│           ├── pages/{Login,Events,EventNew,Dashboard,Guests,Invite,Planner,Budget,Gate,Settings}.tsx
│           └── components/{CountCard,PredictionBand,PlanCategory,OfferCard,Stepper}.tsx
├── db/                                    [M7]
│   └── migrations/0001_init.sql
├── ml/                                    [M9]
│   ├── pyproject.toml
│   └── hostora_ml/{turnout,evals}/...
└── e2e/                                   [M3]
    ├── playwright.config.ts
    └── tests/{rsvp,host,planner,gate}.spec.ts
```

---

## 6. Root files

These exist in the starter. Keep them; extend scripts when a milestone says so.

### 6.1 `package.json`

```json
{
  "name": "hostora",
  "private": true,
  "packageManager": "pnpm@9.12.0",
  "engines": { "node": ">=22" },
  "scripts": {
    "data:build": "node scripts/build-data.mjs",
    "lint": "biome check .",
    "format": "biome format --write .",
    "typecheck": "pnpm -r --if-present typecheck",
    "test": "pnpm -r --if-present test",
    "check": "pnpm lint && pnpm typecheck && pnpm test",
    "dev": "pnpm -r --parallel --if-present dev",
    "e2e": "playwright test -c e2e/playwright.config.ts"
  },
  "devDependencies": {
    "@biomejs/biome": "^1.9.0",
    "@playwright/test": "^1.48.0",
    "typescript": "^5.6.0",
    "yaml": "^2.6.0"
  }
}
```

### 6.2 `pnpm-workspace.yaml`

```yaml
packages:
  - "packages/*"
  - "services/*"
  - "apps/*"
```

### 6.3 `tsconfig.base.json`

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "verbatimModuleSyntax": true,
    "resolveJsonModule": true,
    "noEmit": true
  }
}
```

### 6.4 `.env.example`

```
# Names only. Real values go in `wrangler secret put` or GitHub secrets.
TOKEN_HMAC_SECRET=
PHONE_ENC_KEY=            # base64, 32 bytes
PASS_SIGNING_KEY=         # ed25519 private key, hex
PASS_PUBLIC_KEY=          # ed25519 public key, hex (not secret; also a var)
GROQ_API_KEY=
NEON_DATABASE_URL=        # M7 only
CLOUDFLARE_API_TOKEN=     # GitHub Actions only
CLOUDFLARE_ACCOUNT_ID=    # GitHub Actions only
R2_ACCESS_KEY_ID=         # nightly backups, M7
R2_SECRET_ACCESS_KEY=     # nightly backups, M7
VITE_API_ORIGIN=
VITE_FIREBASE_API_KEY=
VITE_FIREBASE_AUTH_DOMAIN=
VITE_FIREBASE_PROJECT_ID=
VITE_FIREBASE_APP_ID=
```

---

## 7. `packages/engine` (done; do not change behaviour)

Exports, all pure and runnable in Workers, browsers and Node 22:

| Module | Key exports | Contract |
| --- | --- | --- |
| `codes.ts` | `ALPHABET`, `randomKey`, `normalizeCode`, `splitHouseholdCode`, `formatHouseholdCode`, `makeLinkToken`, `verifyLinkToken`, `timingSafeEqual` | Crockford base32. Typed code `XXXXX-XXXXX` = event key + household key. Link token = 16 chars, HMAC-SHA-256. |
| `constraints.ts` | `CatalogItem`, `RuleSet`, `IMPLIED_TAGS`, `expandTags`, `isDeclared`, `applyRules` | Closed world: food and drink without `declared` and a `diet:*` tag are removed. Implied tags (`chicken` → `meat`, `onion` → `root_veg`, `wine` → `alcohol`, …) applied before filtering. Allergies produce warnings, not removals. |
| `quantity.ts` | `rawQuantity`, `bufferFromPrediction`, `roundToPacks`, `DEFAULT_ALPHA` (0.65), `MAX_BUFFER` (0.1) | Q = (adults × perAdult + children × perChild) × k^−α × (1 + buffer). `roundToPacks` returns the smallest buildable total ≥ need. |
| `prediction.ts` | `predictArrivals`, `LIKELY`, `LOW`, `HIGH` | Phase-1 turnout rules; returns `{low, likely, high, p80}` with low ≤ likely ≤ p80 ≤ high. Recommended plates = `p80`. |
| `budget.ts` | `fitBudget`, `PlanLine` | Biggest-saving swap first, then drop optional lines by ascending `priority`. |
| `passes.ts` | `signPass`, `verifyPass`, `generateKeyPairHex`, `passPayload` | Pass = `v1.{eventKey}.{passId}.{seats}.{exp}.{sig}`; Ed25519; verifiable offline. |

---

## 8. `packages/schema` [M1]

`packages/schema/package.json`: name `@hostora/schema`, `"type": "module"`, `"main": "src/index.ts"`, dependency `zod@^3.23.8`, scripts `typecheck` and `test`.

`packages/schema/src/index.ts` (create exactly; you may add exports, not change these):

```ts
import { z } from "zod";

export const RuleId = z.enum([
  "halal", "jain", "pure_veg", "no_beef", "no_pork", "no_alcohol", "eggless", "vegan",
]);
export type RuleId = z.infer<typeof RuleId>;

export const EventType = z.enum([
  "wedding", "birthday", "anniversary", "housewarming", "festival",
  "religious", "office", "community", "other",
]);
export const Meal = z.enum(["lunch", "dinner", "snacks"]);
export const GateMode = z.enum(["off", "soft", "strict"]);
export const Language = z.enum(["en", "hi"]);

export const FunctionInput = z.object({
  name: z.string().min(1).max(60),
  startsAt: z.string().datetime({ offset: true }),
  meal: Meal,
  rules: z.array(RuleId).default([]),
  gateMode: GateMode.default("off"),
});

export const CreateEventInput = z.object({
  title: z.string().min(1).max(100),
  type: EventType,
  tradition: z.string().max(40).default("none"),
  venue: z.object({
    label: z.string().min(1).max(120),
    lat: z.number().min(-90).max(90),
    lng: z.number().min(-180).max(180),
    pincode: z.string().regex(/^\d{6}$/).optional(),
  }),
  budgetInr: z.number().int().nonnegative(),
  functions: z.array(FunctionInput).min(1).max(8),
});
export type CreateEventInput = z.infer<typeof CreateEventInput>;

export const HouseholdInput = z.object({
  displayName: z.string().min(1).max(80),
  phone: z.string().regex(/^\+?\d{10,15}$/).optional(),
  seatCap: z.number().int().min(1).max(20),
  functionIds: z.array(z.string().min(1)).min(1),
  language: Language.default("en"),
});
export const HouseholdBatch = z.object({ households: z.array(HouseholdInput).min(1).max(500) });
export type HouseholdBatch = z.infer<typeof HouseholdBatch>;

export const Meals = z.object({
  veg: z.number().int().min(0),
  jain: z.number().int().min(0),
  nonVeg: z.number().int().min(0),
});

export const RsvpFunction = z.object({
  functionId: z.string().min(1),
  attending: z.enum(["yes", "no", "maybe"]),
  adults: z.number().int().min(0).max(20),
  children: z.number().int().min(0).max(20),
  meals: Meals,
  dietaryRules: z.array(RuleId).default([]),
  allergies: z.array(z.string().min(1).max(30)).max(10).default([]),
  arrivalWindow: z.string().max(20).optional(),
});
export const RsvpInput = z.object({
  functions: z.array(RsvpFunction).min(1).max(8),
  language: Language.default("en"),
});
export type RsvpInput = z.infer<typeof RsvpInput>;

export const PlanRequest = z.object({
  functionId: z.string().min(1),
  brief: z.string().max(1000).default(""),
  daysLeft: z.number().int().min(0).max(365).optional(),
});
export type PlanRequest = z.infer<typeof PlanRequest>;

export const GateScan = z.object({
  deviceId: z.string().min(4).max(64),
  seq: z.number().int().nonnegative(),
  passId: z.string().min(1).max(32),
  seats: z.number().int().min(1).max(20),
  at: z.number().int().positive(),
});
export const GateScanBatch = z.object({ scans: z.array(GateScan).min(1).max(500) });

export interface FunctionSummary {
  functionId: string;
  name: string;
  invitedHouseholds: number;
  repliedHouseholds: number;
  seats: { yes: number; maybe: number; no: number; noReply: number };
  meals: { veg: number; jain: number; nonVeg: number };
  children: number;
  prediction: { low: number; likely: number; high: number; p80: number };
  recommendedPlates: number;
  arrivals: number;
  possibleDuplicates: number;
}
export interface EventSummary {
  eventKey: string;
  title: string;
  functions: FunctionSummary[];
  updatedAt: number;
}

export interface PlanItem {
  itemId: string;
  name: string;
  category: string;
  course: string;
  unit: "g" | "ml" | "piece";
  needed: number;
  packs: { size: number; count: number }[];
  buyTotal: number;
  estCostInr: number;
  perHeadBasis: string;
  allergenWarnings: string[];
  offers: Offer[];
}
export interface Offer {
  channel: string;
  label: string;
  kind: "quick" | "food" | "ecommerce";
  url: string;
}
export interface Plan {
  planId: string;
  functionId: string;
  headcount: { adults: number; children: number; vegSeats: number; jainSeats: number; nonVegSeats: number };
  rules: string[];
  items: PlanItem[];
  removed: { itemId: string; reason: string }[];
  budget: { limitInr: number; totalInr: number; fits: boolean; changes: string[] };
  route: "groq" | "workers-ai" | "template";
  explanation: Record<string, string>;
  createdAt: number;
}
```

---

## 9. Data (starter files) and `scripts/build-data.mjs` [M1]

All values marked `seed-unvalidated` are illustrative development numbers. Replace them with caterer-validated values before any real event (TDD 10.2).

### 9.1 `data/catalog/catalog.csv`

Columns: `id,name,category,course,unit,pack_sizes,tags,est_price_inr_per_unit,search_query,popularity`. `pack_sizes` is pipe-separated; `tags` is space-separated. See the starter file for the 24 seed rows.

### 9.2 `data/consumption/consumption.csv`

Columns: `item_id,meal,per_adult,per_child,source`. Units follow the catalog item (g, ml or pieces). Decor and lighting have no rows; the planner gives them `ceil(guests / 50)` units, minimum 1.

### 9.3 `data/rules/rules.yaml`

Rule sets with `id`, `label`, `forbid`. Must match `RuleId` in `packages/schema`.

### 9.4 `data/templates/traditions.yaml`

Function-name templates only (no rules). The host can rename, add or remove functions.

### 9.5 `data/commerce/channels.yaml`

Search-URL patterns per channel with a `{q}` placeholder. **Verify each pattern in M5** by opening it in a browser; fix the YAML, not the code.

### 9.6 `scripts/build-data.mjs` (create in M1)

Behaviour:

1. Read the five data files (use the `yaml` package and a small CSV parser that handles quoted fields).
2. Validate, failing with a non-zero exit and a clear message when:
   - a `food` or `beverage` item lacks `declared` or a `diet:*` tag;
   - a consumption row references an unknown item, or a food item has no dinner row;
   - a pack size is not a positive integer;
   - a rule id in `rules.yaml` is not in `RuleId`;
   - a channel URL lacks `{q}`.
3. Write `services/edge/src/generated/data.json` as `{ version, catalog: CatalogItem[], consumption: ConsumptionRow[], rules: Record<string, RuleSet>, traditions, channels }`, with `version` = SHA-256 of the inputs.
4. Print counts. The file is committed so the Worker builds without the script, and CI fails if it is stale (`git diff --exit-code` after running the script).

---

## 10. `services/edge` [M2, M5, M6, M7]

### 10.1 `wrangler.jsonc`

```jsonc
{
  "$schema": "node_modules/wrangler/config-schema.json",
  "name": "hostora-edge",
  "main": "src/index.ts",
  "compatibility_date": "2026-10-01",
  "compatibility_flags": ["nodejs_compat"],
  "durable_objects": {
    "bindings": [
      { "name": "EVENT", "class_name": "EventActor" },
      { "name": "HOST", "class_name": "HostDirectory" }
    ]
  },
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["EventActor", "HostDirectory"] }],
  "kv_namespaces": [{ "binding": "CONFIG", "id": "REPLACE_WITH_KV_ID" }],
  "r2_buckets": [{ "binding": "FILES", "bucket_name": "hostora-files" }],
  "queues": {
    "producers": [{ "binding": "OUTBOX", "queue": "hostora-outbox" }],
    "consumers": [{
      "queue": "hostora-outbox",
      "max_batch_size": 10,
      "max_retries": 5,
      "dead_letter_queue": "hostora-outbox-dlq"
    }]
  },
  "ai": { "binding": "AI" },
  "triggers": { "crons": ["*/10 * * * *"] },
  "vars": {
    "DEMO_MODE": "false",
    "PERSISTENCE": "actor-only",
    "FIREBASE_PROJECT_ID": "REPLACE",
    "APP_ORIGIN": "https://app.REPLACE_DOMAIN",
    "GUEST_ORIGIN": "https://REPLACE_DOMAIN",
    "PASS_PUBLIC_KEY": "REPLACE_HEX",
    "GROQ_MODEL_LARGE": "openai/gpt-oss-120b",
    "GROQ_MODEL_SMALL": "llama-3.1-8b-instant",
    "WORKERS_AI_MODEL": "@cf/meta/llama-3.1-8b-instruct"
  }
}
```

Model names change over time: verify current Groq and Workers AI model ids at build time and update the vars, never code.

### 10.2 `src/env.ts`

```ts
import type { EventActor } from "./actors/EventActor";
import type { HostDirectory } from "./actors/HostDirectory";

export interface Env {
  EVENT: DurableObjectNamespace<EventActor>;
  HOST: DurableObjectNamespace<HostDirectory>;
  CONFIG: KVNamespace;
  FILES: R2Bucket;
  OUTBOX: Queue<OutboxBatch>;
  AI: Ai;
  DEMO_MODE: string;
  PERSISTENCE: "actor-only" | "neon";
  FIREBASE_PROJECT_ID: string;
  APP_ORIGIN: string;
  GUEST_ORIGIN: string;
  PASS_PUBLIC_KEY: string;
  GROQ_MODEL_LARGE: string;
  GROQ_MODEL_SMALL: string;
  WORKERS_AI_MODEL: string;
  // secrets
  TOKEN_HMAC_SECRET: string;
  PHONE_ENC_KEY: string;
  PASS_SIGNING_KEY: string;
  GROQ_API_KEY?: string;
  NEON_DATABASE_URL?: string;
}

export interface OutboxBatch {
  eventKey: string;
  rows: { seq: number; kind: string; payload: string; createdAt: number }[];
}
```

### 10.3 Routes (`src/app.ts`)

All responses JSON unless noted. Errors use `application/problem+json` with `{type, title, status, detail, field?}`. CORS allows `APP_ORIGIN` and `GUEST_ORIGIN` only.

| Method and path | Auth | Handler behaviour |
| --- | --- | --- |
| GET /v1/health | none | `{ok: true, dataVersion}` |
| GET /v1/config/public | none | Traditions, rule labels, event types (from data.json) |
| POST /v1/events | host | Validate `CreateEventInput`; generate a 5-char event key (retry until the actor reports uninitialised); `EventActor.init`; `HostDirectory.addEvent`; 201 with `{eventKey, functions:[{id,name}]}` |
| GET /v1/events | host | `HostDirectory.listEvents(uid)` |
| GET /v1/events/:eventKey | host (owner or co-host) | Event meta + summary |
| POST /v1/events/:eventKey/households:batch | host | Validate `HouseholdBatch`; actor creates households with unique 5-char keys; phones encrypted |
| GET /v1/events/:eventKey/households | host | List with reply status (no phones in plain text unless `?includePhone=1` by owner) |
| POST /v1/events/:eventKey/invites:prepare | host | Per household: link `${GUEST_ORIGIN}/i/{eventKey}/{token}`, typed code `XXXXX-XXXXX`, and a WhatsApp URL from `adapters/whatsapp.ts` |
| GET /v1/invites/:eventKey/:token | guest | Verify token; return invitation (household name, seat cap, invited functions with name, time, meal, venue label and map link, earlier reply, pass if any) |
| POST /v1/invites:resolve | guest | Body `{code}`; `splitHouseholdCode`; rate-limited; returns `{eventKey, token}` |
| PUT /v1/invites/:eventKey/:token/rsvp | guest | Header `Idempotency-Key` required; validate `RsvpInput`; `EventActor.submitRsvp`; returns reply + passes |
| GET /v1/events/:eventKey/summary | host | `EventSummary` |
| GET /v1/events/:eventKey/live | host (token as `?auth=` query, since browsers cannot set WebSocket headers) | Upgrade, forward to actor |
| POST /v1/events/:eventKey/plans | host | Validate `PlanRequest`; responds `text/event-stream` (section 10.7) |
| GET /v1/events/:eventKey/plans/:functionId | host | Latest stored plan |
| POST /v1/events/:eventKey/gate-links | host | Body `{functionId, hours}`; returns gate URL `${APP_ORIGIN}/gate/{eventKey}?t={gateToken}` |
| GET /v1/gate/:eventKey/manifest?t= | gate token | `{functionId, publicKey, passes:[{passId, displayName, seats}]}` |
| POST /v1/gate/:eventKey/scans:batch?t= | gate token | Validate `GateScanBatch`; idempotent insert; returns arrivals and duplicate flags |
| POST /v1/events/:eventKey/outcome | host | `{functionId, actualCount, ranShort:string[], leftover:string[]}` |
| POST /v1/demo/seed | demo only | Creates the demo event (section 18.3); 404 unless `DEMO_MODE === "true"` |

Host auth: `Authorization: Bearer <Firebase ID token>`, verified in `auth/firebase.ts`:

```ts
import { createRemoteJWKSet, jwtVerify } from "jose";

const JWKS = createRemoteJWKSet(
  new URL("https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"),
);

export interface HostIdentity { uid: string; name?: string }

export async function verifyHost(authHeader: string | null, env: Env): Promise<HostIdentity> {
  if (env.DEMO_MODE === "true" && authHeader === "Demo demo-host") return { uid: "demo-host", name: "Demo host" };
  const token = authHeader?.startsWith("Bearer ") ? authHeader.slice(7) : null;
  if (!token) throw new HttpError(401, "missing bearer token");
  const { payload } = await jwtVerify(token, JWKS, {
    issuer: `https://securetoken.google.com/${env.FIREBASE_PROJECT_ID}`,
    audience: env.FIREBASE_PROJECT_ID,
  });
  if (!payload.sub) throw new HttpError(401, "token has no subject");
  return { uid: payload.sub, name: typeof payload.name === "string" ? payload.name : undefined };
}
```

Gate token (`auth/tokens.ts`): `${functionId}.${exp}.${mac}` where `mac` = first 22 base64url chars of HMAC-SHA-256(`TOKEN_HMAC_SECRET`, `gate.${eventKey}.${functionId}.${exp}`); reject when expired.

### 10.4 Durable Object SQL (`src/actors/migrations.ts`)

`MIGRATIONS` is an array of statement arrays; index 0 is version 1. Never edit an applied version; append a new one.

```ts
export const MIGRATIONS: string[][] = [
  [
    `CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)`,
    `CREATE TABLE fn (id TEXT PRIMARY KEY, name TEXT NOT NULL, starts_at TEXT NOT NULL,
       meal TEXT NOT NULL, rules_json TEXT NOT NULL, gate_mode TEXT NOT NULL)`,
    `CREATE TABLE household (id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE,
       display_name TEXT NOT NULL, phone_enc TEXT, phone_hash TEXT,
       seat_cap INTEGER NOT NULL, language TEXT NOT NULL DEFAULT 'en', created_at INTEGER NOT NULL)`,
    `CREATE TABLE invitation (household_id TEXT NOT NULL, function_id TEXT NOT NULL,
       PRIMARY KEY (household_id, function_id))`,
    `CREATE TABLE rsvp (household_id TEXT NOT NULL, function_id TEXT NOT NULL,
       status TEXT NOT NULL CHECK (status IN ('yes','no','maybe')),
       adults INTEGER NOT NULL, children INTEGER NOT NULL,
       meals_json TEXT NOT NULL, rules_json TEXT NOT NULL, allergies_json TEXT NOT NULL,
       arrival_window TEXT, updated_at INTEGER NOT NULL,
       PRIMARY KEY (household_id, function_id))`,
    `CREATE TABLE idempotency (key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at INTEGER NOT NULL)`,
    `CREATE TABLE pass (id TEXT PRIMARY KEY, household_id TEXT NOT NULL, function_id TEXT NOT NULL,
       seats INTEGER NOT NULL, token TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
       UNIQUE (household_id, function_id))`,
    `CREATE TABLE scan (device_id TEXT NOT NULL, seq INTEGER NOT NULL, pass_id TEXT NOT NULL,
       seats INTEGER NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (device_id, seq))`,
    `CREATE TABLE plan (function_id TEXT PRIMARY KEY, json TEXT NOT NULL, created_at INTEGER NOT NULL)`,
    `CREATE TABLE outcome (function_id TEXT PRIMARY KEY, json TEXT NOT NULL, created_at INTEGER NOT NULL)`,
    `CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
       payload TEXT NOT NULL, created_at INTEGER NOT NULL)`,
    `CREATE TABLE timer (kind TEXT PRIMARY KEY, due_at INTEGER NOT NULL)`,
    `CREATE TABLE attempt (bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, count INTEGER NOT NULL)`,
    `CREATE TABLE cohost (uid TEXT PRIMARY KEY, role TEXT NOT NULL CHECK (role IN ('edit','view')))`,
  ],
];
```

### 10.5 `EventActor` (`src/actors/EventActor.ts`)

Public RPC methods (called from the Worker through `env.EVENT.get(env.EVENT.idFromName(eventKey))`):

| Method | Notes |
| --- | --- |
| `isInitialized(): boolean` | |
| `init(input, hostUid, eventKey): {functions}` | Stores meta (title, type, tradition, venue, budget, host uid), creates `fn` rows with ids `fn_` + `randomKey(8)` |
| `assertAccess(uid, need: 'view' \| 'edit')` | Owner or co-host; else HttpError 403 |
| `addHouseholds(batch)` | Unique household keys via `randomKey(5)`; phone encrypted with `crypto/phone.ts` |
| `listHouseholds(includePhone)` | |
| `getInvitation(householdKey)` | |
| `submitRsvp(householdKey, input, idempotencyKey)` | See code below |
| `summary(): EventSummary` | SQL aggregate + `predictArrivals`; cached in memory, cleared on any write |
| `issuePasses(householdId)` | For each `yes` function with gate mode not `off`, sign a pass (exp = function start + 12 h) |
| `gateManifest(functionId)` / `recordScans(batch)` | Insert scans with `INSERT OR IGNORE`; arrivals = sum of seats; duplicate = pass whose scanned seats exceed its seats |
| `savePlan(functionId, plan)` / `getPlan(functionId)` | |
| `checkRate(bucket, limitPerMinute): void` | Fixed window; HttpError 429 when exceeded |
| `fetch(request)` | WebSocket upgrade only: `this.ctx.acceptWebSocket(server)` (hibernation API) |
| `alarm()` | Run due timers: `flush`, `reminder`, `retention` |

Required implementation of `submitRsvp` (adapt imports; keep the checks and the single transaction):

```ts
async submitRsvp(householdKey: string, input: RsvpInput, idempotencyKey: string) {
  const prior = this.sql
    .exec<{ response: string }>("SELECT response FROM idempotency WHERE key = ?", idempotencyKey)
    .toArray()[0];
  if (prior) return JSON.parse(prior.response);

  const hh = this.sql
    .exec<{ id: string; seat_cap: number }>("SELECT id, seat_cap FROM household WHERE key = ?", householdKey)
    .toArray()[0];
  if (!hh) throw new HttpError(404, "household not found");

  const invited = new Set(
    this.sql.exec<{ function_id: string }>(
      "SELECT function_id FROM invitation WHERE household_id = ?", hh.id,
    ).toArray().map((r) => r.function_id),
  );
  for (const f of input.functions) {
    if (!invited.has(f.functionId)) throw new HttpError(422, `not invited to ${f.functionId}`, "functions");
    const seats = f.attending === "no" ? 0 : f.adults + f.children;
    if (seats > hh.seat_cap) throw new HttpError(422, "seats exceed household cap", "adults");
    if (f.attending !== "no" && f.meals.veg + f.meals.jain + f.meals.nonVeg !== seats) {
      throw new HttpError(422, "meal counts must add up to seats", "meals");
    }
  }

  const now = Date.now();
  const response = this.ctx.storage.transactionSync(() => {
    for (const f of input.functions) {
      const yes = f.attending !== "no";
      this.sql.exec(
        `INSERT INTO rsvp (household_id, function_id, status, adults, children, meals_json,
           rules_json, allergies_json, arrival_window, updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT (household_id, function_id) DO UPDATE SET
           status = excluded.status, adults = excluded.adults, children = excluded.children,
           meals_json = excluded.meals_json, rules_json = excluded.rules_json,
           allergies_json = excluded.allergies_json, arrival_window = excluded.arrival_window,
           updated_at = excluded.updated_at`,
        hh.id, f.functionId, f.attending,
        yes ? f.adults : 0, yes ? f.children : 0,
        JSON.stringify(yes ? f.meals : { veg: 0, jain: 0, nonVeg: 0 }),
        JSON.stringify(f.dietaryRules), JSON.stringify(f.allergies), f.arrivalWindow ?? null, now,
      );
    }
    this.sql.exec(
      "INSERT INTO outbox (kind, payload, created_at) VALUES ('rsvp', ?, ?)",
      JSON.stringify({ householdId: hh.id, functions: input.functions.map((f) => ({
        functionId: f.functionId, status: f.attending,
        seats: f.attending === "no" ? 0 : f.adults + f.children,
      })) }),
      now,
    );
    const body = { ok: true, functions: input.functions.map((f) => f.functionId) };
    this.sql.exec(
      "INSERT INTO idempotency (key, response, created_at) VALUES (?, ?, ?)",
      idempotencyKey, JSON.stringify(body), now,
    );
    return body;
  });

  this.summaryCache = null;
  this.ensureTimer("flush", now + 15 * 60_000);
  this.broadcast({ type: "summary", summary: this.summary() });
  const passes = await this.issuePasses(hh.id);
  return { ...response, passes };
}
```

`summary()` must use one SQL aggregate per call:

```sql
SELECT f.id AS function_id, f.name, f.meal,
  COUNT(DISTINCT i.household_id) AS invited,
  COUNT(DISTINCT r.household_id) AS replied,
  COALESCE(SUM(CASE WHEN r.status = 'yes'   THEN r.adults + r.children END), 0) AS yes_seats,
  COALESCE(SUM(CASE WHEN r.status = 'maybe' THEN r.adults + r.children END), 0) AS maybe_seats,
  COALESCE(SUM(CASE WHEN r.status = 'no'    THEN h.seat_cap END), 0) AS no_seats,
  COALESCE(SUM(CASE WHEN r.status IS NULL   THEN h.seat_cap END), 0) AS no_reply_seats,
  COALESCE(SUM(CASE WHEN r.status = 'yes' THEN r.children END), 0) AS children,
  COALESCE(SUM(CASE WHEN r.status = 'yes' THEN json_extract(r.meals_json, '$.veg') END), 0) AS veg,
  COALESCE(SUM(CASE WHEN r.status = 'yes' THEN json_extract(r.meals_json, '$.jain') END), 0) AS jain,
  COALESCE(SUM(CASE WHEN r.status = 'yes' THEN json_extract(r.meals_json, '$.nonVeg') END), 0) AS non_veg
FROM fn f
JOIN invitation i ON i.function_id = f.id
JOIN household h ON h.id = i.household_id
LEFT JOIN rsvp r ON r.household_id = i.household_id AND r.function_id = f.id
GROUP BY f.id
```

Then per function: `prediction = predictArrivals({yes, maybe, noReply})`, `recommendedPlates = prediction.p80`, arrivals and duplicates from `scan` joined to `pass`.

Timers: one alarm per actor. `ensureTimer(kind, dueAt)` upserts the `timer` row only if no earlier due time exists, then sets the alarm to `MIN(due_at)`. `alarm()` runs every due timer, deletes it, and re-arms for the next.

`flush`: when `PERSISTENCE === "neon"`, read up to 500 outbox rows by `seq`, `OUTBOX.send({eventKey, rows})`, then delete rows with `seq <= max`. When `actor-only`, keep rows (they are backfilled at M7).

`retention`: set at `init` for last function start + 90 days; deletes `household`, `invitation`, `rsvp`, `pass`, `scan`, `idempotency` rows; keeps `fn`, `plan`, `outcome` and an aggregated snapshot in `meta`.

Broadcast: `for (const ws of this.ctx.getWebSockets()) ws.send(JSON.stringify(msg))`.

### 10.6 `HostDirectory`

SQLite table `event(event_key PRIMARY KEY, title, type, starts_at, created_at)`. RPC: `addEvent`, `listEvents`, `removeEvent`. Addressed by `idFromName(uid)`.

### 10.7 Planner pipeline (`src/planner/pipeline.ts`) [M5]

Input: `PlanRequest`, event meta, `FunctionSummary`, data.json. Output: `Plan`, streamed as SSE events:

```
event: stage      data: {"stage":"headcount"|"candidates"|"filter"|"quantities"|"budget"|"offers"}
event: category   data: {"category":"main","items":[PlanItem,...]}
event: done       data: Plan
event: error      data: {"title":"...","detail":"..."}
```

Steps:

1. **Headcount.** Use `prediction.likely` split into adults and children by the reply ratio (children / yes seats; default 10% when no replies). Veg, Jain and non-veg seat shares from replies (default 50/0/50 when none).
2. **Rules.** Function rules from the host (`fn.rules_json`). These are hard filters for every item.
3. **Allowed catalog.** `applyRules(catalog, rules, rulesData, allergiesUnion)`.
4. **Candidates.** Ask the LLM router (10.8) to choose item ids from the allowed list only, given the brief, meal, event type and headcount. Response schema: `{"starter":[ids],"main":[ids],"rice":[ids],"bread":[ids],"side":[ids],"dessert":[ids],"drink":[ids],"decor":[ids]}`. Drop any id not in the allowed list and log it as `removed: {itemId, reason: "not-allowed"}`. Then run `applyRules` again on the chosen items (defence in depth). If the router fails or returns nothing usable, use `planner/template.ts`: per course, the highest-`popularity` allowed items (2 mains, 1 starter, 1 rice, 1 bread, 1 dessert, 2 drinks, plates, ice, 1 decor).
5. **Diet coverage.** If there are veg seats, ensure at least one `diet:veg` main; if Jain seats, at least one Jain-compatible main (passes the `jain` rule). Non-veg mains are sized for non-veg seats; veg mains for veg seats; Jain mains for Jain seats. Shared items (rice, bread, dessert, drinks, supplies) for all seats.
6. **Quantities.** `rawQuantity(headcount for that item, consumptionRow(meal), dishesInCourse, DEFAULT_ALPHA, bufferFromPrediction(likely, p80))`, then `roundToPacks`. Supplies (plates, ice) use the same formula with k = 1. Decor and lighting: `max(1, ceil(guests / 50))`.
7. **Budget.** `fitBudget` with alternatives = allowed items of the same course with a lower `est_price_inr_per_unit`; mains are `mustHave`; decor priority 1, desserts 3, starters 4, drinks 5.
8. **Offers.** `adapters/commerce.ts` (10.9).
9. **Explanation.** One short line per course from the LLM given the final numbers; on failure, a template line such as "Sized for 38 adults and 4 children at 300 g per adult, 2 mains".
10. Save with `EventActor.savePlan` and emit `done`.

### 10.8 LLM router (`src/adapters/llm.ts`)

```ts
export interface LlmCall { system: string; user: string; maxTokens: number }
export interface LlmResult { text: string; route: "groq" | "workers-ai" }
export interface LlmAdapter { name: "groq" | "workers-ai"; complete(call: LlmCall): Promise<string> }
```

- Order: Groq (`GROQ_MODEL_LARGE`, JSON mode) when `GROQ_API_KEY` is set → Workers AI (`WORKERS_AI_MODEL`) → caller falls back to template.
- Groq: `POST https://api.groq.com/openai/v1/chat/completions` with `{model, messages, temperature: 0.3, max_tokens, response_format: {type: "json_object"}}`, `Authorization: Bearer`, `AbortSignal.timeout(20000)`.
- Workers AI: `env.AI.run(model, {messages, max_tokens})`; read `response`.
- Parse with Zod. On a parse failure, one repair attempt ("Return only valid JSON matching this schema"), then the next adapter.
- Never send phone numbers or household names to any LLM. Only event type, meal, counts, rules, brief and catalog ids and names.
- `mockLlm.ts` returns fixed JSON and is used in tests and when `DEMO_MODE` is on and no key is set.

### 10.9 Commerce links (`src/adapters/commerce.ts`)

From `channels.yaml`. Channel choice by `daysLeft` (from request or computed from function start):

| Item category | daysLeft < 1 | 1–7 | > 7 |
| --- | --- | --- | --- |
| food (cooked dishes) | food channels | food channels | food channels |
| beverage, supply | quick | quick + ecommerce | ecommerce + quick |
| decor, lighting, tableware | quick | ecommerce + quick | ecommerce |

URL = pattern with `{q}` replaced by `encodeURIComponent(searchQuery)`. No affiliate parameters in Phase A.

### 10.10 WhatsApp links (`src/adapters/whatsapp.ts`)

`https://wa.me/{digits}?text={encoded}` when the household has a phone (digits only, with country code; default `91` for 10-digit numbers), else `https://wa.me/?text={encoded}`. Message templates per language in `data/templates/traditions.yaml` under `invite_text`, with `{host}`, `{event}`, `{link}`, `{code}` placeholders.

### 10.11 Phone encryption (`src/crypto/phone.ts`)

AES-GCM with a 32-byte key from `PHONE_ENC_KEY` (base64); stored as `base64(iv).base64(ciphertext)`. `phone_hash` = HMAC-SHA-256 of the normalised number with `TOKEN_HMAC_SECRET`, hex.

### 10.12 Worker tests (`services/edge/test/`)

Use `@cloudflare/vitest-pool-workers` with `wrangler.jsonc`. Required tests:

1. Create event → add 3 households → summary shows 3 invited, 0 replied.
2. Submit RSVP twice with the same `Idempotency-Key` → one row, identical responses.
3. Seats over cap → 422 with `field: "adults"`; meals not adding up → 422 with `field: "meals"`.
4. Tampered link token → 404 (never reveal whether the event exists).
5. Typed-code resolve: 11th wrong attempt in a minute from one IP → 429.
6. WebSocket client receives a `summary` message after an RSVP.
7. Planner with `mockLlm` returning a forbidden item (`pork_ribs` under `halal`) → item absent from `items`, present in `removed`.
8. Planner with every adapter failing → template route, plan still complete.
9. Gate: forged pass rejected by `verifyPass`; duplicate scans from two devices flagged after `recordScans`.

---

## 11. `apps/guest-web` [M3]

### 11.1 Behaviour

- `/` → landing with a "Have a code?" box (→ `/r`) and one line about Hostora.
- `/r` → typed code entry (`XXXXX-XXXXX`, case-insensitive, dash optional) → `POST /v1/invites:resolve` → redirect to `/i/:eventKey/:token`.
- `/i/:eventKey/:token` → `GET /v1/invites/...`; shows household name, event, per-function cards. Each card: attending yes / maybe / no, adults and children steppers (≤ seat cap), meal steppers that must add up, dietary rule chips, allergy chips, arrival window select. Submit → `PUT .../rsvp` with an `Idempotency-Key` stored in `localStorage` per household (wrapped in try/catch).
- After submit: confirmation, map link, "Add to calendar" (`.ics` generated in the browser), and a QR pass per function when issued (render with `uqr` as SVG; offer "Save image").
- Footer: "Hostora beta: a non-commercial project" and the privacy notice link.

### 11.2 Language

`en` and `hi` JSON files; language from the invitation, switchable. All strings through `t(key)`.

### 11.3 Deployment

`wrangler.jsonc`: `{ "name": "hostora-guest", "assets": { "directory": "./dist", "not_found_handling": "single-page-application" }, "compatibility_date": "2026-10-01" }`.

### 11.4 Budget

`scripts/check-budget.mjs`: gzip every file in `apps/guest-web/dist` except images; fail when the sum exceeds 150 KB. Runs in CI after build.

---

## 12. `apps/host` [M4, M5, M6]

### 12.1 Routes

| Path | Page | Notes |
| --- | --- | --- |
| `/login` | Login | Firebase Google sign-in; "Try demo" button when `VITE_DEMO=true` (sends `Authorization: Demo demo-host`) |
| `/events` | Events | List from `GET /v1/events`; "New event" |
| `/events/new` | EventNew | Wizard: type → tradition template (functions pre-filled, editable) → date and meal per function → venue (label + map pin with MapLibre GL and OpenStreetMap tiles; lat/lng) → rules per function (chips) → budget |
| `/e/:eventKey` | Dashboard | Count cards per function, prediction band (low–likely–high and recommended plates), meal split, live via WebSocket; "Plan food" per function |
| `/e/:eventKey/guests` | Guests | Table; add manually; CSV import (`name,phone,seats,functions`); paste-from-contacts box |
| `/e/:eventKey/invite` | Invite | Per household: "Send on WhatsApp" (opens the wa.me URL), copy link, typed code; progress "34 of 60 sent" kept in IndexedDB |
| `/e/:eventKey/plan/:functionId` | Planner | Brief text box, days-left override, streamed categories, removed-items panel ("Removed for halal: Pork ribs"), budget bar, offer buttons per item, "Mark bought" |
| `/e/:eventKey/budget` | Budget | Planned vs bought by category |
| `/gate/:eventKey` | Gate | Token in `?t=`; works offline (section 12.3) |
| `/settings` | Settings | Language, sign out, delete account |

### 12.2 Libraries

React 18, React Router 6, TanStack Query 5, Firebase JS SDK (auth only, modular imports), vite-plugin-pwa (precache app shell), `idb`, `uqr`, `@zxing/browser`, `maplibre-gl`, `@hostora/engine`, `@hostora/schema`.

### 12.3 Gate mode

1. On open with network: fetch manifest, store passes and public key in IndexedDB (`gate/db.ts`).
2. Scan with `@zxing/browser` (rear camera). Verify with `verifyPass` from `@hostora/engine` against the stored public key and current time.
3. Look up seats used locally; show green (seats left), amber (soft mode: unknown pass, count anyway), red (strict mode: unknown pass, "ask family member").
4. Append each scan to a local log `{deviceId, seq, passId, seats, at}`; `deviceId` = random id kept in IndexedDB.
5. `gate/sync.ts` uploads unsent scans when online (`online` event and every 30 s); server is idempotent by `(deviceId, seq)`.
6. Manual lookup by household name from the manifest.
7. Never display diet, faith or phone data on this screen.

### 12.4 Design

Clean, high-contrast, large touch targets; light and dark; Noto Sans for Hindi. One accent colour. Respect the user's language setting everywhere.

---

## 13. Persistence and jobs [M7]

### 13.1 `db/migrations/0001_init.sql`

```sql
CREATE TABLE host (uid text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE event (
  event_key char(5) PRIMARY KEY, host_uid text NOT NULL REFERENCES host(uid),
  type text NOT NULL, tradition text NOT NULL, starts_at timestamptz,
  venue_lat double precision, venue_lng double precision, pincode char(6),
  budget_inr integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX event_host_idx ON event (host_uid, starts_at DESC);
CREATE TABLE fn (
  id text PRIMARY KEY, event_key char(5) NOT NULL REFERENCES event(event_key),
  name text NOT NULL, meal text NOT NULL, starts_at timestamptz NOT NULL,
  rules text[] NOT NULL DEFAULT '{}', gate_mode text NOT NULL);
CREATE TABLE household_feature (
  event_key char(5) NOT NULL, function_id text NOT NULL, household_id text NOT NULL,
  status text, replied_days_before integer, seats integer NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (function_id, household_id));          -- no names, no phones
CREATE TABLE plan (
  function_id text PRIMARY KEY REFERENCES fn(id), json jsonb NOT NULL,
  created_at timestamptz NOT NULL);
CREATE TABLE outcome (
  function_id text PRIMARY KEY REFERENCES fn(id), actual_count integer,
  ran_short text[], leftover text[], reported_at timestamptz NOT NULL);
CREATE TABLE outbox_applied (
  event_key char(5) NOT NULL, seq bigint NOT NULL, applied_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (event_key, seq));
```

### 13.2 Queue consumer

For each message: open one Neon transaction; for each row, `INSERT INTO outbox_applied ... ON CONFLICT DO NOTHING RETURNING seq`; only when a row was inserted, apply the change (upsert event, fn, household_feature, plan or outcome). Ack the message after commit; on error, `message.retry()`.

### 13.3 Workflows

- `nightly.yml` (cron `30 21 * * *`, which is 03:00 IST): `pg_dump $NEON_DATABASE_URL | gzip` uploaded to R2 `backups/YYYY-MM-DD.sql.gz` with the AWS CLI and the R2 S3 endpoint; delete objects older than 30 days.
- Reconciliation test: replay the same queue batch twice in a test and assert no duplicate rows.

---

## 14. CI/CD

### 14.1 `.github/workflows/ci.yml` [M0]

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm }
      - run: pnpm install --frozen-lockfile
      - run: pnpm data:build && git diff --exit-code services/edge/src/generated/data.json
        if: hashFiles('scripts/build-data.mjs') != ''
      - run: pnpm check
      - run: pnpm -r --if-present build
      - run: node scripts/check-budget.mjs
        if: hashFiles('scripts/check-budget.mjs') != ''
```

### 14.2 `.github/workflows/deploy.yml` [M3]

On push to `main` after `ci` passes: build, then `cloudflare/wrangler-action@v3` with `apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}`, `accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}`, `command: deploy`, once each for `services/edge`, `apps/guest-web`, `apps/host` (`workingDirectory`). The host build reads `VITE_*` from GitHub secrets.

---

## 15. Environment variables and secrets

| Name | Where | Used by | Milestone |
| --- | --- | --- | --- |
| `TOKEN_HMAC_SECRET` | Wrangler secret | Link and gate tokens, phone hash | M2 |
| `PHONE_ENC_KEY` | Wrangler secret | Phone encryption | M2 |
| `PASS_SIGNING_KEY` | Wrangler secret | Pass signing | M6 |
| `PASS_PUBLIC_KEY` | Wrangler var | Manifest | M6 |
| `GROQ_API_KEY` | Wrangler secret | LLM router | M5 (optional; template works without) |
| `NEON_DATABASE_URL` | Wrangler secret, GitHub secret | Consumer, backups | M7 |
| `DEMO_MODE` | Wrangler var | Demo auth and seed | M2 |
| `FIREBASE_PROJECT_ID` | Wrangler var | Token verification | M4 |
| `APP_ORIGIN`, `GUEST_ORIGIN` | Wrangler vars | CORS, links | M2 |
| `VITE_API_ORIGIN`, `VITE_FIREBASE_*`, `VITE_DEMO` | Build env | Host and guest apps | M3–M4 |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | GitHub secrets | Deploy | M3 |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | GitHub secrets | Backups | M7 |

`scripts/gen-keys.mjs` [M2] prints fresh values for `TOKEN_HMAC_SECRET` (32 random bytes, base64url), `PHONE_ENC_KEY` (32 bytes, base64) and an Ed25519 pair (via `generateKeyPairHex`).

---

## 16. Quality gates (every milestone)

| Gate | Command | Must be |
| --- | --- | --- |
| Lint and format | `pnpm lint` | No errors |
| Types | `pnpm typecheck` | No errors |
| Unit and property tests | `pnpm test` | All pass; engine still 15+ tests |
| Data | `pnpm data:build` | Exit 0; `data.json` unchanged in git |
| Guest budget | `node scripts/check-budget.mjs` | ≤ 150 KB gzipped |
| E2E (from M3) | `pnpm e2e` | All pass |
| Safety | Planner tests 7 and 8 (10.12) | Pass |

---

## 17. Milestones and acceptance checks

**Hackathon cut = M0 → M6, plus the demo seed and Hindi strings from M8.** About 3–5 focused days for one strong developer with an agent; the agent should be able to do each milestone in one session.

### M0 — Bootstrap

Tasks: verify the starter installs; add `README.md` (what it is, how to run, links to BRD and TDD); write ADR files 0001–0006 from section 3; add `ci.yml`; add the engine to CI.

Accept: `pnpm install && pnpm check` green locally and in GitHub Actions.

### M1 — Schema and data

Tasks: `packages/schema` exactly as section 8 with tests (valid and invalid samples for each schema); `scripts/build-data.mjs` per 9.6; generate `data.json`.

Accept: `pnpm data:build` prints counts and exits 0; a test copies the catalog with one undeclared food row and the script exits non-zero naming that row; `pnpm check` green.

### M2 — Edge core

Tasks: `services/edge` with `wrangler.jsonc`, env, Hono app, errors, auth (Firebase + demo), tokens, phone crypto, both actors, migrations, routes from 10.3 except plans and gate, demo seed endpoint (section 18.3), `gen-keys.mjs`.

Accept: Worker tests 1–6 (10.12) pass; `wrangler dev` runs; `curl` sequence create → households → invites:prepare → GET invite → PUT rsvp → summary works with demo auth.

### M3 — Guest web

Tasks: section 11; `check-budget.mjs`; Playwright config and `e2e/tests/rsvp.spec.ts` (mobile viewport: open link, reply 3 adults + 1 child, meals 2 veg + 2 non-veg, see confirmation; edit reply; typed-code flow); `deploy.yml`.

Accept: e2e passes against `wrangler dev`; budget ≤ 150 KB gzipped; Lighthouse mobile performance ≥ 90 on the invite page; deployed to `*.workers.dev`.

### M4 — Host app core

Tasks: section 12 routes Login, Events, EventNew, Dashboard, Guests, Invite, Settings; live WebSocket; CSV import; PWA install.

Accept: `e2e/tests/host.spec.ts`: demo login → create event with 2 functions → import 5 households from CSV → open one invite link in a second page → reply → dashboard count updates without reload.

### M5 — Planner and shopping

Tasks: 10.7–10.10; Planner and Budget pages; verify every channel URL pattern and fix `channels.yaml`.

Accept: Worker tests 7–8; `e2e/tests/planner.spec.ts` with mock LLM: plan streams all categories, a brief asking for pork under halal shows it in "Removed", each item has at least one offer link; with a real `GROQ_API_KEY`, first category arrives in under 15 s.

### M6 — Gate

Tasks: passes on RSVP for gate-enabled functions; gate links; manifest; offline gate page; sync; duplicate flags on the dashboard.

Accept: Worker test 9; `e2e/tests/gate.spec.ts`: load gate page, go offline (`context.setOffline(true)`), scan 3 passes via an injected test hook, see green results, go online, sync, dashboard arrivals = sum of seats; a forged pass shows red.

**— Hackathon cut complete: deploy, seed demo, rehearse section 18. —**

### M7 — Persistence and jobs

Tasks: section 13; set `PERSISTENCE=neon`; backfill existing actors' outboxes.

Accept: replaying one batch twice leaves row counts unchanged; nightly workflow uploads a backup to R2 on manual dispatch.

### M8 — Quality

Tasks: Hindi strings complete; `data/evals/briefs.jsonl` (≥ 50 briefs) and `adversarial.jsonl` (≥ 100 cases) run by a Vitest eval runner against the pipeline with mock and real LLMs; axe-core checks in e2e; k6 script for the RSVP path; Lighthouse CI.

Accept: zero safety violations across the adversarial set; axe: no serious or critical issues; k6 at 2× expected peak with p95 < 300 ms.

### M9 — After the hackathon

Turnout model in `ml/` (LightGBM quantile regression, trained in Actions, coefficients published to KV); WhatsApp verification webhook; Swiggy Builders Club adapter if access is granted; Capacitor Android build; TLA+ specs and simulation tests (TDD section 19).

---

## 18. Hackathon kit

### 18.1 Three-minute demo script

| Time | Show | Say |
| --- | --- | --- |
| 0:00 | Title slide with one number | "Families pay for 500 plates and 400 people come. 10–20% of wedding food is wasted." |
| 0:20 | Dashboard of the seeded demo event | "Every household got its own code. No app, no login." |
| 0:40 | QR on screen → judges reply on their phones | Live counter ticks up in real time. |
| 1:10 | Prediction band | "We'd tell the caterer 438 plates, not 550." |
| 1:30 | Planner, brief: "interfaith reception, halal, ₹60,000, add pork ribs" | Plan streams in; pork ribs listed under "Removed for halal". "The AI suggests; rules decide." |
| 2:05 | Shopping cards; switch days-left to 0 | Links move from Amazon to 10-minute delivery. |
| 2:25 | Phone in airplane mode at the gate | Pass scans green offline; after reconnect, a duplicate is flagged. |
| 2:50 | Architecture slide + "₹0 bill" | "Runs entirely on free tiers; scales by changing plans, not code." |

### 18.2 Judging criteria map

| Criterion | Evidence to show |
| --- | --- |
| Problem and impact | Food-waste numbers; plates saved per event |
| Technical depth | Actor per event, transactional outbox, offline signed passes, property-tested safety filter |
| Use of AI | Router with fallback; LLM proposes, deterministic engine decides; streamed plan |
| Completeness | Live deployed URL; guest, host and gate flows end to end |
| Design and accessibility | Mobile-first, Hindi, large targets, works on a slow phone |
| Feasibility | ₹0 running cost; conversion plan (TDD section 18) |

### 18.3 Demo data

`POST /v1/demo/seed` creates event "Arjun & Sara — Reception (demo)", tradition `none`, functions "Lunch" (rules `[]`) and "Reception" (rules `["halal","no_beef"]`, gate `soft`), 60 households with seat caps 1–6, and replies for 70% of households (mixed yes, maybe, no; mixed meals). It returns 5 invite links reserved for judges to use live. Deterministic: seeded random generator, so the demo looks the same every time.

### 18.4 Backup plan

- Recorded 3-minute video of the full demo.
- Demo works without `GROQ_API_KEY` (template route).
- A second deployed copy on another `workers.dev` subdomain.
- Gate demo needs no network by design.

### 18.5 Pitch deck outline (6 slides)

1. Problem: guesswork, waste, money.
2. Product: count → plan → cart → gate.
3. Live demo.
4. How it works: architecture diagram, safety guarantees.
5. Impact and validation: pilot numbers or survey results.
6. What's next: commercial path and roadmap.

---

## 19. Deployment runbook (first time)

1. Accounts, all free with no card: Cloudflare, Firebase (enable Google sign-in; add the app domain to authorised domains), Groq (API key, optional), GitHub.
2. `pnpm install && pnpm dlx wrangler login`
3. Create resources and paste their ids into `services/edge/wrangler.jsonc`:

   ```
   pnpm dlx wrangler kv namespace create CONFIG
   pnpm dlx wrangler r2 bucket create hostora-files
   pnpm dlx wrangler queues create hostora-outbox
   pnpm dlx wrangler queues create hostora-outbox-dlq
   ```

4. `node scripts/gen-keys.mjs`, then for each secret: `pnpm --filter edge exec wrangler secret put TOKEN_HMAC_SECRET` (and `PHONE_ENC_KEY`, `PASS_SIGNING_KEY`, `GROQ_API_KEY`). Put the public key in `PASS_PUBLIC_KEY`.
5. Deploy: `pnpm --filter edge exec wrangler deploy`, then build and deploy `apps/guest-web` and `apps/host` the same way.
6. Domain (TDD 17.3): add custom domains — apex and `www` → `hostora-guest`, `app.` → `hostora-host`, `api.` → `hostora-edge`. Update `APP_ORIGIN`, `GUEST_ORIGIN`, `VITE_API_ORIGIN`.
7. For demos: set `DEMO_MODE=true`, redeploy, then `curl -X POST https://api.<domain>/v1/demo/seed -H "Authorization: Demo demo-host"`.
8. Add the GitHub secrets from section 15 so `deploy.yml` takes over.

---

## 20. Definition of done for the whole project

- [ ] Every milestone's acceptance checks pass in CI.
- [ ] Live URLs for guest, host and API on the project domain.
- [ ] Demo seed works and the 3-minute script runs without a network at the gate.
- [ ] Zero safety-rule violations across the adversarial eval set.
- [ ] README explains setup in under 10 commands.
