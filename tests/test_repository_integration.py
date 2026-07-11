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


async def test_record_and_list_events_roundtrip(monkeypatch):
    # Proves the audit log persists a trimmed summary and reads it back newest
    # first, scoped to the account.
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    await db.init_db()

    account_id = 900_000_000 + (uuid.uuid4().int % 1_000_000)
    other_account = account_id + 1
    pool = db.get_pool()
    try:
        await repository.record_event(
            account_id=account_id,
            board_id=123,
            item_id=1,
            vendor_name="First Vendor",
            risk_level="Clear",
        )
        await repository.record_event(
            account_id=account_id,
            board_id=123,
            item_id=2,
            vendor_name="Bad Actor",
            risk_level="Critical",
            score=0.95,
            match_id="ent-1",
            match_caption="Bad Actor",
        )
        # A different account's row must not leak into this account's export.
        await repository.record_event(
            account_id=other_account,
            board_id=999,
            item_id=3,
            vendor_name="Someone Else",
            risk_level="Warning",
        )

        events = await repository.list_events(account_id)

        assert [e["item_id"] for e in events] == [2, 1]  # newest first
        assert events[0]["risk_level"] == "Critical"
        assert events[0]["score"] == 0.95
        assert events[0]["match_id"] == "ent-1"
        assert all(e["vendor_name"] != "Someone Else" for e in events)
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM screening_events WHERE account_id = ANY($1::bigint[])",
                [account_id, other_account],
            )
        await db.close_db()


async def test_set_plan_upgrades_then_downgrades_account(monkeypatch):
    # Mirrors a monday subscription webhook: created (free -> pro), then
    # cancelled (pro -> free), and check_quota must reflect it immediately.
    monkeypatch.setattr(repository, "PLAN_LIMITS", {"free": 3, "pro": 10})
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    await db.init_db()

    account_id = 900_000_000 + (uuid.uuid4().int % 1_000_000)
    period = "itest-" + uuid.uuid4().hex[:8]
    pool = db.get_pool()
    try:
        # First sight creates the account on the default plan.
        first = await repository.check_quota(account_id, period=period)
        assert first.plan == "free"

        await repository.set_plan(account_id, "pro")
        upgraded = await repository.check_quota(account_id, period=period)
        assert upgraded.plan == "pro"
        assert upgraded.limit == 10

        await repository.set_plan(account_id, "free")
        downgraded = await repository.check_quota(account_id, period=period)
        assert downgraded.plan == "free"
        assert downgraded.limit == 3
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM usage_counters WHERE account_id = $1", account_id)
            await conn.execute("DELETE FROM accounts WHERE account_id = $1", account_id)
        await db.close_db()
