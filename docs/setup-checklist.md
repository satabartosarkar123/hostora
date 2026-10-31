# Hostora — Setup & Deployment Checklist

> **Security Rule:** Never paste secret values into chat or commit them to git. Put secrets only in your local `.env` (git-ignored), Render Environment settings, GitHub Repository Secrets, or Hugging Face Space Secrets.

---

## 1. Needed Now (Local Development — M0 to M2)

- [ ] **Local Secrets (`SECRET_KEY`, `TOKEN_HMAC_SECRET`, `PHONE_ENC_KEY`, `PASS_SIGNING_KEY`, `PASS_PUBLIC_KEY`, `CRON_SECRET`)**
  - **What & Why:** Cryptographic keys for session cookies, HMAC link/gate tokens, AES-256-GCM phone encryption, Ed25519 offline QR gate passes, and signed cron webhooks.
  - **How to generate:** Run `uv run hostora gen-keys` in the repository root. (`PASS_PUBLIC_KEY` is public; the other five are secret.)
  - **Classification:** `SECRET` (`PASS_PUBLIC_KEY` is `PUBLIC`)
  - **Where it goes:** Local `.env`, Render Environment, Hugging Face Space Secrets (`CRON_SECRET` also goes in GitHub Actions Secrets).
  - **Cost:** Free (generated locally).

- [ ] **PostgreSQL Database (`DATABASE_URL`, `TEST_DATABASE_URL`)**
  - **What & Why:** Primary transactional store (households, RSVPs, idempotency, plans, passes, scans, event log) and isolated test database.
  - **How to create:**
    - **Local Docker:** `make db-up && docker compose exec db createdb -U hostora hostora_test`
    - **Neon (Cloud, Free):** Sign up at https://console.neon.tech → Create Project (region: **Singapore `ap-southeast-1`**) → copy the **direct** connection string (hostname without `-pooler`) → create a second branch named `test` for `TEST_DATABASE_URL` (verify current free-tier storage/compute limits in Neon console).
  - **Classification:** `SECRET` (contains database password)
  - **Where it goes:** Local `.env` (`DATABASE_URL`, `TEST_DATABASE_URL`), Render Environment (`DATABASE_URL`), GitHub Actions Secret (`DATABASE_URL` if needed by backup workflow).
  - **Cost:** Free tier, no credit card required.

- [ ] **GitHub Repository**
  - **What & Why:** Hosts source code, runs CI/E2E tests, and triggers scheduled cron and encrypted nightly database backups via GitHub Actions.
  - **How to create:** https://github.com/new → create repository (e.g. `satabartosarkar123/hostora`).
  - **Classification:** `PUBLIC` (repository URL/name)
  - **Where it goes:** `README.md` badges and git remote `origin`.
  - **Cost:** Free, no credit card required.

---

## 2. Needed for M3 (First Cloud Deploy & Guest Web)

- [ ] **Render Web Service (`PUBLIC_ORIGIN` + `render.yaml` Blueprint)**
  - **What & Why:** Hosts the Dockerized FastAPI monolith (`hostora.main:create_app`) in the Singapore region.
  - **How to create:** Sign in at https://dashboard.render.com → **New +** → **Blueprint** → connect your GitHub repository (`render.yaml` is pre-configured) → paste the `sync: false` environment variables → deploy and copy the `https://<service>.onrender.com` URL into `PUBLIC_ORIGIN` (verify Blueprint auto-deploy settings in Render docs).
  - **Classification:** `PUBLIC` (`PUBLIC_ORIGIN`)
  - **Where it goes:** Local `.env`, Render Environment (`PUBLIC_ORIGIN`), GitHub Repository Variable (`APP_URL`).
  - **Cost:** Free web service plan, no credit card required (sleeps after 15 min idle).

- [ ] **Custom Domain (Optional for M3, Recommended for Demo)**
  - **What & Why:** Clean memorable URL for guest invite links and QR passes (e.g. `https://hostora.is-a.dev`).
  - **How to create:** Open a PR at https://github.com/is-a-dev/register adding `domains/hostora.json` pointing `CNAME` to `<service>.onrender.com` (or use a GitHub Student Pack domain with Cloudflare DNS at https://dash.cloudflare.com), then add the custom domain in Render → Settings → Custom Domains.
  - **Classification:** `PUBLIC`
  - **Where it goes:** Render `PUBLIC_ORIGIN`, Firebase Authorized Domains, GitHub Variable `APP_URL`.
  - **Cost:** Free, no credit card required.

---

## 3. Needed for M4 (Host Authentication)

