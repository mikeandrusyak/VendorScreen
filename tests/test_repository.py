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
