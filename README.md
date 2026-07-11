# VendorScreen

Automated KYC/AML compliance screening for vendors on Monday.com boards. It ships as a Monday.com **integration recipe**: when a new item is created, the app queries the OpenSanctions API and writes a risk level — **Clear**, **Warning**, or **Critical** — plus supporting notes back to the columns the customer chose.

If OpenSanctions is temporarily unreachable, the app retries transient failures (429 / 5xx / network errors) with backoff and, if it still can't complete, writes a **Screening Failed** status instead of leaving the record blank — so the check is never lost silently and the customer can re-run it.

> ⚠️ **Informational tool only.** VendorScreen provides indicative screening signals and **does not make compliance decisions**. Results may be incomplete or inaccurate and must be independently verified. Responsibility for regulatory compliance rests with the customer. See [Terms of Service](./TERMS_OF_SERVICE.md) and [Privacy Policy](./PRIVACY_POLICY.md).

## How it works (recipe model)

The app is **multi-tenant** — it is not tied to any specific board. Each customer wires it up through the recipe UI, and Monday.com passes the board/column IDs to the app at runtime.

```
Recipe trigger: "When an item is created"
  └── Monday POSTs to /monday/execute_action
        ├── Challenge handshake (action URL registration only)
        ├── JWT verification via MONDAY_SIGNING_SECRET (production)
        ├── reads payload.inboundFieldValues → boardId, itemId, statusColumnId,
        │     detailsColumnId, [countryColumnId — optional]
        │     (chosen by the CUSTOMER in the automation UI — NOT from .env)
        ├── 200 OK returned immediately
        └── async process_vendor()
              ├── monday_service.get_item_name(item_id)   → vendor name
              ├── [optional] get_item_column_text(countryColumnId) → country
              ├── sanctions_service: POST /match/default (name + country + schema)
              │     ├── scored candidates → Clear / Warning / Critical (score thresholds)
              │     └── retries 429/5xx/network; if still down → Screening Failed
              ├── monday_service.update_vendor_record()
              │     └── writes status by LABEL (create_labels_if_missing) + details text
              ├── repository.record_event()  → append outcome to audit log (if DB on)
              └── if Critical → create_notification() alerts the automation owner
```

Because the status is written **by label** (not by a hard-coded index) and missing labels are auto-created, the app works on any customer board regardless of column order or naming.

## Usage limits (multi-tenant metering)

When `DATABASE_URL` is set, each Monday account is metered against a monthly screening allowance keyed by its plan (`free` / `pro` / `business` — see [MONETIZATION.md](./MONETIZATION.md) for pricing and limits). Before each paid OpenSanctions call, the app atomically consumes one screening from the account's quota; once the allowance is exhausted the item is marked **Screening Failed** with a message to upgrade or wait for the next period, and no OpenSanctions call is made. The account (`accounts`) and counter (`usage_counters`) tables are created automatically by the startup migration.

The counter's period key is `YYYY-MM`, so allowances reset at the month boundary with no scheduled job. The database is **optional**: with `DATABASE_URL` unset, metering is disabled and every request is screened — identical to prior behavior. A database error never blocks a screening — at runtime the quota check fails open, and a startup connection failure disables limits rather than taking the app down (both are logged and reported to Sentry) — so metering can't take the core product down.

### Plan sync (monday.com Monetization)

`POST /monday/subscription_webhook` receives subscription created/changed/renewed/cancelled events from monday's built-in Monetization and updates `accounts.plan` accordingly — a customer's plan is driven by what they're actually subscribed to on monday, not set manually in the database. Plan ids configured in Developer Center → Monetization must match the `PLAN_LIMITS` keys in `src/repository.py` (`pro`, `business`) exactly; an unrecognized plan id falls back to `free`. See [MONETIZATION.md](./MONETIZATION.md) for the full pricing model and setup steps.

## Audit log & export