- [ ] **Firebase Authentication (`FIREBASE_PROJECT_ID`, `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_APP_ID`)**
  - **What & Why:** Google sign-in for hosts on `/app/login`, verified server-side via Firebase RS256 JWKS (`PyJWT`).
  - **How to create:** Go to https://console.firebase.google.com → **Create a project** → **Build → Authentication → Get started** → Enable **Google** provider → **Settings → Authorized domains** → add `localhost`, your `*.onrender.com` domain, and custom domain → **Project Overview → Add app → Web (`</>`)** → copy `projectId`, `apiKey`, `authDomain`, and `appId` (verify in Firebase web setup docs).
  - **Classification:** `PUBLIC` (Firebase web client config is public by design)
  - **Where it goes:** Local `.env`, Render Environment, Hugging Face Space Secrets/Variables.
  - **Cost:** Free Spark plan (up to 50,000 MAUs), no credit card required.

---

## 4. Needed for M5 (AI Planner — Optional, Falls Back to Deterministic Template)

- [ ] **Groq API Key (`GROQ_API_KEY`)**
  - **What & Why:** Primary fast LLM provider (`openai/gpt-oss-120b` and `llama-3.1-8b-instant`) for proposing allowed catalog dish IDs and 1-line course explanations.
  - **How to create:** Sign in at https://console.groq.com/keys → **Create API Key** (verify current free rate limits and model IDs in Groq docs).
  - **Classification:** `SECRET`
  - **Where it goes:** Local `.env`, Render Environment, Hugging Face Space Secrets.
  - **Cost:** Free developer tier, no credit card required.

- [ ] **Google AI Studio Gemini Key (`GEMINI_API_KEY`, `GEMINI_ALLOWED`) — Optional Fallback**
  - **What & Why:** Secondary LLM fallback when Groq is unavailable; only called when `GEMINI_ALLOWED=true` and only with PII-redacted text.
  - **How to create:** Sign in at https://aistudio.google.com/app/apikey → **Create API key** (verify current free-tier data usage terms).
  - **Classification:** `SECRET` (`GEMINI_API_KEY`), `PUBLIC` (`GEMINI_ALLOWED`)
  - **Where it goes:** Local `.env`, Render Environment.
  - **Cost:** Free tier, no credit card required.

---

## 5. Needed for M7 (Operations, Cron, Backups & Monitoring)

- [ ] **GitHub Actions Cron & Backup Configuration (`APP_URL`, `CRON_SECRET`, `BACKUP_PASSPHRASE`, `DATABASE_URL`)**
  - **What & Why:** Runs daily `retention` and `cleanup` jobs against `/internal/cron/{job}` and stores AES-256-CBC encrypted nightly `pg_dump` artifacts.
  - **How to create:** In your GitHub repo → **Settings → Secrets and variables → Actions**:
    - Under **Variables**, add `APP_URL` = your live Render URL.
    - Under **Secrets**, add `CRON_SECRET` (matching Render), `DATABASE_URL` (Neon direct URL), and `BACKUP_PASSPHRASE` (generate with `openssl rand -base64 32` and save in your password manager).
  - **Classification:** `APP_URL` is `PUBLIC`; `CRON_SECRET`, `DATABASE_URL`, `BACKUP_PASSPHRASE` are `SECRET`.
  - **Where it goes:** GitHub Actions Variables and Secrets.
  - **Cost:** Free on public GitHub repositories (2,000 min/month on private free repos).

- [ ] **Keep-Warm Uptime Monitor (UptimeRobot or cron-job.org)**
  - **What & Why:** Pings `GET /healthz` every 5–10 minutes on demo days so Render's free instance does not sleep after 15 minutes idle.
  - **How to create:** https://uptimerobot.com or https://cron-job.org → Create HTTP(s) monitor pointing to `https://<your-domain>/healthz` every 5 minutes (enable before demo day, pause afterwards).
  - **Classification:** `PUBLIC`
  - **Where it goes:** External monitor dashboard.
  - **Cost:** Free tier, no credit card required.

- [ ] **Sentry Error Tracking (`SENTRY_DSN`) — Optional**
  - **What & Why:** Captures unhandled server exceptions with `send_default_pii=False`.
  - **How to create:** https://sentry.io → Create Project (FastAPI) → copy DSN.
  - **Classification:** `PUBLIC` / semi-secret DSN
  - **Where it goes:** Local `.env`, Render Environment.
  - **Cost:** Free developer plan, no credit card required.

- [ ] **Hugging Face Docker Space Backup (`HF_TOKEN`) — Optional Demo Backup**
  - **What & Why:** Second live deployment of the Docker container for demo resilience.
  - **How to create:** https://huggingface.co/new-space → choose **Docker** SDK → in Space Settings add the environment secrets (using a separate Neon branch) → create a write token at https://huggingface.co/settings/tokens if using automated sync.
  - **Classification:** `HF_TOKEN` is `SECRET`; Space URL is `PUBLIC`.
  - **Where it goes:** GitHub Actions Secret (`HF_TOKEN`), Hugging Face Space Settings.
  - **Cost:** Free CPU Basic Space, no credit card required.

