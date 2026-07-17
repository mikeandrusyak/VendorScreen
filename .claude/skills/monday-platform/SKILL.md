---
name: monday-platform
description: Reference for building on monday.com's developer platform — the GraphQL API (boards, items, columns, column values, webhooks, rate limits/complexity), custom integration apps (automation triggers/actions, JWT-verified action endpoints, recipe/workflow field mapping), monday Code hosting and deployment, built-in Monetization, and marketplace submission/compliance. Use this whenever the task touches monday.com, even if not named explicitly — a monday board/item/column, the monday GraphQL API or API token, a monday webhook, an automation/recipe/workflow trigger or action, monday Code, the `mapps` CLI, `.mappsrc`, an app manifest, the Developer Center, monday Monetization or a subscription webhook, or submitting to the monday marketplace. Also use it to debug monday API errors — rate limit / complexity budget errors, column value JSON shape mismatches, or JWT / signing-secret verification failures.
---

# monday.com Platform

monday.com's data model is simple at the core — **boards** contain **items** (rows) organized into **groups**, and each item has a **column value** per board **column** — but the platform around it is large: a GraphQL API, a client-side app SDK, a visual automation/workflow builder that calls out to your backend, managed hosting ("monday Code"), built-in billing, and a marketplace with its own submission process. This skill indexes all of it.

Two shapes of project use this skill:

1. **A backend that talks *to* monday's API** — an automation/integration app (a "recipe" trigger or action, like this repo), a webhook consumer, or any server-side script that reads/writes boards. This is the common case here.
2. **A client-side UI extension that runs *inside* monday** — a board view, item view, or dashboard widget, rendered in an iframe via `monday-sdk-js`.

If you're not sure which reference file you need, start with the table below. Each reference file is self-contained — read only the one(s) relevant to the task at hand, not all of them.

## Decision tree

| You're trying to... | Read |
|---|---|
| Query or write a board, item, column value, group, update, user, or notification via GraphQL | [references/graphql-cookbook.md](references/graphql-cookbook.md) |
| Handle auth (token vs OAuth vs JWT), rate limits/complexity, pagination, or a GraphQL error | [references/api-auth-and-limits.md](references/api-auth-and-limits.md) |
| Build or debug a custom automation trigger/action ("recipe"), verify a request is really from monday, or handle field mapping | [references/integrations-and-automations.md](references/integrations-and-automations.md) |
| Deploy or host a backend on monday Code; manage env vars/secrets/storage/logs | [references/monday-code-hosting.md](references/monday-code-hosting.md) |
| Set up pricing plans, read a customer's subscription, or debug the Monetization webhook | [references/monetization.md](references/monetization.md) |
| Submit an app to the marketplace, meet listing/security requirements, read analytics | [references/publishing-and-compliance.md](references/publishing-and-compliance.md) |
| Build a board view / item view / dashboard widget / other in-product UI feature, or understand app types generally | [references/apps-and-features.md](references/apps-and-features.md) |
| Get oriented on monday.com's products, or find the right place to search for help | [references/platform-overview.md](references/platform-overview.md) |

## The shape of an automation/integration app (the common case)

If the task is "call our backend when something happens on a board" or "add an action a customer can wire into their automations" — the pattern this repo already uses — the runtime contract is:

1. A customer builds an automation in monday's visual **workflow builder** and adds your app's trigger and/or action block to it, mapping board columns to the input fields you defined in the Developer Center.
2. monday **POSTs to your action's Run URL** (or a trigger's Subscribe URL) with a JSON body shaped like:
   ```json
   { "payload": { "inboundFieldValues": { "...": "..." }, "recipeId": 123, "integrationId": 456 } }
   ```
   `inboundFieldValues` holds whatever the customer mapped — there's no reserved `boardId`/`itemId` key; those are just conventions you invent when you define your input fields.
3. Your endpoint **verifies the `Authorization` header JWT against your app's Signing Secret**, returns `200` within about a minute, and does the real work — synchronously if it's fast, or via the async-action callback pattern if it might run long (both are documented in the integrations reference).
4. You call back into the GraphQL API (item name, column values, notifications) using the short-lived token from the JWT, an OAuth token, or a personal token, depending on context.

