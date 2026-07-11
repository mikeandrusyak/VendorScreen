import datetime as dt

import db
import repository


def test_current_period_formats_year_month():
    fixed = dt.datetime(2026, 7, 7, 12, 0, tzinfo=dt.UTC)
    assert repository.current_period(fixed) == "2026-07"


def test_limit_for_plan_known_and_default():
    assert repository.limit_for_plan("free") == repository.PLAN_LIMITS["free"]
    assert repository.limit_for_plan("pro") == repository.PLAN_LIMITS["pro"]
    # Unknown plans fall back to the free allowance rather than being unlimited.
    assert repository.limit_for_plan("mystery") == repository.PLAN_LIMITS["free"]


async def test_check_quota_returns_none_without_pool(monkeypatch):
    # DB disabled → enforcement is skipped by returning None.
    monkeypatch.setattr(db, "_pool", None)
    assert await repository.check_quota(12345) is None


async def test_set_plan_noop_without_pool(monkeypatch):
    # DB disabled → set_plan is a no-op rather than raising, mirroring
    # check_quota's fail-open behavior.
    monkeypatch.setattr(db, "_pool", None)
    await repository.set_plan(12345, "pro")


async def test_record_event_noop_without_pool(monkeypatch):
    # DB disabled → recording an audit event is a no-op, never raising, so a
    # screening is never blocked by auditing being off.
    monkeypatch.setattr(db, "_pool", None)
    await repository.record_event(
        account_id=1,
        board_id=2,
        item_id=3,
        vendor_name="Acme",
        risk_level="Clear",
    )


async def test_list_events_empty_without_pool(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    assert await repository.list_events(1) == []


def test_as_bigint_coerces_and_tolerates_junk():
    assert repository._as_bigint("456") == 456
    assert repository._as_bigint(789) == 789
    assert repository._as_bigint(None) is None
    # A non-numeric id becomes NULL rather than raising and losing the row.
    assert repository._as_bigint("not-a-number") is None
