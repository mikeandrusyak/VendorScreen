import main
from repository import QuotaResult


def _stub_screening(monkeypatch, *, name="Acme", result=None):
    """Wire up get_item_name / check / update so a screening can run, recording
    which of them were reached. Returns the shared `calls` dict."""
    calls = {}

    async def fake_get_item_name(item_id, api_token):
        calls["get_item_name"] = True
        return name

    async def fake_check(vendor_name, country=None):
        calls["check"] = vendor_name
        calls["country"] = country
        return result or {"riskLevel": "Clear", "details": "ok"}

    async def fake_update(**kw):
        calls["update"] = kw

    monkeypatch.setattr(main, "get_item_name", fake_get_item_name)
    monkeypatch.setattr(main, "check_vendor_with_retry", fake_check)
    monkeypatch.setattr(main, "update_vendor_record", fake_update)
    return calls


async def test_over_limit_marks_board_and_skips_screening(monkeypatch):
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=False, used=50, limit=50, plan="free")

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    calls = _stub_screening(monkeypatch)

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123")

    # No paid work happened — neither the name lookup nor the OpenSanctions call.
    assert "get_item_name" not in calls
    assert "check" not in calls
    # The item is marked so it isn't left blank, with a limit message.
    assert calls["update"]["risk_level"] == "Screening Failed"
    assert "limit" in calls["update"]["details"].lower()


async def test_under_limit_proceeds_to_screening(monkeypatch):
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=True, used=1, limit=50, plan="free")

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    calls = _stub_screening(monkeypatch, result={"riskLevel": "Warning", "details": "pep"})

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123")

    assert calls["check"] == "Acme"
    assert calls["update"]["risk_level"] == "Warning"


async def test_db_error_does_not_block_screening(monkeypatch):
    # A quota-check failure must fail open: the core product keeps working.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def boom(account_id):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(main.repository, "check_quota", boom)

    captured = {}
    monkeypatch.setattr(
        main.sentry_sdk, "capture_exception", lambda err: captured.setdefault("err", err)
    )
    calls = _stub_screening(monkeypatch, result={"riskLevel": "Clear", "details": "ok"})

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123")

    assert calls["check"] == "Acme"  # screening still ran
    assert calls["update"]["risk_level"] == "Clear"
    assert "err" in captured  # and the DB error was reported


async def test_country_column_is_read_and_passed_to_screening(monkeypatch):
    # When the client maps a country column, its value is read and threaded into
    # the /match query to sharpen the result.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    calls = _stub_screening(monkeypatch)

    async def fake_country(item_id, column_id, api_token):
        return "Ukraine"

    monkeypatch.setattr(main, "get_item_column_text", fake_country)

    await main.process_vendor(
        "b", "i", "s", "d", "tok", account_id=None, country_column_id="country"
    )

    assert calls["country"] == "Ukraine"


async def test_country_read_failure_falls_back_to_name_only(monkeypatch):
    # A failure reading the country column must not abort the screening.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    calls = _stub_screening(monkeypatch)

    async def boom(item_id, column_id, api_token):
        raise RuntimeError("monday down")

    monkeypatch.setattr(main, "get_item_column_text", boom)

    await main.process_vendor(
        "b", "i", "s", "d", "tok", account_id=None, country_column_id="country"
    )

    assert calls["check"] == "Acme"  # screening still ran
    assert calls["country"] is None  # fell back to name-only


async def test_successful_screening_records_audit_event(monkeypatch):
    # With a tenant + DB, the outcome (incl. the match summary) is logged.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=True, used=1, limit=50, plan="free")

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    _stub_screening(
        monkeypatch,
        result={
            "riskLevel": "Critical",
            "details": "hit",
            "score": 0.95,
            "matchId": "ent-1",
            "matchCaption": "Bad Actor",
        },
    )

    recorded = {}

    async def fake_record(**kw):
        recorded.update(kw)

    monkeypatch.setattr(main.repository, "record_event", fake_record)

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123")

    assert recorded["account_id"] == "123"
    assert recorded["risk_level"] == "Critical"
    assert recorded["score"] == 0.95
    assert recorded["match_id"] == "ent-1"
    assert recorded["vendor_name"] == "Acme"


async def test_audit_skipped_without_account(monkeypatch):
    # Dev requests (no account) don't write audit rows.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)
    _stub_screening(monkeypatch)

    called = {"record": False}

    async def fake_record(**kw):
        called["record"] = True

    monkeypatch.setattr(main.repository, "record_event", fake_record)

    await main.process_vendor("b", "i", "s", "d", "tok", account_id=None)

    assert called["record"] is False


async def test_audit_failure_does_not_break_screening(monkeypatch):
    # An audit write blowing up must not fail the screening that already landed.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=True, used=1, limit=50, plan="free")

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    calls = _stub_screening(monkeypatch, result={"riskLevel": "Clear", "details": "ok"})

    async def boom(**kw):
        raise RuntimeError("audit db down")

    monkeypatch.setattr(main.repository, "record_event", boom)
    captured = {}
    monkeypatch.setattr(
        main.sentry_sdk, "capture_exception", lambda err: captured.setdefault("err", err)
    )

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123")

    assert calls["update"]["risk_level"] == "Clear"  # screening still completed
    assert "err" in captured  # audit failure was reported, not raised


async def test_no_account_id_skips_quota_entirely(monkeypatch):
    # Dev requests have no account — quota must not even be consulted.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)
    consulted = {"quota": False}

    async def fake_quota(account_id):
        consulted["quota"] = True
        return None

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    calls = _stub_screening(monkeypatch)

    await main.process_vendor("b", "i", "s", "d", "tok", account_id=None)

    assert consulted["quota"] is False
    assert calls["check"] == "Acme"
