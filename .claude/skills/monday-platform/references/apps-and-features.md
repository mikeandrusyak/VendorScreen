# Apps & Feature Types

What a "monday app" actually is, what you can build, and where it shows up in the product. Read this when deciding what *kind* of app/feature fits a task, or when building a client-side UI extension (board view, item view, dashboard widget). For the integration/automation feature type specifically (trigger/action blocks), see [integrations-and-automations.md](integrations-and-automations.md) — it's covered here only at a high level.

## What is a monday app?

monday.com frames itself as a customizable "Work OS." An app is one or more **app features**, each built on the same technology-agnostic framework (any backend language/stack works — the framework doesn't care). Apps can be **private** (account-only), **public** (shared via link/OAuth outside the marketplace), or submitted to the **marketplace** for public discovery — see [publishing-and-compliance.md](publishing-and-compliance.md) for that last step.

## Feature types

| Scope | Feature type | Where it renders | Primary use case |
|---|---|---|---|
| Boards | **Board view** | Tab under the board title, added via the Views Center (`+`) | Visualize/manage data from one board |
| Boards | **Item view** | Updates section of an item, added via the Item View Center (`+`) | Same idea as board view, scoped to one item |
| Boards | **Board menu features** | Context menu on a group/item/multi-selection | Actions, not a persistent view |
| Boards | **Column view / board column extension** | Inside a specific column | Custom display/interaction for one column type |
| Dashboards | **Dashboard widget** | Under a dashboard, added via "Add widget (+)" | Multi-board analytics/visualization |
| Workspaces | **Custom objects** | Left-pane menu, standalone (not attached to any board/item/dashboard) | A dedicated space for a standalone tool |
| Workspaces | **Workspace templates** | Left-pane menu, like a board/doc/dashboard | An all-in-one bundle of pre-wired boards/docs/dashboards. **Can't be an app's only feature** — must ship alongside at least one other feature type |
| Settings & admin | **Account settings view** | Fixed admin surface, not addable to a board | Account-wide global settings |
| Settings & admin | **Administration view** | Workspace-level admin tooling | Paired with account settings |
| Docs | **Doc actions** | Inside monday WorkDocs | Document workflow plug-ins |
| Integrations | **monday workflows** (automation blocks) | Visual workflow builder | Triggers/actions — see [integrations-and-automations.md](integrations-and-automations.md) |
| AI | **AI assistant** | Varies — 6 documented sub-types depending on where it's invoked | Automate workflows/tasks with AI |
| AI | **Sidekick tool** | Invocable by monday's AI agents | An AI-agent-callable tool |

**Mobile:** as of current docs, only **Integrations, board views, and item views** are supported on the monday mobile app — a dashboard widget or custom object built today won't be usable from mobile.

## The Developer Center

Reached from the profile menu — the "one-stop shop" for app management:
- **Build** — create the app; under Features → Create feature, register each feature type and its settings.
- **OAuth & Permissions** — declare required API scopes.
- **Manage** — app versions (draft/live/deprecated) for controlled rollout.
- **Distribute** — install, share, or submit to the marketplace.
- **Analyze** — sales/installs/usage/reviews/listing metrics (see [publishing-and-compliance.md](publishing-and-compliance.md)).

Access is role-gated: admins/members can create apps, guests can only view collaborative apps, viewers get no access.

## The manifest (`manifest.json`)

A single file declaratively configuring an app's setup: metadata, OAuth scopes, feature definitions, and endpoints. Stated benefits over configuring everything by hand in the Developer Center UI: consistent config across environments/accounts, version control and rollback, and CI/CD pluggability for promoting an app between environments.

## Client-side SDK (`monday-sdk-js`)

The SDK for features that render *inside* monday's UI (board views, item views, dashboard widgets) — running in an iframe, talking to the parent monday.com window over a postMessage bridge that the SDK abstracts away so you never hand-roll cross-frame messaging.

```js
import mondaySdk from "monday-sdk-js";
const monday = mondaySdk();
monday.setApiVersion("2023-10");
```

Six core methods/namespaces:
| Method | Purpose |
|---|---|
| `monday.get` | Read contextual data from the parent monday app |
| `monday.listen` | Subscribe to client-side events (returns an unsubscribe function) |
| `monday.execute` | Trigger actions in the host monday UI |
| `monday.api` | Run GraphQL queries/mutations as the logged-in user |
| `monday.storage` | Key-value persistence for the app |
| `monday.set` | Set init/config data for the SDK instance |

Auth "works out of the box," scoped to the logged-in user and the app's granted OAuth scopes — you don't separately manage credentials for client-side calls.

Note: this SDK is for **client-side, in-iframe** apps. A server-side backend (like an integration/automation app) talks to the GraphQL API directly over HTTP — see [graphql-cookbook.md](graphql-cookbook.md) and [api-auth-and-limits.md](api-auth-and-limits.md) — it doesn't need `monday-sdk-js` at all.

## The marketplace listing surface (for context — full detail in publishing-and-compliance.md)

The marketplace (`monday.com/marketplace`) has an **Apps** tab and a newer **AI hub** tab (for AI agents/agent skills specifically). Discovery surfaces: Featured/Trending/Editor's choice/New tabs, a 13-category mega-menu, curated shelves, and free-text search. An app card shows name, a status badge (Best seller/New/Editor's choice/Trending), publisher, tagline, star rating + review count, and install count.

## Finding app ideas

`monday.com/appdeveloper/appideas` is mostly a conversion/FAQ page, not a literal idea list — its actual methodology (in `find-validate-monday-app-idea`) is: browse the marketplace for gaps, mine the Feature Requests board for upvoted asks, watch community channels (monday's Facebook groups, community forums, r/mondaydotcom), check G2/Capterra reviews for recurring complaints, and look at what's commonly connected via Zapier/Make.com but lacks a deep native integration. Real shipped examples span forms/PDF generation, cross-board sync, calendar integration, client onboarding, expense management, and first-party AI agents (morning briefs, meeting actions, lead processing).
