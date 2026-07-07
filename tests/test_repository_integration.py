import os
import uuid

import pytest

import db
import repository

# This suite exercises the atomic quota SQL against a REAL Postgres. It is
# skipped unless TEST_DATABASE_URL points at a throwaway database (CI provides a
# postgres service; locally you can point it at a scratch DB). It never touches
# the production DATABASE_URL.
TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL not set — skipping live Postgres integration test",
)


async def test_quota_allows_exactly_limit_then_blocks(monkeypatch):
    # Small limit keeps the round-trips fast while still proving the behavior.
    monkeypatch.setattr(repository, "PLAN_LIMITS", {"free": 3, "pro": 10})
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    await db.init_db()

    # Unique account + period so parallel/repeat runs never collide.
    account_id = 900_000_000 + (uuid.uuid4().int % 1_000_000)
    period = "itest-" + uuid.uuid4().hex[:8]
    pool = db.get_pool()
    try:
        limit = repository.limit_for_plan("free")
        results = [
            await repository.check_quota(account_id, period=period) for _ in range(limit + 2)
        ]

        allowed = [r for r in results if r.allowed]
        # Exactly `limit` screenings go through, no more (no race past the cap).
        assert len(allowed) == limit
        assert [r.used for r in allowed] == list(range(1, limit + 1))
        # Everything after the cap is blocked and consumes nothing further.
        assert all(not r.allowed for r in results[limit:])
        assert results[-1].used == limit
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM usage_counters WHERE account_id = $1", account_id)
            await conn.execute("DELETE FROM accounts WHERE account_id = $1", account_id)
        await db.close_db()
