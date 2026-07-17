# Integrations & Automations (Custom Triggers/Actions)

This is the core of any app shaped like VendorScreen: a backend that plugs into monday's visual automation builder as a **trigger** ("when X happens...") and/or an **action** ("...do Y"), which a customer assembles into an automation alongside monday's own native blocks. Read this when building or debugging a custom action/trigger endpoint, verifying that a request really came from monday, or figuring out what a customer's field mapping looks like at runtime.

## Two systems, one wire contract

monday is mid-migration between two systems. Every doc page you find will belong to one or the other:

| | Legacy | Current |
|---|---|---|
| Name | "Integration for sentence builder" | **monday workflows** |
| Builder UI | Sentence Builder / classic recipe center | Visual **workflow builder** |
| Unit | Recipe (built from a Recipe Sentence) | **Automation** (built from **Automation blocks**) |
| Status | Deprecated — every legacy page carries a deprecation banner | Actively developed; build new integrations here |

**The good news: the runtime payload shape is confirmed identical between the two systems** — field names like `payload.inboundFieldValues`, `payload.recipeId`, `payload.integrationId` match byte-for-byte across legacy and current reference pages. A handler written against the shapes below works regardless of which builder the customer used. monday's own migration guide states custom app blocks migrate without issues.

The migration has a formal wizard (Developer Center → Features → your integration feature → "Start migration") and monday is steering all new development toward the new system, but the docs are inconsistent/unclear about an exact legacy-shutdown date — don't hard-code a sunset date into anything; just build new work on the current Automation Block system.

## Defining a trigger or action

Both are the same underlying "Automation block" feature in the Developer Center (profile → **Developers** → your app → **Features** → **Create feature** → **Automation block**), distinguished by **Block type: Trigger** or **Action**.

Shared configuration:
- **Block name** (user-facing, shown in the workflow builder) and **Block description** (internal only)
- **Async actions** checkbox — opt into the callback pattern (see below) instead of the synchronous contract
- **Deployment** — which of your hosted endpoints backs this block
- **Credentials** — attach a Credentials feature if the block needs third-party auth
- **Input fields**: each has a field key (letters/digits/underscores, must start with a letter), a title shown in the builder, optional/list flags, and constraints wiring fields to each other. One input field must be marked the **main field** — "the most valuable field."
- **Output fields**: field key, title, list flag — what your block hands back to the automation.

Trigger-specific: a **Subscribe URL** and an **Unsubscribe URL**.
Action-specific: an **Execution (Run) URL**.

Field values are one of four scalar types (string, number, boolean, date) for simple fields, or an **object field** with a dynamic schema for structured data (see "Field and column mapping" below). There's no separate declared "board picker" or "column picker" type — those are implemented as ordinary dropdown-style fields backed by your own remote-options endpoint.

## Trigger runtime protocol: Subscribe → Invoke → Unsubscribe

**1. Subscribe** — monday calls your Subscribe URL once, when a customer adds your trigger to an automation:

```json
POST <your Subscribe URL>
Authorization: <JWT signed with your Signing Secret — see "Verifying requests" below>

{
  "payload": {
    "webhookUrl": "https://api-gw.monday.com/automations/apps-events/481709001",
    "subscriptionId": 481709001,
    "inputFields": { "text": "hello world" },
    "inboundFieldValues": { "text": "hello world" },
    "credentialsValues": { "credentials-key": { "userCredentialsId": 12345, "accessToken": "abcd1234" } },
    "recipeId": 629280,
    "integrationId": 398528596
  }
}
```

Respond `200` with `Content-Type: application/json`, optionally `{"webhookId": <your own id>}` — if you omit it, monday reuses `subscriptionId` to identify this subscription in future calls.

**2. Invoke** — when the real-world event happens, *you* are the caller: POST to the `webhookUrl` from the subscribe payload, with your own JWT (signed by you, containing your `appId`) as a Bearer token:

```json
POST <webhookUrl from subscribe payload>
Authorization: Bearer <JWT you signed with your Signing Secret>

{ "trigger": { "outputFields": { "text": "Hello?", "number": 9 } } }
```

**3. Unsubscribe** — monday calls this **only when the automation is deleted, not when it's merely turned off**:

```json
POST <your Unsubscribe URL>
{ "payload": { "webhookId": 481709001 } }
```
Respond `200`, no body required.

## Action runtime protocol

monday POSTs to your Run URL — this is the payload shape a VendorScreen-style action endpoint receives:

