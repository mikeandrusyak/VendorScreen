# Monetization

monday's built-in billing system for marketplace apps — plans, checkout, invoicing, currency, and payouts are all handled by monday; your app's job is to define plans, sync subscription state via webhook, and gate features accordingly. As of the current review checklist, **new marketplace apps must be monetized through monday's built-in system** — it's a submission requirement, not an optional add-on.

## Contents
- [Defining plans](#defining-plans)
- [Runtime enforcement is on you](#runtime-enforcement-is-on-you)
- [Subscription webhook events](#subscription-webhook-events)
- [Reading subscription state directly](#reading-subscription-state-directly)
- [Testing without a real subscription](#testing-without-a-real-subscription)
- [Revenue share and payouts](#revenue-share-and-payouts)
- [Trials and discounts](#trials-and-discounts)

## Defining plans

Developer Center → your app → **Monetization** tab. Two models:
- **Feature-based** — up to **15 plans**, tiers by feature access/usage limits. (This is the shape a usage-metered app like a screening tool typically wants.)
- **Seat-based** (current model, effective Aug 2025) — "progressive pricing" using seat buckets scaled to account size. A **legacy seat-based** model (up to 25 plans) still exists but is marked deprecated for new setups.

Plan fields:
| Field | Constraint |
|---|---|
| Plan Name | 1–255 chars, user-facing |
| **ID** | Letters/digits/dashes/underscores, must start with a letter or digit, **case-sensitive** — this is the exact string sent to your backend as `plan_id` |
| Description | 1–255 chars |
| Includes | 1–5 feature bullets, ≤255 chars each |
| Monthly price | non-negative integer, USD |
| Yearly price | monthly-equivalent, non-negative integer, USD |
| Price per seat (seat-based only) | monthly; discount mode: Optimized / No Discount / Manual |

**The ID is developer-chosen, not monday-generated — and it's exactly what shows up in webhook payloads and GraphQL responses.** Whatever plan-id strings your backend's plan-limits table expects must match the Developer Center's plan IDs verbatim, including case. A mismatch doesn't error — it just means the plan silently doesn't map to anything your code recognizes, so treat an unrecognized `plan_id` as "fall back to the most restrictive tier," never as "grant full access."

## Runtime enforcement is on you

Quoting the docs directly: **"Your app must verify active subscriptions at runtime. monday.com does not automatically restrict access when a subscription expires or changes."** There's no platform-side gate — if you don't check the synced plan before doing metered work, an expired or downgraded account keeps getting full access indefinitely.

## Subscription webhook events

monday's own docs list 13 events (one page's summary text says "12," which doesn't match its own enumerated list — a real inconsistency in the source, not a transcription error here):

```
install
uninstall
app_subscription_created
app_subscription_changed
app_subscription_renewed
app_subscription_cancelled_by_user
app_subscription_cancelled
app_subscription_cancellation_revoked_by_user
app_subscription_renewal_attempt_failed
app_subscription_renewal_failed
app_trial_subscription_started
app_trial_subscription_ended
app_subscription_pricing_version_change_scheduled
```

Payload — every event shares this `{type, data}` envelope (this is the `install` example; subscription events look the same with a populated `subscription` object):
```json
{
  "type": "install",
  "data": {
    "app_id": 1000000000,
    "app_name": "Test App",
    "user_id": 2,
    "user_email": "user@example.com",
    "account_id": 777777,
    "account_name": "Demo Account",
    "account_slug": "test",
    "timestamp": "2023-06-26T00:00:00.000+00:00",
    "subscription": {
      "plan_id": "5",
      "renewal_date": "2023-07-10T00:00:00+00:00",
      "is_trial": false,
      "billing_period": "monthly",
      "days_left": 14,
      "pricing_version": 5,
      "max_units": 100
    },
    "user_country": "IL"
  }
}
```
`max_units` is null for feature-based plans (only meaningful for seat-based).

**Signed with your app's Client Secret** (a JWT in the `Authorization` header) — **not** the Signing Secret used for action/trigger/board-webhook requests. See [integrations-and-automations.md](integrations-and-automations.md) for why mixing these up matters. The exact claim list for this specific webhook JWT isn't separately documented from the general session-token JWT — verify against a real received payload if your code depends on specific claims beyond signature validity.

**FAQ nuances worth knowing:**
- A failed payment doesn't cancel anything immediately — the customer has **up to 45 days** to fix payment info before a cancellation-type webhook fires.
- Uninstalling fires **two** webhooks (`uninstall` and `app_subscription_cancelled`), not one — handle both without double-processing.
- Trial installs: `install` **does** include subscription data even for a trial, but `app_subscription_created` explicitly does **not** fire for a trial install — don't wait on `app_subscription_created` to detect a new trial user.

## Reading subscription state directly

Besides the webhook, you can read current state on demand:
- **Frontend (client-side apps):** `monday.get('sessionToken')` → JWT → decode client-side for a quick read, or send it to your backend and verify with `jwt.verify(token, CLIENT_SECRET)` for a trustworthy read.
- **Backend / integration apps:** GraphQL —
  ```graphql
  query { app_subscription { plan_id is_trial billing_period days_left } }
  query { apps_monetization_status { is_supported } }
  ```

## Testing without a real subscription

There's no separate sandbox environment — instead, mock a subscription on a real dev account via GraphQL:
```graphql
mutation {
  set_mock_app_subscription(
    app_id: 12345
    partial_signing_secret: "abcde12345"
    plan_id: "basic_plan_15_users"
  ) { plan_id }
}
```
Mock subscriptions **auto-expire after 24 hours** if not removed; remove early with `remove_mock_app_subscription`. Useful for exercising your webhook handler and plan-gating logic end-to-end before going live.

## Revenue share and payouts

Confirmed exact figures: once an app crosses **$200,000 in lifetime accumulated revenue** (gross, no expense deductions), a revenue-share program activates — from that point forward, **85% to the developer, 15% to monday.com**, per month. *(The common framing of "you keep 100% before that" is a reasonable inference — no share program means no cut — but isn't verbatim wording in monday's docs; the exact, sourced numbers are the 85/15 split after $200K.)*

Payouts: monthly, via Payoneer, USD only, for invoices up to one year old. Finance review takes roughly a week, then a bookkeeping pass; payment is issued within up to 60 days of invoice approval.

## Trials and discounts

Default trial length: **14 days**. A trial extension **overrides** remaining time rather than adding to it — 5 days left + a 10-day extension = 10 days total, not 15.

Discounts are created in the Monetization tab (or via API), scoped to an `account_slug` + `plan_id` + billing period; they apply at checkout for new customers or at the next billing cycle for existing ones. No documented cap on discount size or count.
