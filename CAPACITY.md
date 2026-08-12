# VendorScreen — Capacity & Cost at Scale

How infrastructure cost and provider free-tier limits behave as real customers
arrive. This complements [MONETIZATION.md](./MONETIZATION.md) — that doc owns
per-unit economics, tier prices, and pricing policy; this one projects those
numbers across customer-count scenarios and says **when each provider's free
tier runs out**. Update it whenever a provider's pricing or the plan quotas
change.

> Figures verified against provider pricing as of **August 2026** — see
> [Sources](#sources). Provider pricing moves; re-check before relying on a
> number for a pricing decision.

## TL;DR

- **OpenSanctions is ~100% of marginal cost.** Every screening is one paid
  `/match` query (~$0.11). Neon and Sentry stay on their free tiers well past
  the first ~100 paying customers.
- **Per-customer cost is hard-capped by the monthly quota.** The shared
  `usage_counters` limit bounds OpenSanctions spend at `limit × $0.11` per
  account, no matter how the customer uses it — so worst-case COGS is fully
  predictable (see table below).
- **The free tier is the only cost that scales with *non-paying* users**
  (up to $2.20/account/mo). This is why the free→paid conversion nudge
  (the over-limit signal) directly protects margin.
- **Two thresholds to watch as you grow:** OpenSanctions volume discount kicks
  in above **20,000 req/mo** (unit cost drops), and monday takes **15%** after
  **$200k lifetime revenue**.

## Unit economics recap (from MONETIZATION.md)

| Plan | Price/mo | Screenings/mo | Max OpenSanctions cost¹ | Gross/mo¹ | Margin |
|---|---:|---:|---:|---:|---:|
| Free | $0 | 20 | $2.20 | −$2.20 | loss-leader |
| Pro | $99 | 400 | $44.00 | $55.00 | ~56% |
| Business | $349 | 1,500 | $165.00 | $184.00 | ~53% |

¹ *Worst case = 100% quota utilization. Real customers rarely max out, so actual
cost is lower and margin equal-or-better. The quota caps spend either way.*

## Scaling scenarios

All costs are **worst-case (every customer maxes their quota)** — a ceiling, not
a forecast. Revenue assumes the listed paid mix.

| Scenario | Customers (Free / Pro / Biz) | Max screenings/mo | Max OpenSanctions cost/mo | Revenue/mo | Gross/mo |
|---|---|---:|---:|---:|---:|
| **Early** | 10 / 3 / 1 | 2,900 | ~$319 | $646 | ~$327 |
| **Growth** | 50 / 20 / 5 | 16,500 | ~$1,815 | $3,725 | ~$1,910 |
| **Scale** | 200 / 100 / 30 | 89,000 | ~$9,790² | $20,370 | ~$10,580 |

² *At 89k req/mo you're past the 20k volume-discount threshold, so the real
OpenSanctions unit cost — and this line — is lower than the undiscounted figure
shown.*

## When each provider's free tier runs out

### OpenSanctions — never "free", scales linearly (but capped per account)
There is no meaningful free allowance to plan around (trial keys have an
undisclosed safety-stop quota, then `429` until next month). Treat **every
screening as ~$0.11 from day one**. This is already priced into the margins
above. Above 20k req/mo the unit price drops (volume discount) — a tailwind at
Scale, not a cliff.

### Neon (Postgres) — free tier lasts ~1.5+ years even at Scale
- **Storage:** 0.5 GB free. Audit rows are lean (trimmed match summary, no raw
  payload) — roughly **~300 bytes/row all-in**, so ~**1.7M rows** fit in the
  free tier. Even the Scale scenario (89k screenings/mo worst-case) takes
  **~18–19 months** to fill 0.5 GB. Past that, storage is **$0.35/GB-month** —
  i.e. pennies. Same `DATABASE_URL`, no migration (README "Capacity and scaling").
- **Compute:** 100 CU-hours/mo free, with **scale-to-zero** (`DB_POOL_MIN=0`, so
  no idle compute burns hours). Each screening is a few millisecond-scale
  queries; even Scale volume stays comfortably under 100 CU-hours. The only way
  to blow this early is setting `DB_POOL_MIN=1` (warm connection) on free —
  don't, unless you've moved to a paid always-on plan.
- **Verdict:** Neon is **not** a near-term constraint. Revisit around the point
  audit history crosses ~1M rows or you want an always-on warm pool.

### Sentry — 5,000 events/mo free, only errors sent
- The app sends **errors only** (`send_default_pii=False`, traces sample rate
  `0` by default), so normal operation produces near-zero events. 5k/mo is
  ample unless something is broken.
- **The one burst risk:** a sustained OpenSanctions outage captures one event
  per failed screening — a Scale-level outage could approach 5k in a day. On the
  free plan, excess events are simply **dropped (no overage charge)**; you'd
  lose some error visibility during the incident, not get a bill. If that
  becomes a real risk, Team ($26/mo, 50k events) is the fix — or lower capture
  volume by sampling the outage path.
- **Verdict:** free tier is fine at every scenario here; only revisit if error
  volume (not screening volume) grows.

## Cost levers & risks

- **Free-tier drag scales with free signups, not revenue.** 200 free accounts
  maxing out = up to **$440/mo of pure cost**. Realistic utilization is far
  lower, but this is the one line that grows without paying customers — so
  conversion and the over-limit upgrade nudge are cost controls, not just UX.
- **Bulk import must never draw from the subscription quota** (already the plan
  in MONETIZATION.md: sell it as a separate paid SKU). One 5,000-row CSV at
  $0.11/row is $550 of OpenSanctions cost — that cannot silently land inside a
  $99 Pro plan.
- **monday's 15% cut** starts at $200k lifetime revenue (~within a year at the
  Scale mix). Re-check tier margins before crossing it.
- **Re-check this doc** whenever OpenSanctions unit price, Neon pricing, or the
  plan quotas in `repository.PLAN_LIMITS` change.

## Sources

- [OpenSanctions — API metering & cost](https://www.opensanctions.org/faq/api/metering/) (€0.10/successful match query; only 200-responses billed)
- [OpenSanctions — API key quota](https://www.opensanctions.org/faq/api/quota/) (safety-stop quota, `429` when exceeded)
- [OpenSanctions — usage limits & optimization](https://www.opensanctions.org/faq/api/api-usage-limits-optimization/) (volume discounts from 20k req/mo)
- [Neon — pricing 2026](https://vela.simplyblock.io/articles/neon-serverless-postgres-pricing-2026/) (free: 100 CU-h/mo, 0.5 GB; storage $0.35/GB-mo, compute $0.106/CU-h)
- [Sentry — free Developer plan 2026](https://sentrypricing.com/free-plan) (5,000 events/mo, errors dropped—not charged—at cap)
