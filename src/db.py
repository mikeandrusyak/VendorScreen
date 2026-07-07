import logging
import os

log = logging.getLogger("vendorscreen")

# Module-level connection pool. Stays None until init_db() succeeds, which only
# happens when DATABASE_URL is configured. Everything downstream treats a None
# pool as "database disabled" and skips gracefully — the app and local dev keep
# working exactly as before until the variable is set (mirrors the optional
# Sentry pattern in observability.py).
_pool = None

# Ordered schema migrations. Each entry is (version, SQL). Applied once, in
# order, and recorded in schema_migrations so re-runs are no-ops. Add new
# migrations by appending — never edit or renumber an applied one.
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS accounts (
            account_id BIGINT PRIMARY KEY,
            plan       TEXT NOT NULL DEFAULT 'free',
            status     TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS usage_counters (
            account_id BIGINT NOT NULL REFERENCES accounts (account_id),
            period     TEXT NOT NULL,
            used       INT NOT NULL DEFAULT 0,
            PRIMARY KEY (account_id, period)
        );
        """,
    ),
]


def is_configured() -> bool:
    """True when a database is configured via DATABASE_URL."""
    return bool(os.getenv("DATABASE_URL"))


def get_pool():
    """Return the active connection pool, or None when the database is disabled."""
    return _pool


async def init_db() -> bool:
    """Create the connection pool and apply migrations if DATABASE_URL is set.

    No-op when DATABASE_URL is unset: returns False and leaves the pool as None,
    so usage limits are simply disabled and everything else runs unchanged. This
    keeps local dev and any deploy without the variable working as before.

    asyncpg is imported lazily so the module imports cleanly even where the
    driver isn't installed (e.g. a minimal test environment without a database).
    """
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        log.info("[db] DATABASE_URL not set — usage limits disabled")
        return False

    import asyncpg

    _pool = await asyncpg.create_pool(
        dsn,
        min_size=int(os.getenv("DB_POOL_MIN", "1")),
        max_size=int(os.getenv("DB_POOL_MAX", "5")),
    )
    await _run_migrations()
    log.info("[db] Connected — migrations applied")
    return True


async def close_db() -> None:
    """Close the pool on shutdown. Safe to call when the pool was never created."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _run_migrations() -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INT PRIMARY KEY, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {
            row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")
        }
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            # Each migration + its bookkeeping row commit together, so a failure
            # never leaves a half-applied version recorded as done.
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
            log.info("[db] Applied migration %d", version)