```json
POST <your Run/Execution URL>
Authorization: <JWT signed with your Signing Secret>

{
  "payload": {
    "blockKind": "action",
    "credentialsValues": { "credentials-key": { "userCredentialsId": 12345, "accessToken": "abc1234" } },
    "inboundFieldValues": { "number": "12" },
    "inputFields": { "number": "12" },
    "recipeId": 30440660,
    "integrationId": 398759485
  },
  "runtimeMetadata": {
    "actionUuid": "1f6129e75d7b78b77179061ab026bbab",
    "triggerUuid": "b644926b34fbabf3547bd1bc38ad6cdb"
  }
}
```

Notes:
- `inboundFieldValues` and `inputFields` carry identical data in every documented example — read from either, but treat them as the same thing rather than expecting a meaningful diff.
- **There's no reserved `boardId`/`itemId` key.** Whatever the customer mapped shows up under whatever field key *you* defined for that input field — `boardId`/`itemId` are conventions you invent, not platform reservations. (Confirmed real example from monday's own docs: `"inboundFieldValues": { "boardId": 541329092, ... }` — that key exists only because the app author named their field `boardId`.)
- `runtimeMetadata.actionUuid` is a good idempotency/dedup key if a retry could otherwise double-process the same event.
- `credentialsValues` carries any third-party auth the customer configured via a Credentials-type field, keyed by the field key you defined for it.

Expected response:
```json
HTTP/1.1 200 OK
Content-Type: application/json

{ "outputFields": { "recipientAddress": "someone@example.com", "success": true } }
```

**Timing contract:** if you respond with anything other than 200, or don't respond within about a minute, monday retries the request for 30 minutes. That one-minute window is the synchronous budget — plenty for a fast column write, tight for an outbound call to a third-party screening/enrichment API.

## Async actions: ack fast, finish later

