import main
from repository import QuotaResult
from sanctions_service import SanctionsUnavailableError


def _stub_screening(monkeypatch, *, name="Acme", result=None):
    """Wire up get_item_name / check / update so a screening can run, recording
    which of them were reached. Returns the shared `calls` dict."""
    calls = {}

    async def fake_get_item_name(item_id, api_token):
        calls["get_item_name"] = True
        return name

    async def fake_check(vendor_name, country=None, account_id=None):
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
    # The item gets the distinct 'Limit Reached' label (not 'Screening Failed',
    # which is reserved for a service outage) with an upgrade message.
    assert calls["update"]["risk_level"] == "Limit Reached"
    assert "limit" in calls["update"]["details"].lower()


async def test_first_block_sends_one_time_upgrade_alert(monkeypatch):
    # The first blocked screening of the period claims the notification and nudges
    # the owner to upgrade — even for an account already over its limit.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=False, used=20, limit=20, plan="free")

    async def fake_claim(account_id):
        return True

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    monkeypatch.setattr(main.repository, "claim_limit_notification", fake_claim)
    _stub_screening(monkeypatch)

    sent = {}

    async def fake_notify(user_id, item_id, text, api_token):
        sent["user_id"] = user_id
        sent["text"] = text

    monkeypatch.setattr(main, "create_notification", fake_notify)

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123", user_id="42")

    assert sent["user_id"] == "42"
    assert "upgrade" in sent["text"].lower()


async def test_subsequent_block_does_not_realert(monkeypatch):
    # Once the claim is taken this period (claim returns False), further blocked
    # screenings stay silent — no notification spam.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=False, used=20, limit=20, plan="free")

    async def fake_claim(account_id):
        return False

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    monkeypatch.setattr(main.repository, "claim_limit_notification", fake_claim)
    _stub_screening(monkeypatch)

    sent = {}

    async def fake_notify(user_id, item_id, text, api_token):
        sent["called"] = True

    monkeypatch.setattr(main, "create_notification", fake_notify)

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123", user_id="42")

    assert "called" not in sent


async def test_under_limit_does_not_alert(monkeypatch):
    # A screening that leaves headroom must not nudge the owner.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=True, used=1, limit=50, plan="free")

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    _stub_screening(monkeypatch, result={"riskLevel": "Clear", "details": "ok"})

    sent = {}

    async def fake_notify(user_id, item_id, text, api_token):
        sent["called"] = True

    monkeypatch.setattr(main, "create_notification", fake_notify)

    await main.process_vendor("b", "i", "s", "d", "tok", account_id="123", user_id="42")

    assert "called" not in sent


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


def _capture_notifications(monkeypatch):
    """Patch create_notification and return the list of calls it received."""
    sent = []

    async def fake_notify(user_id, item_id, text, api_token):
        sent.append({"user_id": user_id, "item_id": item_id, "text": text})

    monkeypatch.setattr(main, "create_notification", fake_notify)
    return sent


async def test_critical_result_notifies_owner(monkeypatch):
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    _stub_screening(
        monkeypatch,
        name="Bad Actor",
        result={"riskLevel": "Critical", "details": "sanction hit"},
    )
    sent = _capture_notifications(monkeypatch)

    await main.process_vendor("b", "item-9", "s", "d", "tok", user_id=42)

    assert len(sent) == 1
    assert sent[0]["user_id"] == 42
    assert sent[0]["item_id"] == "item-9"
    assert "CRITICAL" in sent[0]["text"]
    assert "Bad Actor" in sent[0]["text"]


async def test_clear_and_warning_do_not_notify(monkeypatch):
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    for level in ("Clear", "Warning"):
        _stub_screening(monkeypatch, result={"riskLevel": level, "details": "x"})
        sent = _capture_notifications(monkeypatch)

        await main.process_vendor("b", "i", "s", "d", "tok", user_id=42)

        assert sent == []


async def test_critical_without_user_id_does_not_notify(monkeypatch):
    # Dev requests (no signed JWT) have no userId — nobody to alert.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    _stub_screening(monkeypatch, result={"riskLevel": "Critical", "details": "hit"})
    sent = _capture_notifications(monkeypatch)

    await main.process_vendor("b", "i", "s", "d", "tok", user_id=None)

    assert sent == []


async def test_alert_failure_does_not_break_screening(monkeypatch):
    # A notification failure must not fail the screening that already landed.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    calls = _stub_screening(monkeypatch, result={"riskLevel": "Critical", "details": "hit"})

    async def boom(user_id, item_id, text, api_token):
        raise RuntimeError("monday notifications down")

    monkeypatch.setattr(main, "create_notification", boom)
    captured = {}
    monkeypatch.setattr(
        main.sentry_sdk, "capture_exception", lambda err: captured.setdefault("err", err)
    )

    await main.process_vendor("b", "i", "s", "d", "tok", user_id=42)

    assert calls["update"]["risk_level"] == "Critical"  # screening still completed
    assert "err" in captured  # alert failure reported, not raised


