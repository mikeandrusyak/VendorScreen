# VendorScreen AI

Automated KYC/AML compliance checks for new vendors on Monday.com boards. When a new item is created, it queries the OpenSanctions API and updates the board with a risk level: **Clear**, **Warning**, or **Critical**.

## Local Development

### 1. Install dependencies
```bash
npm install
```

### 2. Configure environment variables
Copy the values from `.env`. Required variables:

| Variable | Description |
|---|---|
| `MONDAY_SIGNING_SECRET` | JWT verification for Integration App webhooks (from Developer Center → App credentials → signing_secret) |
| `MONDAY_API_TOKEN` | Personal API token for board updates |
| `OPENSANCTIONS_API_KEY` | OpenSanctions authentication |
| `MONDAY_BOARD_ID` | Target board ID |
| `COLUMN_ID_STATUS` | Status column ID (e.g. `status`) |
| `COLUMN_ID_DETAILS` | Details column ID |
| `NODE_ENV` | Set to `development` locally, `production` in deploy |

### 3. Start the server
```bash
npm start   # port 3000
```

### 4. Expose via ngrok for Monday webhook testing
```bash
ngrok http 3000
```
Use the generated HTTPS URL + `/webhook` in Monday.com → Integrations → Webhooks.

---

## Deployment (Monday Code)

### First-time setup
```bash
npm install -g @mondaycom/mapps-cli
mapps init   # requires App ID from Developer Center
```

### Set environment variables in Monday Code
Run once per variable — Monday Code does NOT read your local `.env`:
```bash
mapps code:env --mode set --key OPENSANCTIONS_API_KEY --value <value>
mapps code:env --mode set --key MONDAY_SIGNING_SECRET --value <value>
mapps code:env --mode set --key MONDAY_API_TOKEN --value <value>
mapps code:env --mode set --key MONDAY_BOARD_ID --value <value>
mapps code:env --mode set --key COLUMN_ID_STATUS --value <value>
mapps code:env --mode set --key COLUMN_ID_DETAILS --value <value>
mapps code:env --mode set --key NODE_ENV --value production
```

To view current variables:
```bash
mapps code:env --mode list
```

### Deploy
```bash
mapps code:push
```

After deploy, update the webhook URL in Developer Center → Features → your trigger to the new `*.monday.app` URL.

---

## Status Column Index Reference

Current board configuration for the `status` column:

| Index | Label | Color |
|---|---|---|
| `0` | Warning | Orange |
| `1` | Clear | Green |
| `2` | Critical | Red |
| `5` | Pending | Grey |

To update these indices, query the board:
```bash
curl -X POST https://api.monday.com/v2 \
  -H "Authorization: $MONDAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "{ boards(ids: YOUR_BOARD_ID) { columns(ids: [\"status\"]) { settings_str } } }"}'
```

---

## Architecture

```
Monday webhook → POST /webhook
  ├── Challenge handshake (app setup only)
  ├── Signature verification (production only)
  ├── 200 OK immediately returned
  └── async processVendor()
        ├── sanctionsService: GET /search/default?q=<name>
        │     └── maps result → Clear / Warning / Critical
        └── mondayService: GraphQL change_multiple_column_values
```