A distinct, opt-in feature (the **"Async actions" checkbox** in the block's Developer Center config), built specifically for work that might not finish inside that one-minute window — monday's own docs describe the problem this solves as developers faking immediate "success" to dodge timeouts, which hides real outcomes and causes duplicate retries. This is the right fit for anything that calls a slow third-party API (an OpenSanctions-style screening call, for example) rather than trying to squeeze it under the synchronous window.

The inbound payload gains a `callbackUrl`:
```json
"payload": {
  "blockKind": "action",
  "inboundFieldValues": { "...": "..." },
  "inputFields": { "...": "..." },
  "recipeId": 123456,
  "integrationId": 123456,
  "callbackUrl": "https://callback-to-monday.com/callbacks/12345678"
}
```

**Step 1 — acknowledge immediately:**
```json
HTTP/1.1 200 OK
{ "status": "received", "message": "Action has been triggered.", "actionUuid": "a6676dzce11zd50b25c4871417e1zez1" }
```

**Step 2 — when the real work finishes, call back:**
```json
POST https://callback-to-monday.com/callbacks/12345678
Authorization: <JWT you sign with your Signing Secret, containing your appId>

// success
{ "success": true, "outputFields": {} }

// failure
{
  "success": false,
  "severityCode": 4000,
  "runtimeErrorDescription": "...",
  "notificationErrorTitle": "...",
  "notificationErrorDescription": "..."
}
```

## Verifying requests: which secret, which claims

Every action/trigger request carries a JWT in the `Authorization` header (HTTP headers are case-insensitive, so `authorization` works too). Claims:

```
accountId          — account initiating the request
userId             — user who triggered it
aud                — expected audience; docs say "your app's endpoint URL" without
                      spelling out whether that's the specific Run/Subscribe URL or
                      your app's base URL — don't assume strict aud checking without testing
exp / iat          — expiry / issued-at
shortLivedToken    — usable directly as an API credential for 5 minutes, scoped to
                      whatever accountId/userId can already do
```

Verify by: (1) checking the signature against the correct secret, (2) confirming `aud` matches your endpoint, (3) confirming `exp` hasn't passed. Use [../scripts/verify_monday_request.py](../scripts/verify_monday_request.py) rather than hand-rolling this.

**Confirmed gotcha (from a live production handler, not just docs):** the header arrives as `Authorization: Bearer <jwt>` — strip the `Bearer ` prefix before decoding. And the token's `aud` claim will make **PyJWT reject an otherwise-valid token by default** — Node's `jsonwebtoken` library ignores `aud` unless you ask it to check, but PyJWT enforces it unless you explicitly pass `options={"verify_aud": False}` (or supply a correct `audience=`). This produces a confusing failure mode: signature and secret are both right, but verification still fails, which looks identical to "wrong secret" from the outside. If a Python action handler is silently rejecting every request, check this before re-checking the secret.

**The one distinction that actually matters and is easy to get backwards:** monday signs different request categories with different secrets, and picking the wrong one fails verification *silently* (the JWT just won't validate — it looks like "monday isn't sending a real token," not "you're checking against the wrong secret"):

| Request category | Signed with |
|---|---|
| Custom action/trigger invocations (this page) | **Signing Secret** |
| Board-level webhooks via the `create_webhook` GraphQL mutation | **Signing Secret** |
| App lifecycle events (install/uninstall) and Monetization subscription webhooks | **Client Secret** |

Both secrets live in **Developer Center → your app → General settings → App credentials**.

`shortLivedToken` has no separate "exchange" step — it's directly usable as an API bearer credential for its 5-minute window. It's not suitable for the async-callback pattern above if the real work runs past 5 minutes; use an OAuth token (stored via the Credentials feature) for anything that needs to call the API outside that window.

## The `{"challenge": ...}` pattern — and where it does *not* apply

This is worth being precise about, because it's easy to conflate with the action/trigger contract above. The bare challenge-echo handshake:
```json
{ "challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P" }
```
— echoed back verbatim with HTTP 200 — belongs to the **raw Platform API's `create_webhook` GraphQL mutation** (registering a board-event webhook directly via the API, independent of the visual automation builder entirely; see the GraphQL cookbook). It is *not* documented as part of the custom action/trigger Run URL or Subscribe URL contract — those use the Subscribe/Invoke/Unsubscribe or Run-URL protocols above, not a challenge echo.

Practical takeaway: handling a bare `{"challenge": "..."}` POST defensively on your action endpoint is cheap and harmless (echo it back, then fall through to normal payload parsing if it's not present), but don't treat it as your endpoint's actual security check — that's the JWT verification above, not the challenge.

## Field and column mapping: how a customer's picks reach your payload

There's no fixed "board picker"/"column picker" type. Two mechanisms implement it:

**Remote-options fields** — you host an endpoint monday calls to populate a dropdown, optionally receiving `dependencyData` so one field's options depend on another's already-picked value (e.g. a column dropdown scoped to the board already chosen):
```json
// monday calls your remote-options endpoint with:
{ "payload": { "boardId": 12345678, "automationId": 123456, "dependencyData": {}, "recipeId": 123456, "integrationId": 123456 } }
// you respond:
{ "options": [{ "title": "Display name", "value": "option_key" }], "isPaginated": true, "nextPageRequestData": { "page": 2 }, "isLastPage": false }
```

**Object fields** — for mapping a structured set of values (not just one ID), backed by a schema endpoint you host:
```json
{
  "fieldKey": { "title": "Display label", "type": "primitive", "primitiveType": "string", "fieldTypeKey": "custom-key", "isNullable": false, "isOptional": false, "isArray": false }
}
```
(Keep `isOptional` equal to `isNullable` — the docs call this out explicitly.)

Either way, whatever the customer picked lands as an ordinary key in `payload.inboundFieldValues` at runtime, under the field key you defined — there's no separate "column mapping" envelope.

## Manual / on-demand actions

Thinly documented. What's confirmed: integrations don't have to run automatically — a customer can trigger one by clicking a monday-native **Button column**, which functions as the trigger upstream of your action. The action Run URL contract itself doesn't appear to differ based on what triggered it; the difference is only which trigger block (automatic event vs. button click) feeds into your action in the customer's automation. If you need a second, on-demand action (e.g. "export my audit log now" alongside an automatic "screen new vendors" action), register it as an ordinary action block and let the customer wire it to a manual trigger — this is a reasonable working pattern, not something spelled out verbatim in monday's docs, so validate against monday's own example integrations if it matters for your case.

## Recipe/workflow terminology cheat sheet

```
Legacy                              → Current
-----------------------------------   ------------------------------------
Integration for sentence builder    → monday workflows infrastructure
Field Types (custom field feature)  → Field for automation block / Credentials feature
Block (trigger/action)              → Automation block
Recipe / Recipe Template            → Automation / Automation template
Authorization URL                   → Credentials feature (OAuth, API keys, custom auth)
```
A **condition** block type also exists in the visual workflow builder (trigger → condition → action chains) — no dedicated custom-condition-block doc was found, so it's unclear whether third-party apps can define custom conditions or if that's monday-native-only.
