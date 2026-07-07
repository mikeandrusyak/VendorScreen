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
        ├── reads payload.inboundFieldValues → boardId, itemId, statusColumnId, detailsColumnId
        │     (chosen by the CUSTOMER in the automation UI — NOT from .env)
        ├── 200 OK returned immediately
        └── async process_vendor()
              ├── monday_service.get_item_name(item_id)   → vendor name
              ├── sanctions_service: GET /search/default?q=<name>
              │     ├── maps result → Clear / Warning / Critical
              │     └── retries 429/5xx/network; if still down → Screening Failed
              └── monday_service.update_vendor_record()
                    └── writes status by LABEL (create_labels_if_missing) + details text
```

Because the status is written **by label** (not by a hard-coded index) and missing labels are auto-created, the app works on any customer board regardless of column order or naming.

---

## Environment variables

Only **secrets** live in the environment. Board and column IDs come from the recipe payload, not from here.

| Variable | Where | Description |
|---|---|---|
| `MONDAY_SIGNING_SECRET` | Monday Code + local | JWT verification for recipe action requests (Developer Center → App credentials → signing_secret) |
| `OPENSANCTIONS_API_KEY` | Monday Code + local | OpenSanctions authentication |
| `APP_ENV` | Monday Code + local | `production` in deploy, `development` locally (`NODE_ENV` is still honored for backwards compatibility) |
| `MONDAY_API_TOKEN` | **local dev only** | Personal API token used only when no `Authorization` header is present (dev mode). Not needed in production — the token comes from the JWT. |
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
4. **Action URL:** `https://<your-app>.monday.app/monday/execute_action`
5. **Recipe sentence:** *"When an item is created, screen the vendor and set {statusColumn} with details in {detailsColumn}"* — this is where the customer maps their own columns.
6. **Scopes:** `boards:read`, `boards:write`.

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