Full detail, including the exact JWT claims, the *two different secrets* used for different request types, and the async callback shape: [references/integrations-and-automations.md](references/integrations-and-automations.md).

## Core vocabulary

| Term | Meaning |
|---|---|
| **Board** | Top-level container; defines columns, holds groups and items |
| **Item** | A row on a board; has a `name` and one `column_values` entry per column |
| **Column** | A field definition on a board (`type` = status, text, date, people, ...); each type has its own JSON shape for reading/writing values — see the cookbook |
| **Group** | A labeled section of items within a board (e.g. "This Week") |
| **Update** | A comment/activity post on an item — not a column value |
| **Workspace** | A folder-like container that groups related boards |
| **Recipe / Automation** | A user-built "when X, do Y" rule in the workflow builder, assembled from **trigger** and **action** blocks — yours or monday's native ones |
| **App feature** | One building block of an app (board view, item view, dashboard widget, integration/automation block, AI assistant, ...) — an app is one or more features |

## Gotchas worth knowing before you start

These cost real debugging time and aren't obvious from the API shape alone:

- **Column value JSON is inconsistent across types**, on purpose (it mirrors each column's own quirks) — `checkbox` writes take the *string* `"true"`, not a boolean; `people` writes use camelCase `personsAndTeams` even though the read field is snake_case `persons_and_teams`; `link`'s write key is `text` but the read field is `url_text`. Use [scripts/column_values.py](scripts/column_values.py) instead of hand-rolling this JSON — see the cookbook for the full table.
- **Two unrelated secrets sign two unrelated kinds of requests.** Custom action/trigger invocations and board-level webhooks (via `create_webhook`) are signed with your app's **Signing Secret**; app install/uninstall and Monetization subscription webhooks are signed with your app's **Client Secret**. Verifying with the wrong one just fails silently — see [scripts/verify_monday_request.py](scripts/verify_monday_request.py) and the integrations reference.
- **The `{"challenge": "..."}` echo-back pattern belongs to the raw `create_webhook` GraphQL API**, not to a custom action/trigger's Run URL. It's cheap and harmless to handle defensively on any endpoint, but don't rely on it as your action endpoint's actual authenticity check — that's the JWT, not the challenge.
- **monday is mid-migration** from a legacy "Sentence Builder" recipe system to a new **"monday workflows"** visual builder. Build new integrations against the new Automation Block system; the request/response payload shape is confirmed identical between old and new, so existing handlers keep working either way.
- **Complexity budget and simple rate limits are separate, simultaneous limits** — a query can be under the per-minute request-count limit and still get rejected for exhausting the complexity budget, or vice versa. Query the `complexity` field when in doubt; see [references/api-auth-and-limits.md](references/api-auth-and-limits.md).
- **Monetization plan IDs are developer-chosen strings, not monday-generated ones**, and they're case-sensitive. Whatever `id` you type into a plan in the Developer Center is exactly the `plan_id` your webhook and GraphQL queries will receive — a typo or case mismatch fails silently as "unrecognized plan," not as an error.

## Scripts

- [scripts/column_values.py](scripts/column_values.py) — builds correctly-shaped `column_values` JSON for the common column types (status, text, long_text, dropdown, date, people, numbers, checkbox, link), so you don't have to memorize the per-type asymmetries above.
- [scripts/verify_monday_request.py](scripts/verify_monday_request.py) — verifies the `Authorization` JWT on inbound requests, with separate functions for Signing-Secret requests (actions/triggers/webhooks) and Client-Secret requests (lifecycle/monetization webhooks), so the two never get mixed up.

Both are Python (matching this repo's stack); the JSON shapes and JWT claims they encode apply regardless of what language actually calls the API.

## A note on freshness

This skill was compiled from monday's live developer docs as of **July 2026** by deep research across the developer docs, GitHub quickstarts/SDKs, and community threads. monday's platform moves fast (see the workflows migration above) and a few facts in the references are flagged as *unconfirmed* or *inferred* where the docs were genuinely ambiguous or self-contradictory (this happens more than you'd expect — e.g. monday's own listing-guidelines page and review-checklist page disagree on the long-description character limit). Where a reference file flags something as unconfirmed, and the task is security- or money-critical (auth, webhook verification, billing), spot-check the live page at `developer.monday.com` or search `developer-community.monday.com` rather than trusting the note as permanently accurate.