When `DATABASE_URL` is set, every screening outcome is appended to an **append-only audit log** (`screening_events`) — one row per board write, with the risk level and a **trimmed match summary** (top match's score, entity id, and caption), never the raw provider payload. Over-limit and screening-failed outcomes are logged too, so the trail shows items that were received but not screened. Auditing is fail-open: a log write never blocks or fails a screening that already reached the board, and with the database disabled it is simply skipped.

Customers export their own audit trail as CSV through a second recipe action, without VendorScreen needing a user-facing frontend or login:

```
Recipe action "Export screening audit"
  └── Monday POSTs to /monday/export_action
        ├── JWT verification (accountId + userId from the token)
        ├── mints a short-lived (15 min) signed token scoped to the account
        │     (signed with MONDAY_SIGNING_SECRET — no separate password store)
        └── create_notification → DMs the user a link:
              /audit/export?token=<signed token>
                    └── verifies token → streams account-scoped CSV
```

The token **is** the one-time credential: because the app only ever holds a monday JWT during a recipe action, a browser download can't ride a session — so the signed, account-scoped, quickly-expiring token both authenticates and scopes the download. An invalid or expired link returns `401`.

## Critical-risk alerts

When a vendor screens as **Critical**, VendorScreen sends the automation owner a monday notification (`create_notification`, anchored to the item) so a hard sanction hit surfaces in the bell menu immediately, not only as a column change. Only Critical fires an alert — Clear and Warning stay silent to keep the bell free of noise. The recipient is the `userId` from the recipe action's JWT (absent in dev, so alerting is skipped there). Alerting is fail-open: a notification failure is logged and reported to Sentry but never breaks or blocks the screening that already reached the board. Requires the `notifications:write` scope (the same scope the export action needs).

### Capacity and scaling

The default provider is Neon's free tier, which is sized by two independent limits: **compute** (scale-to-zero, so idle time is free — a good fit for spiky screening traffic) and **storage**. Storage is the one to watch now that the audit log and (later) ongoing-monitoring history accumulate: per-screening rows are kept lean (a trimmed match summary — top match, score, entity id — not the full raw provider payload), so the free tier stretches to well over a million rows. When real volume from monitoring outgrows it, upgrading is a **plan change in the Neon console — same project, same `DATABASE_URL`, no code change or data migration**. The single-env-var + repository layer keeps the provider swappable regardless.

---

## Environment variables

Only **secrets** live in the environment. Board and column IDs come from the recipe payload, not from here.

| Variable | Where | Description |
|---|---|---|
| `MONDAY_SIGNING_SECRET` | Monday Code + local | JWT verification for recipe action requests (Developer Center → App credentials → signing_secret) |
| `OPENSANCTIONS_API_KEY` | Monday Code + local | OpenSanctions authentication |
| `APP_ENV` | Monday Code + local | `production` in deploy, `development` locally (`NODE_ENV` is still honored for backwards compatibility) |
| `MONDAY_API_TOKEN` | **local dev only** | Personal API token used only when no `Authorization` header is present (dev mode). Not needed in production — the token comes from the JWT. |
| `SENTRY_DSN` | **optional** | Enables Sentry error tracking when set. Unset = tracking disabled, app runs unchanged. PII is never sent (`send_default_pii=False`). |
| `SENTRY_TRACES_SAMPLE_RATE` | **optional** | Performance tracing sample rate (e.g. `0.1`). Defaults to `0` (errors only). |
| `MATCH_SCHEMA` | **optional** | OpenSanctions entity type matched against in `/match`. Defaults to `Company` (vendors); set `Person` for boards of individual vendors. |
| `MATCH_SCORE_CRITICAL` | **optional** | Minimum candidate score (0–1) for a sanction hit to be **Critical**. Default `0.85`. |
| `MATCH_SCORE_WARNING` | **optional** | Score floor (0–1) below which candidates are treated as noise; sanction/PEP hits at/above it (but below critical) are **Warning**. Default `0.70`. |
| `DATABASE_URL` | **optional** | Postgres connection string (e.g. [Neon](https://neon.com)). Enables per-account monthly usage limits. Unset = limits disabled, app runs unchanged. Migrations apply automatically on startup. |
| `DB_POOL_MIN` / `DB_POOL_MAX` | **optional** | Connection pool bounds. Default `0` / `5`. `min=0` holds no connection while idle so Neon can scale its compute to zero; set `1` on an always-on plan for a warm connection. |
| `DB_POOL_MAX_IDLE` | **optional** | Seconds before an idle pooled connection is recycled. Default `240` — below Neon's autosuspend, so a connection closed server-side while idle is never handed back out. |
| `PORT` | auto | Provided by Monday Code; defaults to `3000` locally |

> The old single-tenant variables (`MONDAY_BOARD_ID`, `COLUMN_ID_STATUS`, `COLUMN_ID_DETAILS`, `COLUMN_ID_COUNTRY`) are **no longer used** — the app reads these from the recipe input fields.

---

## Local development

The app code lives in [`src/`](./src) (that folder is what gets deployed). Dev
tooling (tests, CI) stays in the repo root.

### 1. Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # app deps + test/lint tools
```

### 2. Configure environment variables
Set the variables above in a local `.env`.

### 3. Start the server
```bash
python src/main.py   # port 3000
```
Health check: `GET /` returns `{ "status": "ok" }`.

### 4. Expose via ngrok for recipe testing
```bash
ngrok http 3000
```
Use the generated HTTPS URL + `/monday/execute_action` as the action URL while testing the recipe in Developer Center. In dev mode (`APP_ENV=development`) requests without an `Authorization` header fall back to `MONDAY_API_TOKEN`.

---

## Recipe configuration (Developer Center)

The code does nothing until the integration recipe is configured. Field names below **must match** those read in `main.py` (`boardId`, `itemId`, `statusColumnId`, `detailsColumnId`).

1. **Create App → Add Feature → Integration.**
2. **Trigger:** built-in *"When an item is created"* (Monday manages the subscription and supplies `boardId` + `itemId`).
3. **Custom Action** *"Screen vendor & update status"* with input fields:
   - `boardId` — type **Board**
   - `itemId` — type **Item** (from the trigger)
   - `statusColumnId` — type **Status Column** (picker)
   - `detailsColumnId` — type **Text / Long Text Column** (picker)
   - `countryColumnId` — type **Country / Text Column** (picker), **optional** — when mapped, the vendor's country is sent to OpenSanctions `/match` to sharpen scoring and cut false positives
4. **Action URL:** `https://<your-app>.monday.app/monday/execute_action`
5. **Recipe sentence:** *"When an item is created, screen the vendor and set {statusColumn} with details in {detailsColumn}"* — this is where the customer maps their own columns.
6. **(Optional) Export action** *"Export screening audit"* — a button-triggered recipe with a single `itemId` input field, action URL `https://<your-app>.monday.app/monday/export_action`. Running it DMs the user a 15-minute CSV download link for their account's audit log. Requires `notifications:write` in addition to the scopes below.
7. **Scopes:** `boards:read`, `boards:write` (add `notifications:write` for the export action).

---

## Deployment (Monday Code)

### First-time setup
```bash
npm install -g @mondaycom/mapps-cli
mapps init   # requires App ID from Developer Center
```

### Set environment variables in Monday Code
Monday Code does NOT read your local `.env` — set each secret once:
```bash
mapps code:env --mode set --key APP_ENV --value production
mapps code:env --mode set --key MONDAY_SIGNING_SECRET --value <value>
mapps code:env --mode set --key OPENSANCTIONS_API_KEY --value <value>
```
> Without `MONDAY_SIGNING_SECRET`, every real request fails JWT verification and returns 401.

To view current variables:
```bash
mapps code:env --mode list
```

### Deploy
```bash
mapps code:push
```
> Monday Code supports Python natively — no Dockerfile needed. The build detects the runtime from `requirements.txt` and starts the app with the `web:` command in [`Procfile`](./src/Procfile) (same layout as monday's official [quickstart-python-fastapi](https://github.com/mondaycom/monday-code-quickstarts/tree/master/quickstart-python-fastapi)).

After deploy, set the recipe **Action URL** in Developer Center to the new `*.monday.app` URL + `/monday/execute_action`.

Re-run `mapps code:push` whenever you change the code — each push creates a new version that must go through review before customers receive it.
