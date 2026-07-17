# API Auth, Rate Limits, Versioning, Pagination, Errors

Cross-cutting mechanics every GraphQL call needs to get right. For object-specific queries/mutations (boards, items, columns, ...), see [graphql-cookbook.md](graphql-cookbook.md). For the JWT that arrives on *inbound* action/trigger/webhook requests (a different thing from the outbound auth described here), see [integrations-and-automations.md](integrations-and-automations.md).

## Contents
- [Authentication](#authentication)
- [Rate limiting / complexity budget](#rate-limiting--complexity-budget)
- [API versioning](#api-versioning)
- [Pagination](#pagination)
- [Errors](#errors)

## Authentication

All three credential types go in the same header:
```
Authorization: <token>
```

### Personal API token
Tied to one human user, mirroring their exact UI permissions — no separate scoping. Retrieved via avatar → **Developers** → **API token**. Regenerating it immediately invalidates the old one. **Apps that rely on a personal token aren't eligible for marketplace approval** — treat it as an internal/dev-only credential, not something a shipped multi-tenant app depends on.

```bash
curl -X POST https://api.monday.com/v2 \
  -H "Authorization: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"query": "query { me { id name } }"}'
```

### OAuth2 access token (app-level)
The production path for a marketplace/multi-tenant app acting on a customer's account.

```
GET https://auth.monday.com/oauth2/authorize?client_id=1111&scope=boards:write%20boards:read&state=...
```
monday redirects back with `code` (valid **10 minutes**) and `state`. Exchange:
```
POST https://auth.monday.com/oauth2/token
Content-Type: application/json

{"client_id": "...", "client_secret": "...", "redirect_uri": "...", "code": "..."}
```
Response:
```json
{ "access_token": "NgeFeX...FEmEka", "token_type": "Bearer", "scope": "boards:write boards:read" }
```
**Access tokens don't expire** — valid until the app is uninstalled. Common scopes: `boards:read`/`write`, `items` (covered under boards), `docs:read`/`write`, `me:read`, `notifications:write`, `updates:read`/`write`, `users:read`/`write`, `webhooks:read`/`write`, `workspaces:read`/`write`, `account:read`, `assets:read`, `teams:read`/`write`. Request only what you need — the marketplace security review checks for scope minimization.

*Unconfirmed:* whether OAuth tokens need a literal `Bearer ` prefix on the `Authorization` header — the token-exchange response's `token_type: "Bearer"` implies it, but the one personal-token cURL example in the docs sends the raw token with no prefix, and nothing states the rule for both cases explicitly. Verify empirically if it matters for your client. (This is genuinely a *different* token from the short-lived action/trigger JWT below — that one *is* confirmed to arrive `Bearer`-prefixed, from a live production handler; don't assume the same is true for OAuth tokens without checking.)

### Short-lived JWT (`shortLivedToken`)
For apps running *inside* a monday session (iframes, or the JWT that arrives on action/trigger invocations — see the integrations reference), monday hands you a token usable as an API credential for **5 minutes**, scoped to whatever the acting user/account can already do. Not a fit for background jobs outside that window — use OAuth instead.

### Which one to use
Personal token → internal tooling only. OAuth → production apps, background/async work, anything outside an active monday UI session. Short-lived JWT → in-session work triggered by monday itself (an action/trigger handler that finishes fast). A backend service reacting to automation events outside any live monday session — the VendorScreen shape — wants OAuth for production, though a personal token is common enough during early development.

## Rate limiting / complexity budget

monday enforces **five separate, simultaneous** limit types. As of a Dec 2024 change, all of them return **HTTP 429** with a `Retry-After` header when exceeded.

### Complexity budget (the main one — not a simple request count)
Every query has a "cost." Check it via the `complexity` field, includable alongside any query:
```graphql
query {
  complexity { before query after reset_in_x_seconds }
  boards(ids: 1234567890) { items { id name } }
}
```
| Token / usage | Budget |
|---|---|
| Single query, hard ceiling | 5,000,000 points |
| Personal token (read+write combined) | 10,000,000 points/minute |
| Personal token — trial/NGO/free plans | 1,000,000 points/minute |
| App (OAuth) token — read **and** write, each | 5,000,000 points/minute |
| API Playground — standard accounts | 5,000,000 points/minute |
| API Playground — trial/free accounts | 1,000,000 points/minute |

Budgets are sliding windows resetting 60 seconds after the first call in the window.

### Simple per-minute request-count limit (separate from complexity)
| Plan tier | Requests/minute |
|---|---|
| Enterprise | 5,000 |
| Pro | 2,500 |
| Free/Standard/Basic | 1,000 |
Error text: `"Minute limit rate exceeded"`.

### Concurrency limit (simultaneous in-flight requests)
| Plan tier | Max concurrent |
|---|---|
| Enterprise | 250 |
| Pro | 100 |
| Free/Standard/Basic | 40 |
Error code: `maxConcurrencyExceeded`.

### Daily call limit (resets midnight UTC)
| Plan tier | Daily limit |
|---|---|
| Free/Standard/Basic | 1,000 |
| Pro | 10,000 (soft) |
| Enterprise | 25,000 (soft) |
Error code: `DAILY_LIMIT_EXCEEDED`. Rolled out gradually through 2025, Basic/Standard first. Rate-limit-error responses and complexity-only queries each count as only 0.1 calls toward this; a high-complexity query can count as 1+.

### IP-level limit
5,000 requests / 10 seconds / IP. Error: `IP_RATE_LIMIT_EXCEEDED`.

### Response headers (draft IETF RateLimit-header style)
```
RateLimit-Policy: "minuteRate";q=100;w=60, "concurrency";q=40;qu="concurrent-requests"
RateLimit: "minuteRate";r=90, "concurrency";r=35, "complexityMinute";r=800000;t=45
Retry-After: <seconds>
```
`RateLimit-Policy` is static per account tier; `RateLimit` reports live remaining quota (`r`) and seconds-to-reset (`t`).

Note: there's a *different* set of limits — CPU/RAM/execution-minutes — that applies to code hosted on monday Code itself, unrelated to these API-call limits. See [monday-code-hosting.md](monday-code-hosting.md); don't conflate the two.

### Best practices (from monday's own optimization guide)
- Request only the fields you need; use fragments for column-specific data.
- Paginate (`items_page`/`limit`) instead of pulling everything at once.
- Batch writes with `change_multiple_column_values` instead of many single-column calls.
- Check `complexity` before/after when a query is expensive.
- Respect `Retry-After`; don't retry immediately. Use idempotency for retried mutations.
- Prefer webhooks over polling where possible.
- **All requests count toward these limits even when they fail or error** — a tight retry loop on a failing call burns budget just as fast as a successful one.

## API versioning

Header: `API-Version`, format `YYYY-MM` (e.g. `2026-07`). Omit it and you get the **Current** default, which changes every quarter — pin a version in production so a quarterly rollover doesn't silently change your query's behavior.

```javascript
fetch("https://api.monday.com/v2", { headers: { "API-Version": "2026-07" } });
```

Lifecycle per version, each stage ~3 months: `Release Candidate → Current → Maintenance → Deprecated`. Deprecated versions get **at least 6 months' notice** before removal, and traffic to a deprecated version auto-routes to Maintenance — so any pinned version is guaranteed stable for at least 6 months from when it becomes Current.

```graphql
query { version { kind value display_name } }
query { versions { kind value display_name } }
```
`kind` enum: `current`, `deprecated`, `maintenance`, `previous_maintenance`, `release_candidate`. Both are root-level only.

## Pagination

See the [cookbook's Items section](graphql-cookbook.md#items) for the full `items_page`/`next_items_page` example. Key facts: `limit` defaults to 25, maxes at 500 for `items_page`; `cursor` and `query_params` are mutually exclusive; a `null` cursor means no more pages; **cursors expire 60 minutes** after the first call in the sequence, so a long export needs to finish (or restart) within that window.

## Errors

monday uses **two error channels** — a correct client checks both:

1. **Application-level**: HTTP `200 OK`, problems reported in a top-level `errors[]` array (standard GraphQL behavior) — the request succeeded but part of what you asked for failed.
2. **Transport-level**: genuine `4xx`/`5xx` HTTP status — the request itself was rejected (auth, rate limit, malformed body).

### Shape (200 OK, `errors[]` populated)
```json
{
  "data": [],
  "errors": [
    {
      "message": "User unauthorized to perform action",
      "locations": [{ "line": 2, "column": 3 }],
      "path": ["me"],
      "extensions": { "code": "UserUnauthorizedException", "error_data": {}, "status_code": 403 }
    }
  ],
  "account_id": 123456
}
```
Partial success is normal — some fields resolve, one fails:
```json
{
  "data": { "me": { "id": "4012689", "photo_thumb": null }, "complexity": { "query": 12 } },
  "errors": [{ "message": "Photo unavailable.", "path": ["me", "photo_thumb"], "extensions": { "code": "ASSET_UNAVAILABLE" } }],
  "account_id": 18888528
}
```
`extensions.request_id` is worth logging — useful for support escalation.

### Common codes
**Inside `errors[]` at HTTP 200:** `API_TEMPORARILY_BLOCKED`, `ColumnValueException` (malformed/unsupported column value), `CorrectedValueException`, `CreateBoardException`, `InvalidArgumentException`, `InvalidBoardIdException`/`InvalidColumnIdException`/`InvalidUserIdException`, `InvalidVersionException`, `ItemNameTooLongException` (>255 chars), `ItemsLimitationException` (board over 10,000 items), `missingRequiredPermissions` (action exceeds granted OAuth scope), `ResourceNotFoundException`.

**Non-200 HTTP status:** `400` malformed JSON/request body, `401` missing/invalid token or IP-restricted, `403` `UserUnauthorizedException`/`USER_ACCESS_DENIED`, `404` resource not found, `409` `DeleteLastGroupException`/`IDEMPOTENCY_CONFLICT`, `422` `RecordInvalidException` (subscriber/board limits), `423` resource locked (concurrent update in progress), `429` any of `maxConcurrencyExceeded`/`COMPLEXITY_BUDGET_EXHAUSTED`/`IP_RATE_LIMIT_EXCEEDED`/`FIELD_LIMIT_EXCEEDED`, `500` bad arguments or malformed JSON column values.

**Naming caveat:** the complexity-exhausted error's exact code has changed across API versions/vintages (`ComplexityException` in some places, `COMPLEXITY_BUDGET_EXHAUSTED` in current docs; a documented reversion happened in May 2024). Match defensively — check `extensions.code` *and* whether `message` contains `"complexity"` rather than trusting one canonical string. `argumentLimitExceeded` is not a documented code anywhere in current docs — don't rely on it existing.

### Handling
- Always parse `errors[]` even on a 200 response — a 200 does not mean your mutation succeeded.
- Branch on `extensions.code` (or the flatter top-level `error_code` some 429 responses use) rather than matching on `message` text, which isn't a stable contract.
- Back off using `Retry-After`/`retry_in_seconds` on any 429; don't use a fixed interval.
- `401`/`403` are not retryable — surface them, don't loop.
- `5xx` can be transient — retry with backoff.
