# VendorScreen AI

Automated KYC/AML compliance screening for vendors on Monday.com boards. It ships as a Monday.com **integration recipe**: when a new item is created, the app queries the OpenSanctions API and writes a risk level — **Clear**, **Warning**, or **Critical** — plus supporting notes back to the columns the customer chose.

> ⚠️ **Informational tool only.** VendorScreen AI provides indicative screening signals and **does not make compliance decisions**. Results may be incomplete or inaccurate and must be independently verified. Responsibility for regulatory compliance rests with the customer. See [Terms of Service](./TERMS_OF_SERVICE.md) and [Privacy Policy](./PRIVACY_POLICY.md).

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
        └── async processVendor()
              ├── mondayService.getItemName(itemId)   → vendor name
              ├── sanctionsService: GET /search/default?q=<name>
              │     └── maps result → Clear / Warning / Critical
              └── mondayService.updateVendorRecord()
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
| `NODE_ENV` | Monday Code + local | `production` in deploy, `development` locally |
| `MONDAY_API_TOKEN` | **local dev only** | Personal API token used only when no `Authorization` header is present (dev mode). Not needed in production — the token comes from the JWT. |
| `MONTHLY_CHECK_LIMIT` | Monday Code + local | Soft per-account cap on paid OpenSanctions checks per calendar month. Defaults to `100`. Once hit, the board is updated with a `Limit Reached` status instead of calling OpenSanctions. Not yet tied to Monday's native billing tiers — see TECH_SPEC.md. |
| `PORT` | auto | Provided by Monday Code; defaults to `3000` locally |

> The old single-tenant variables (`MONDAY_BOARD_ID`, `COLUMN_ID_STATUS`, `COLUMN_ID_DETAILS`, `COLUMN_ID_COUNTRY`) are **no longer used** — the app reads these from the recipe input fields.

---

## Local development

### 1. Install dependencies
```bash
npm install
```

### 2. Configure environment variables
Set the variables above in a local `.env`.

### 3. Start the server
```bash
npm start   # port 3000
```
Health check: `GET /` returns `{ "status": "ok" }`.

### 4. Expose via ngrok for recipe testing
```bash
ngrok http 3000
```
Use the generated HTTPS URL + `/monday/execute_action` as the action URL while testing the recipe in Developer Center. In dev mode (`NODE_ENV=development`) requests without an `Authorization` header fall back to `MONDAY_API_TOKEN`.

---

## Recipe configuration (Developer Center)

The code does nothing until the integration recipe is configured. Field names below **must match** those read in `index.js` (`boardId`, `itemId`, `statusColumnId`, `detailsColumnId`).

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
mapps code:env --mode set --key NODE_ENV --value production
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

After deploy, set the recipe **Action URL** in Developer Center to the new `*.monday.app` URL + `/monday/execute_action`.

Re-run `mapps code:push` whenever you change the code — each push creates a new version that must go through review before customers receive it.
