# Smoke testing the P1 features on monday

This runbook takes you from a fresh deploy to a green end-to-end check of the
three P1 features — **`/match` scoring**, the **audit log + CSV export**, and
**Critical-risk alerts** — against a real (draft) monday deployment, before you
promote it to customers.

The flow is: **set up a test board → adapt the app's feature/recipe in Developer
Center → push a draft → run `scripts/e2e_smoke.py` → promote if green.**

---

## 1. Test board

Create a throwaway board (e.g. "VendorScreen QA"). You need these columns; note
each column **id** (open the column menu → *Customize* → the id is in the URL, or
read it from the API). Column ids — not titles — are what you pass to the smoke
test and map in the recipe.

| Column | Type | Purpose | Required |
|---|---|---|---|
| *(item name)* | built-in | The **vendor name** that gets screened | yes |
| Risk | **Status** | Where the app writes `Clear` / `Warning` / `Critical` / `Screening Failed` | yes |
| Details | **Text** or **Long Text** | Where the app writes the match summary (score + profile link) | yes |
| Country | **Country** or **Text** | Optional refinement sent to `/match` to cut false positives | optional |

Notes:
- The status column's labels are **auto-created** by the app (`create_labels_if_missing`), so you don't have to pre-add `Clear` / `Warning` / `Critical` / `Screening Failed`.
- For a Critical result to smoke-test alerts, seed an item whose name is a well-known sanctioned entity (the script defaults to `Vladimir Putin`).

Quick way to list column ids via the API:
```bash
curl -s https://api.monday.com/v2 \
  -H "Authorization: $E2E_MONDAY_TOKEN" -H "API-Version: 2024-01" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { boards(ids: [YOUR_BOARD_ID]) { columns { id title type } } }"}' | jq
```

---

## 2. Adapt the app's feature in Developer Center

Two changes to the existing integration feature, plus one new action.

### 2a. Scopes
Add **`notifications:write`** to the app's scopes (you already have `boards:read`,
`boards:write`). Without it, `create_notification` fails and both the audit-export
notification and the Critical alert can't be delivered.

### 2b. Screening action — add the optional country field
On the existing **"Screen vendor & update status"** custom action, add one input
field so customers can map a country column:

- `countryColumnId` — type **Column** (country/text) picker, **optional**

The field name must be exactly `countryColumnId` (that's what `main.py` reads).
Leaving it unmapped keeps the prior name-only behavior.

### 2c. New action — "Export screening audit"
Add a second custom action so customers can pull their audit log:

- **Name:** `Export screening audit`
- **Input field:** `itemId` — type **Item** (used as the notification's anchor)
- **Action URL:** `https://<your-app>.monday.app/monday/export_action`
- Trigger it however suits you (a board **button** recipe is typical).

Running it DMs the user a 15-minute CSV download link for their account's audit
log.

### 2d. Environment variables (Monday Code)
Set these on the deployment (`mapps code:env --mode set --key ... --value ...`):

| Variable | Needed for | Notes |
|---|---|---|
| `MONDAY_SIGNING_SECRET` | everything | JWT + export-token signing |
| `OPENSANCTIONS_API_KEY` | screening | — |
| `APP_ENV=production` | auth | enforces JWT verification |
| `DATABASE_URL` | audit log + export + quotas | **without it the audit log is skipped** and the export CSV is header-only |
| `MATCH_SCHEMA` / `MATCH_SCORE_CRITICAL` / `MATCH_SCORE_WARNING` | tuning | optional; defaults `LegalEntity` (matches people **and** companies) / `0.85` / `0.70` |

---

## 3. Push a draft and run the smoke test

```bash
mapps code:push            # deploys a DRAFT on its own *.monday.app URL
```

Point the smoke test at that draft URL and your test board:

```bash
export APP_URL="https://<draft>.monday.app"
export MONDAY_SIGNING_SECRET="<same secret the deploy uses>"
export E2E_MONDAY_TOKEN="<monday API token with write access to the QA board>"
export E2E_BOARD_ID="<board id>"
export E2E_STATUS_COLUMN="<Risk column id>"
export E2E_DETAILS_COLUMN="<Details column id>"
# optional:
export E2E_COUNTRY_COLUMN="<Country column id>"   # exercises the /match country path
# export E2E_SKIP_EXPORT=1                         # skip export/notify stage temporarily

python scripts/e2e_smoke.py
```

### What each stage proves
| Stage | Proves |
|---|---|
| `health OK` | the draft booted with its secrets |
| `PASS screening` | `/match` ran and wrote a **score-based** result — details carry `% match` + an OpenSanctions profile link (PR1) |
| `PASS notification` | `POST /monday/export_action` returned 200, i.e. the real **`create_notification`** mutation + `notifications:write` scope work — the same call the Critical alert uses |
| `PASS audit export` | `GET /audit/export` streamed a CSV that contains the screening just run (PR2) |

A `502` at the notification stage is the script telling you `notifications:write`
isn't granted yet (or the mutation shape is off) — fix step 2a and re-run.

A `WARN audit export ... item absent` means the CSV was valid but empty of your
row — almost always `DATABASE_URL` isn't set on the deployment (step 2d).

### The one thing the script can't prove
The script **mints its own JWT**, so it can't confirm that monday's *real* action
request includes the `userId` claim (the Critical-alert recipient). Verify that
once with a live recipe run: create an item named for a sanctioned entity on a
board where the recipe is installed, then check the deployment logs for
`[alert] Critical alert sent to user ...`. If instead you see the screening
succeed with no alert line, monday isn't sending `userId` on that action and the
alert recipient needs to be sourced differently.

---

## 4. Promote

Once the smoke test is green (and you've eyeballed the live Critical alert once),
promote the draft to live in Developer Center / via `mapps`. Then update any
production recipe **Action URLs** to the promoted version if they changed.
