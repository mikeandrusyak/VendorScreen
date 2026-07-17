# Platform Overview: Products, Support, Community

Business/product context — read this to get oriented on what monday.com is and where to point someone for non-technical help. For the developer platform itself, start at the [main SKILL.md](../SKILL.md) decision tree instead.

## What monday.com is

A horizontal "Work OS" built around one core data model — boards, items, columns, groups — that gets re-skinned into several purpose-built products. monday.com markets itself as credible across virtually any industry (case studies span retail, healthcare, financial services, government, and non-profits), not a niche vertical tool.

## Product suite

| Product | What it's for | Notes for API/app work |
|---|---|---|
| **monday Work Management** | General-purpose project/task tracking across any team (PMO, marketing, ops, sales, IT, HR, product) | The default/foundational product; most boards you'll interact with via the API live here |
| **monday CRM** | Sales pipeline and customer relationship management | Same underlying board/item/column engine, themed: boards = pipelines, items = deals/leads/contacts, columns = stage/activity fields |
| **monday dev** | Software development: roadmaps, sprints, backlogs, releases | Adds Scrum/Kanban boards, GitHub/GitLab/CI integrations |
| **monday Service** | AI-first IT/customer service management (ticketing, triage, SLAs) | Positioned around AI agents handling requests, with escalation to humans |

All four sit on the same GraphQL API and app framework described in the other reference files — a "CRM deal" and a "Work Management task" are both just items on a board underneath.

## Where to point people for non-technical help

- **`support.monday.com`** — the official help center: account/billing, board basics, automations/integrations at a user level, troubleshooting/bug reports. This is end-user and admin support, not developer docs — if someone lands here looking for API help, redirect them to `developer.monday.com` instead.
- **`community.monday.com`** — the general user community (workflow sharing, feature requests, product announcements, partner referrals).
- **`developer-community.monday.com`** — a **separate, developer-focused** community forum, and the more useful one for anything API/app-related: its "API & Apps framework" section is by far the largest category, covering GraphQL, monday Code, auth, and integration troubleshooting. Worth searching here for real-world gotchas that don't make it into formal docs (e.g. edge cases in webhook auth, community-sourced clarifications on ambiguous doc pages).
- **`monday.com/customers`** — case studies, useful for understanding how a specific industry/team already uses the platform before designing an integration for them.
- Official YouTube: a general "Tutorials" playlist on monday.com's main channel for product onboarding, and a separate developer-focused channel (`@mondayappdeveloper`) mixing build tutorials with go-to-market guidance for people shipping marketplace apps.

## When to actually use this file

Rarely, in practice — most tasks land in one of the technical reference files. This one is for orienting a newcomer to the platform, understanding which monday product a customer is likely using (relevant when designing what a "vendor" or "deal" or "ticket" item might look like on their board), or finding the right support channel when something is a monday-platform question rather than a code question.
