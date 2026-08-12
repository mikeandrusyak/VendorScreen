import datetime as dt
from dataclasses import dataclass

import db

# Monthly screening allowance per plan. `free` is the default for any account we
# haven't explicitly upgraded. Matches the launch pricing in MONETIZATION.md —
# a single shared quota per plan (new screenings and, later, ongoing-monitoring
# rescreens both draw from it). Plan ids here must match the plan ids
# configured in the monday.com Developer Center Monetization tab, since the
# subscription webhook in main.py passes monday's plan_id straight through.
PLAN_LIMITS = {
    "free": 20,
    "pro": 400,
    "business": 1500,
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


async def claim_limit_notification(account_id, period: str | None = None) -> bool:
    """Atomically claim the once-per-period 'limit reached' upgrade nudge.

    Returns True for exactly the first over-limit screening in the period (its
    caller then sends the notification), and False for every subsequent one — the
    conditional UPDATE makes the claim race-safe across concurrent blocked
    screenings. Returns False when the database is disabled. The usage_counters
    row always exists here (an account can only be over-limit after check_quota
    created and filled it), so no upsert is needed.
    """
    pool = db.get_pool()
    if pool is None:
        return False

    account_id = int(account_id)
    period = period or current_period()
    async with pool.acquire() as conn:
        claimed = await conn.fetchval(
            "UPDATE usage_counters SET limit_notified = true "
            "WHERE account_id = $1 AND period = $2 AND limit_notified = false "
            "RETURNING 1",
            account_id,
            period,
        )
    return claimed is not None


async def set_plan(account_id, plan: str) -> None:
    """Upsert an account's plan, driven by a monday.com subscription webhook
    event (created/changed/renewed/cancelled). No-op when the database is
    disabled, mirroring check_quota's fail-open behavior."""
    pool = db.get_pool()
    if pool is None:
        return

    account_id = int(account_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO accounts (account_id, plan) VALUES ($1, $2) "
            "ON CONFLICT (account_id) DO UPDATE SET plan = EXCLUDED.plan",
            account_id,
            plan,
        )


async def purge_account(account_id) -> bool:
    """Permanently delete every row we hold for one account — the audit log,
    usage counters, and the account record itself.

    Called when monday reports the app was uninstalled from that account, to
    satisfy the marketplace requirement that all end-user data be deleted after
    uninstall. Returns True when a purge ran, False when the database is disabled
    (nothing is stored, so there's nothing to delete). No-op / fail-open on a
    disabled DB, mirroring the rest of this module.

    The three deletes run in one transaction so a partial purge can't leave, say,
    audit rows behind after the account row is gone.
    """
    pool = db.get_pool()
    if pool is None:
        return False

    account_id = int(account_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM screening_events WHERE account_id = $1", account_id)
            await conn.execute("DELETE FROM usage_counters WHERE account_id = $1", account_id)
            await conn.execute("DELETE FROM accounts WHERE account_id = $1", account_id)
    return True


async def record_event(
    *,
    account_id,
    board_id,
    item_id,
    vendor_name,
    risk_level,
    score=None,
    match_id=None,
    match_caption=None,
    country=None,
    match_type=None,
) -> None:
    """Append one screening outcome to the audit log.

    No-op when the database is disabled (pool is None), mirroring check_quota's
    fail-open behavior — auditing must never block or fail a screening. The board
    / item ids are stored as BIGINT (monday ids are numeric); a non-numeric id is
    coerced to NULL rather than raising, so a malformed id can't lose the row.
    """
    pool = db.get_pool()
    if pool is None:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO screening_events "
            "(account_id, board_id, item_id, vendor_name, risk_level, score, "
            "match_id, match_caption, country, match_type) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            int(account_id),
            _as_bigint(board_id),
            _as_bigint(item_id),
            vendor_name,
            risk_level,
            score,
            match_id,
            match_caption,
            country,
            match_type,
        )


async def list_events(account_id, board_id=None, limit: int = 10_000) -> list[dict]:
    """Return an account's screening events, newest first, for CSV export.

    When `board_id` is given, restrict to that board (the board-view export is
    board-scoped). Returns an empty list when the database is disabled. Capped by
    `limit` so a single export can't stream an unbounded result set.
    """
    pool = db.get_pool()
    if pool is None:
        return []

    select = (
        "SELECT created_at, board_id, item_id, vendor_name, risk_level, "
        "score, match_id, match_caption, country, match_type "
        "FROM screening_events WHERE account_id = $1"
    )
    params = [int(account_id)]
    board_id = _as_bigint(board_id)
    if board_id is not None:
        params.append(board_id)
        select += f" AND board_id = ${len(params)}"
    params.append(limit)
    select += f" ORDER BY created_at DESC LIMIT ${len(params)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(select, *params)
    return [dict(row) for row in rows]


def _as_bigint(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
