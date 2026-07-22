# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repo.

## What this is

VendorScreen is a **monday.com marketplace app**: a multi-tenant integration recipe that screens newly-added vendor items against sanction/PEP lists (via OpenSanctions) and writes the result back to columns the customer chose. It is not tied to any specific board — board/column IDs always come from the recipe payload at runtime, never from `.env`.

Full product/architecture description: [README.md](README.md). Pricing/plan logic: [MONETIZATION.md](MONETIZATION.md). Pre-promotion checklist: [SMOKE_TEST.md](SMOKE_TEST.md). `TECH_SPEC.md` is an early draft and is **stale** (references a single-tenant `/webhook` endpoint and env vars that no longer exist) — trust README.md over it.

## Layout

- `src/` — the actual deployed app (FastAPI + Uvicorn). This is what CI/CD ships via `mapps code:push` on every merge to `main` — see "Delivery is CI/CD" below.
  - `main.py` — HTTP routes (`/monday/execute_action`, `/monday/export_action`, `/monday/subscription_webhook`, `/audit/export`), JWT verification, field extraction/validation, `process_vendor()` orchestration.
  - `monday_service.py` — all monday.com GraphQL calls (item lookups, column writes, notifications).
  - `sanctions_service.py` — OpenSanctions `/match` calls, retry/backoff, risk-level scoring.
  - `repository.py` / `db.py` — optional Postgres-backed usage metering + audit log (no-ops when `DATABASE_URL` is unset).
  - `export_token.py` — signs the short-lived tokens for the CSV audit-export download link.
  - `observability.py` — Sentry init (fail-open everywhere it's used).
- `tests/` — pytest, mirrors `src/` one-to-one. Run from repo root; `pyproject.toml` puts `src/` on `pythonpath`.
- `scripts/e2e_smoke.py` — end-to-end smoke test against a real deployed draft URL (see SMOKE_TEST.md).
- `.claude/skills/monday-platform/` — the reference skill for anything touching monday's GraphQL API, automation recipes/blocks, JWT/signing-secret verification, or monday Code. Load it before making platform-config changes.

## How this app is wired into monday.com

VendorScreen ships two **Automation Blocks** (actions) exposed via monday's Developer Center, and two **Automation Templates** (pre-wired trigger+action combos, "managed templates") that bundle them for one-click install:

| Feature | Type | Action URL |
|---|---|---|
| Screen vendor and update columns | Automation Block | `/monday/execute_action` |
| Vendor Screener | Automation Template | wraps the block above |
| Export screening audit | Automation Block | `/monday/export_action` |
| Export Screening Audit | Automation Template | wraps the block above |

Input field **keys must match exactly** what `main.py` reads from `payload.inboundFieldValues`: `boardId`, `itemId`, `statusColumnId`, `detailsColumnId`, `countryColumnId` (optional). A mismatch here fails silently as a missing field, not a clear error — see the "known gotcha" below.

**Managed templates auto-propagate.** Editing a template's trigger/config in Developer Center pushes to every customer who already installed it — there is no staging step. Treat trigger changes on a managed template as a live, blast-radius-across-all-tenants change, not a local edit.

## Known gotcha: boardId isn't reliably supplied by every trigger

`Board`'s field source is "Variable in context" at the Automation Block level. Empirically, this resolves correctly under monday's native `When an item is created` trigger but **not** reliably under `When button clicked` (its trigger output is only `userId, itemId, groupId, item` — no board context), which surfaces on a live run as monday's own `"This automation is missing required fields"` error, even when the sentence-mapped Status/Details/Country columns are correctly filled in. This looks like a monday platform inconsistency tied to the legacy Sentence-Builder-vs-new-workflows migration, not an app bug.

**Fix already in place:** `execute_action` falls back to resolving `board_id` from `item_id` via `monday_service.get_item_board_id()` (a GraphQL `items(ids: ...) { board { id } }` lookup) whenever monday doesn't supply `boardId` directly. This makes the action trigger-agnostic — if you add a new trigger to a template and boardId goes missing again, this fallback is why it should still work; if it doesn't, suspect `itemId` resolution instead.

If monday's "Edit fields here" deep-link on a failed run 404s, that's a known-broken link on monday's side (same migration) — don't chase it. Instead, delete and re-add the action step (or the whole automation) on the live board to force monday to re-prompt for all fields.

## Environment variables

Only **secrets** belong in `.env` / monday Code env vars — never board/column IDs. Full table with descriptions: [README.md](README.md#environment-variables). Quick reference:

- `MONDAY_SIGNING_SECRET`, `OPENSANCTIONS_API_KEY` — required in production.
- `MONDAY_API_TOKEN` — local dev only, used when no `Authorization` header is present.
- `DATABASE_URL`, `SENTRY_DSN` — both optional; the app runs unchanged (metering/tracking disabled) when unset. Both are fail-open — a DB or Sentry outage never blocks a screening.

## Working in this repo

- **Dev loop:** `python src/main.py` (port 3000), `ngrok http 3000` to get an HTTPS URL for testing against a real recipe action.
- **Lint/format:** `ruff check .` and `ruff format --check .` (CI-enforced; `src/main.py` is intentionally exempt from E402 because `load_dotenv()` must run before other imports).
- **Tests:** `pytest -q` from repo root. The Postgres-backed integration test (`test_repository_integration.py`) needs `TEST_DATABASE_URL`; CI spins up a throwaway Postgres service for it, and it's skipped locally without that var (see the "skipped" count in test output — that's expected, not a failure).
- **Delivery is CI/CD, not manual.** `.github/workflows/deploy.yml` deploys automatically: every push/PR runs lint + tests; non-draft PRs from the same repo (and pushes to `main`) additionally push to monday's single shared **draft** version and run the smoke test — there is only one draft slot account-wide, so deploys are serialized via a global concurrency group (`deploy-monday`). **Don't manually run `mapps code:push`** as part of a normal change — merging (or pushing) is the deploy step; running it locally on top of CI risks racing/clobbering the shared draft slot. Reach for it manually only for first-time app setup (`mapps init`) or to debug the pipeline itself. Promoting draft → live customer-facing is a separate, manual step in Developer Center (not done by CI).
- **Secrets:** `.mappsrc` (monday CLI auth token) and `.env` are gitignored — never commit them. Setting env vars on the actual deployed app is `mapps code:env --mode set --key ... --value ...`, not editing `.env` (monday Code doesn't read local `.env`).
- **Multi-tenant discipline:** never hardcode a board ID, column ID, or account-specific value in `src/`. If a value should be per-customer, it comes from `inboundFieldValues` or is resolved via a GraphQL lookup keyed off something the trigger did supply (see the boardId fallback above as the pattern to follow).