async def test_sanctions_unavailable_marks_board_failed(monkeypatch):
    # OpenSanctions down after retries: the board must show "Screening Failed",
    # not be left blank.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    calls = _stub_screening(monkeypatch)

    async def boom(vendor_name, country=None, account_id=None):
        raise SanctionsUnavailableError("still down after 3 attempts")

    monkeypatch.setattr(main, "check_vendor_with_retry", boom)

    await main.process_vendor("b", "i", "s", "d", "tok")

    assert calls["update"]["risk_level"] == "Screening Failed"


async def test_unexpected_error_marks_board_failed(monkeypatch):
    # Any other unhandled error (not just SanctionsUnavailableError — e.g. a
    # non-retryable OpenSanctions error or a monday lookup failure) must not
    # leave the board blank either.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)
    calls = _stub_screening(monkeypatch)

    async def boom(vendor_name, country=None, account_id=None):
        raise RuntimeError("something unrelated to sanctions broke")

    monkeypatch.setattr(main, "check_vendor_with_retry", boom)

    await main.process_vendor("b", "i", "s", "d", "tok")

    assert calls["update"]["risk_level"] == "Screening Failed"


async def test_screening_writes_single_final_status(monkeypatch):
    # Screening runs inline within the request (monday Code / Cloud Run throttles
    # CPU on a detached background task), so it's fast enough that there's no
    # interim 'Screening…' write — the board goes straight to the final status in
    # exactly one write. Guards against reintroducing the extra pending round-trip.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)

    updates = []

    async def fake_get_item_name(item_id, api_token):
        return "Acme"

    async def fake_check(vendor_name, country=None, account_id=None):
        # No status must have been written before the real screening result.
        assert updates == []
        return {"riskLevel": "Clear", "details": "ok"}

    async def fake_update(**kw):
        updates.append(kw)

    monkeypatch.setattr(main, "get_item_name", fake_get_item_name)
    monkeypatch.setattr(main, "check_vendor_with_retry", fake_check)
    monkeypatch.setattr(main, "update_vendor_record", fake_update)

    await main.process_vendor("b", "i", "s", "d", "tok")

    assert [u["risk_level"] for u in updates] == ["Clear"]


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


# --- screen_vendor_name (conversational / Sidekick) ------------------------


async def test_screen_vendor_name_returns_result_without_board(monkeypatch):
    # Conversational screening returns the result in outputFields and never writes
    # to a board (no item/columns involved).
    monkeypatch.setattr(main.db, "is_configured", lambda: False)

    async def fake_check(vendor_name, country=None, account_id=None):
        assert vendor_name == "Acme"
        return {"riskLevel": "Warning", "details": "possible pep flag"}

    async def no_write(**kw):
        raise AssertionError("conversational screening must not write to a board")

    monkeypatch.setattr(main, "check_vendor_with_retry", fake_check)
    monkeypatch.setattr(main, "update_vendor_record", no_write)

    out = await main.screen_vendor_name("Acme", None, account_id=None)

    assert out["outputFields"]["riskLevel"] == "Warning"
    assert "Acme" in out["outputFields"]["resultMessage"]


async def test_screen_vendor_name_over_quota_is_limit_reached(monkeypatch):
    # An account over its monthly allowance gets a 'Limit Reached' answer and no
    # OpenSanctions call is made.
    monkeypatch.setattr(main.db, "is_configured", lambda: True)

    async def fake_quota(account_id):
        return QuotaResult(allowed=False, used=20, limit=20, plan="free")

    async def fake_check(vendor_name, country=None, account_id=None):
        raise AssertionError("must not screen when over quota")

    monkeypatch.setattr(main.repository, "check_quota", fake_quota)
    monkeypatch.setattr(main, "check_vendor_with_retry", fake_check)

    out = await main.screen_vendor_name("Acme", None, account_id="123")

    assert out["outputFields"]["riskLevel"] == "Limit Reached"
    assert "limit" in out["outputFields"]["resultMessage"].lower()


async def test_screen_vendor_name_failure_is_unavailable(monkeypatch):
    # A screening failure never raises to Sidekick — it returns the fail-safe result.
    monkeypatch.setattr(main.db, "is_configured", lambda: False)

    async def boom(vendor_name, country=None, account_id=None):
        raise SanctionsUnavailableError("down")

    monkeypatch.setattr(main, "check_vendor_with_retry", boom)

    out = await main.screen_vendor_name("Acme", None, account_id=None)

    assert out["outputFields"]["riskLevel"] == "Screening Failed"
