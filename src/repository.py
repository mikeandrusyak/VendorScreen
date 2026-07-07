import datetime as dt
from dataclasses import dataclass

import db

# Monthly screening allowance per plan. `free` is the default for any account we
# haven't explicitly upgraded. These are placeholder numbers for the P0
# monetization foundation — tune them once pricing is finalized.
PLAN_LIMITS = {
    "free": 50,
    "pro": 10_000,
}
DEFAULT_PLAN = "free"


@dataclass
class QuotaResult:
    """Outcome of a quota check. `allowed` is False when the account has already
    used its monthly allowance; `used`/`limit` are for reporting to the client."""

    allowed: bool
    used: int
    limit: int
    plan: str


def current_period(now: dt.datetime | None = None) -> str:
    """Billing period key, e.g. '2026-07'. Counters reset when the month rolls
    over simply because the key changes — no cron job needed."""
    now = now or dt.datetime.now(dt.UTC)
    return now.strftime("%Y-%m")


def limit_for_plan(plan: str) -> int:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[DEFAULT_PLAN])


async def check_quota(account_id, period: str | None = None) -> QuotaResult | None:
    """Atomically consume one screening from the account's monthly quota.

    Returns None when the database is disabled (DATABASE_URL unset) so the caller
    skips enforcement entirely. Otherwise returns a QuotaResult; `allowed` is
    False when the account is already at its limit and nothing is consumed.

    The account row is created on first sight (default `free` plan). The
    increment is a single conditional upsert so concurrent screenings can't race
    past the limit — the check and the increment happen in one statement.
    """
    pool = db.get_pool()
    if pool is None:
        return None

    account_id = int(account_id)
    period = period or current_period()

    async with pool.acquire() as conn:
        plan = await _get_or_create_account(conn, account_id)
        limit = limit_for_plan(plan)
        used = await _consume(conn, account_id, period, limit)
        if used is None:
            # Conditional update didn't fire: already at the limit. Read the
            # current value back for the client-facing message.
            current = await conn.fetchval(
                "SELECT used FROM usage_counters WHERE account_id = $1 AND period = $2",
                account_id,
                period,
            )
            return QuotaResult(allowed=False, used=current or limit, limit=limit, plan=plan)
        return QuotaResult(allowed=True, used=used, limit=limit, plan=plan)


async def _get_or_create_account(conn, account_id) -> str:
    # The no-op DO UPDATE (instead of DO NOTHING) is what lets RETURNING give us
    # back the plan on both insert and conflict.
    row = await conn.fetchrow(
        "INSERT INTO accounts (account_id) VALUES ($1) "
        "ON CONFLICT (account_id) DO UPDATE SET account_id = EXCLUDED.account_id "
        "RETURNING plan",
        account_id,
    )
    return row["plan"]


async def _consume(conn, account_id, period, limit) -> int | None:
    # First screen of the period inserts used=1. Subsequent ones increment only
    # while under the limit (the WHERE guards the UPDATE); once at the limit the
    # update is skipped and RETURNING yields nothing, so fetchval returns None.
    return await conn.fetchval(
        "INSERT INTO usage_counters (account_id, period, used) VALUES ($1, $2, 1) "
        "ON CONFLICT (account_id, period) DO UPDATE SET used = usage_counters.used + 1 "
        "WHERE usage_counters.used < $3 "
        "RETURNING used",
        account_id,
        period,
        limit,
    )
