import main
from repository import QuotaResult


def _stub_screening(monkeypatch, *, name="Acme", result=None):
    """Wire up get_item_name / check / update so a screening can run, recording
    which of them were reached. Returns the shared `calls` dict."""
    calls = {}

    async def fake_get_item_name(item_id, api_token):
        calls["get_item_name"] = True
        return name

    async def fake_check(vendor_name):
        calls["check"] = vendor_name
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
