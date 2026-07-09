# VendorScreen — Monetization Plan

Scope: this covers what ships now (P0 + P1) and how pricing evolves as P2/P3
features land. It is the reference to update whenever a new feature changes
the cost model or the tier structure — don't re-derive pricing from scratch
each time a feature ships.

## Cost basis

- **OpenSanctions**: €0.10 (~$0.11) per successful `/match` query, no volume
  discount below 20,000 requests/month. This is the only real marginal cost
  per screening today.
- **monday.com marketplace**: the developer keeps 100% of subscription
  revenue until the app reaches $200,000 in lifetime revenue; after that,
  monday takes 15%. Not a launch-time concern, but factor it in before
  re-pricing once the app is generating meaningful revenue.
- monday.com's built-in monetization handles billing, currency conversion,
  invoicing, and payouts — no separate Stripe integration needed.

## Launch pricing (P0 + P1 scope only)

No ongoing monitoring, bulk import, adverse media, or case workflow yet —
those aren't built. Pricing reflects exactly what P0 (usage limits, DB,
error tracking, OpenSanctions outage handling) and P1 (`/match` with score,
audit log + export, Critical alerts) deliver.

| Plan | Price/mo | Screenings/mo | Audit log + export | Critical alerts | Boards |
|---|---|---|---|---|---|
| **Free** | $0 | 20 | — | — | 1 |
| **Pro** | $99 | 400 | ✓ | ✓ | 3 |
| **Business** | $349 | 1,500 | ✓ | ✓ | unlimited |

Gross margin at $0.11/screening: Pro ~56%, Business ~53%. Free is a
deliberate loss-leader (~$2.20/account) for trial/conversion.

No Enterprise line at launch — handle large-volume requests as one-off manual
deals until there's a real pattern of demand for one.

## How the quota works (technical)

- **One shared counter per account per month** (`usage_counters`, keyed by
  `account_id` + `YYYY-MM` period). New screenings and, later, ongoing-monitoring
  rescreens both draw from the same number. This is a deliberate simplification:
  it bounds total OpenSanctions spend at `limit × $0.11` regardless of how the
  customer splits usage, and avoids a second metered dimension the customer
  (or we) would have to reason about.
- **Plan is synced from monday, not set manually.** `POST
  /monday/subscription_webhook` (registered in Developer Center →
  Monetization) receives subscription created/changed/renewed/cancelled
  events and calls `repository.set_plan(account_id, plan)`, which upserts
  `accounts.plan`. `repository.check_quota` then enforces whatever plan is
  currently on file — no manual DB edits needed to move a customer between
  tiers.
- **Plan ids must match between monday and code.** The plan ids configured
  in Developer Center Monetization must be exactly `pro` and `business`
  (lowercase, matching `PLAN_LIMITS` keys in `src/repository.py`) — there is
  no translation table. An unrecognized plan id falls back to `free` rather
  than failing the request.
- **Over-quota behavior today is a hard stop**: once the monthly allowance is
  used, the item is marked `Screening Failed` with an upgrade message — no
  overage billing yet. Revisit this once there's usage data showing how often
  paying customers actually hit their cap; usage-based overage billing needs
  its own design (and isn't necessarily supported cleanly by monday's
  monetization API) and shouldn't be built speculatively.

## Roadmap: how P2/P3 features change pricing

| Feature | Phase | Pricing treatment |
|---|---|---|
| Ongoing monitoring | P2 | No new counter — rescreens consume the same shared monthly quota as new screenings. Don't raise price immediately when it ships; watch 1–2 months of real usage split (new vs. monitoring) before deciding whether to raise the included quota or price |
| Bulk screening | P2 | One-time paid SKU (e.g. $0.25/record, $49 minimum), sold separately from the subscription — never let one CSV import silently drain a month's quota |
| Case workflow | P2 | Feature-gate on Business, no price change — zero marginal cost, strengthens the Pro→Business upgrade case |
| Adverse media | P2 | Do not price until a data provider and its per-query cost are chosen. Show as "Coming soon" on the pricing page, no number attached |
| Dashboard, Shield Badge | P3 | Bundled into Business at no price change — marketing/trust value, zero marginal cost |
| KYB / UBO | P3 | This is the point an actual **Enterprise** tier with custom pricing makes sense — beneficial-owner lookups cost meaningfully more per check than name screening and need their own provider research first |
| Onboarding guide | P3 | Not a priced item — becomes a white-glove onboarding perk bundled into Enterprise |

## Pricing-change policy

- When a feature is added to an **existing** tier (not a new SKU), current
  subscribers keep their existing price for at least 6–12 months. Only new
  signups see the updated price. Reduces churn risk and is easy to
  communicate ("you got more for the same price").
- Never publish a fixed price for a feature whose underlying provider cost is
  unknown (currently: adverse media, KYB/UBO). Mark it "Coming soon" instead.
- Re-check tier margins whenever OpenSanctions or monday's fee structure
  changes, and before crossing the $200k lifetime-revenue mark where monday's
  15% cut kicks in.
