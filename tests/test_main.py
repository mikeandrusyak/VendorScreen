import jwt
from fastapi.testclient import TestClient

import main
import repository
from main import app, field_value

client = TestClient(app)

ACTION_URL = "/monday/execute_action"
SUBSCRIPTION_URL = "/monday/subscription_webhook"


def _valid_fields():
    return {
        "boardId": {"boardId": "123"},
        "itemId": {"itemId": "456"},
        "statusColumnId": {"columnId": "status"},
        "detailsColumnId": {"columnId": "details"},
    }


# --- field_value -----------------------------------------------------------


def test_field_value_unwraps_object():
    assert field_value({"columnId": "status"}, "columnId", "id") == "status"


def test_field_value_returns_primitive_untouched():
    assert field_value("plain", "columnId") == "plain"


def test_field_value_none_when_missing():
    assert field_value({"other": 1}, "columnId") is None
    assert field_value(None, "columnId") is None


# --- health ----------------------------------------------------------------


def test_health_endpoints_ok():
    for path in ("/", "/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# --- execute_action --------------------------------------------------------


def test_challenge_is_echoed():
    resp = client.post(ACTION_URL, json={"challenge": "abc123"})
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}


def test_invalid_jwt_is_unauthorized():
    resp = client.post(
        ACTION_URL,
        json={"payload": {"inboundFieldValues": _valid_fields()}},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_missing_fields_is_bad_request(monkeypatch):
    # Dev-mode fallback supplies auth, so we reach field validation.
    monkeypatch.setattr(main, "NODE_ENV", "development")
    monkeypatch.setenv("MONDAY_API_TOKEN", "dev-token")

    resp = client.post(ACTION_URL, json={"payload": {"inboundFieldValues": {}}})

    assert resp.status_code == 400


def test_conversational_vendor_name_returns_output_fields(monkeypatch):
    # A vendorName with no itemId is a Sidekick (conversational) invocation: screen
    # the name and return the result in outputFields — no board item, no columns,
    # and it must NOT hit the missing-fields 400 the board flow uses.
    monkeypatch.setattr(main, "NODE_ENV", "development")
    monkeypatch.setenv("MONDAY_API_TOKEN", "dev-token")

    async def fake_check(vendor_name, country=None):
        assert vendor_name == "Rosneft"
        return {"riskLevel": "Critical", "details": "sanction match"}

    async def no_write(**kw):
        raise AssertionError("conversational screening must not write to a board")

    monkeypatch.setattr(main, "check_vendor_with_retry", fake_check)
    monkeypatch.setattr(main, "update_vendor_record", no_write)

    resp = client.post(
        ACTION_URL,
        json={"payload": {"inboundFieldValues": {"vendorName": "Rosneft"}}},
    )

    assert resp.status_code == 200
    out = resp.json()["outputFields"]
    assert out["riskLevel"] == "Critical"
    assert "Rosneft" in out["resultMessage"]


def test_valid_payload_enqueues_and_returns_output_fields(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "development")
    monkeypatch.setenv("MONDAY_API_TOKEN", "dev-token")

    seen = {}

    async def fake_process(
        board_id,
        item_id,
        status_column_id,
        details_column_id,
        api_token,
        account_id=None,
        country_column_id=None,
        user_id=None,
    ):
        seen["args"] = (board_id, item_id, status_column_id, details_column_id, api_token)
        seen["account_id"] = account_id
        seen["country_column_id"] = country_column_id
        seen["user_id"] = user_id

    monkeypatch.setattr(main, "process_vendor", fake_process)

    resp = client.post(ACTION_URL, json={"payload": {"inboundFieldValues": _valid_fields()}})

    assert resp.status_code == 200
    # process_vendor's fake here returns nothing (like a mock with no result);
    # execute_action must tolerate that and still shape a valid outputFields body.
    assert resp.json() == {
        "outputFields": {"resultMessage": "This item was not screened.", "riskLevel": ""}
    }
    assert seen["args"] == ("123", "456", "status", "details", "dev-token")
    # Dev-mode fallback has no signed JWT, so there's no tenant to meter.
    assert seen["account_id"] is None


def test_missing_board_id_resolves_from_item(monkeypatch):
    # "When button clicked" doesn't reliably supply boardId (unlike "When item
    # created") — the app must fall back to resolving it from the item.
    monkeypatch.setattr(main, "NODE_ENV", "development")
    monkeypatch.setenv("MONDAY_API_TOKEN", "dev-token")

    async def fake_get_board(item_id, api_token):
        assert item_id == "456"
        return "resolved-board"

    monkeypatch.setattr(main, "get_item_board_id", fake_get_board)

    seen = {}

    async def fake_process(board_id, item_id, status_column_id, details_column_id, api_token, **kw):
        seen["board_id"] = board_id

    monkeypatch.setattr(main, "process_vendor", fake_process)

    fields = _valid_fields()
    del fields["boardId"]

    resp = client.post(ACTION_URL, json={"payload": {"inboundFieldValues": fields}})

    assert resp.status_code == 200
    assert seen["board_id"] == "resolved-board"


def test_board_id_resolution_failure_is_bad_request(monkeypatch):
    # If the fallback lookup itself fails, this is still a missing-field 400,
    # not an unhandled exception.
    monkeypatch.setattr(main, "NODE_ENV", "development")
    monkeypatch.setenv("MONDAY_API_TOKEN", "dev-token")

    async def boom(item_id, api_token):
        raise RuntimeError("monday down")

    monkeypatch.setattr(main, "get_item_board_id", boom)

    fields = _valid_fields()
    del fields["boardId"]

    resp = client.post(ACTION_URL, json={"payload": {"inboundFieldValues": fields}})

    assert resp.status_code == 400


def test_valid_jwt_authorizes(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "test-secret")
    monkeypatch.setattr(main, "process_vendor", _noop)

    token = jwt.encode(
        {"shortLivedToken": "slt-123", "aud": "someone"},
        "test-secret",
        algorithm="HS256",
    )

    resp = client.post(
        ACTION_URL,
        json={"payload": {"inboundFieldValues": _valid_fields()}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200


async def _noop(*args, **kwargs):
    pass


# --- subscription_webhook ----------------------------------------------------


def _subscription_event(event_type, account_id=555, plan_id="pro"):
    return {
        "type": event_type,
        "data": {
            "account_id": account_id,
            "subscription": {"plan_id": plan_id} if plan_id else None,
        },
    }


def test_subscription_challenge_is_echoed():
    resp = client.post(SUBSCRIPTION_URL, json={"challenge": "xyz789"})
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "xyz789"}


def test_subscription_webhook_requires_auth_in_production(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "production")
    resp = client.post(SUBSCRIPTION_URL, json=_subscription_event("subscription_created"))
    assert resp.status_code == 401


def test_subscription_created_upgrades_plan(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "development")
    seen = {}

    async def fake_set_plan(account_id, plan):
        seen["account_id"] = account_id
        seen["plan"] = plan

    monkeypatch.setattr(repository, "set_plan", fake_set_plan)

    resp = client.post(
        SUBSCRIPTION_URL, json=_subscription_event("subscription_created", plan_id="pro")
    )

    assert resp.status_code == 200
    assert seen == {"account_id": 555, "plan": "pro"}


def test_subscription_cancelled_downgrades_to_free(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "development")
    seen = {}

    async def fake_set_plan(account_id, plan):
        seen["account_id"] = account_id
        seen["plan"] = plan

    monkeypatch.setattr(repository, "set_plan", fake_set_plan)

    resp = client.post(
        SUBSCRIPTION_URL,
        json=_subscription_event("subscription_cancelled", plan_id=None),
    )

    assert resp.status_code == 200
    assert seen == {"account_id": 555, "plan": "free"}


def test_subscription_unknown_plan_id_defaults_to_free(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "development")
    seen = {}

    async def fake_set_plan(account_id, plan):
        seen["account_id"] = account_id
        seen["plan"] = plan

    monkeypatch.setattr(repository, "set_plan", fake_set_plan)

    resp = client.post(
        SUBSCRIPTION_URL,
        json=_subscription_event("subscription_changed", plan_id="mystery_tier"),
    )

    assert resp.status_code == 200
    assert seen == {"account_id": 555, "plan": "free"}


def test_subscription_unrelated_event_is_ignored(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "development")
    called = False

    async def fake_set_plan(account_id, plan):
        nonlocal called
        called = True

    monkeypatch.setattr(repository, "set_plan", fake_set_plan)

    resp = client.post(SUBSCRIPTION_URL, json={"type": "item_created", "data": {"account_id": 1}})

    assert resp.status_code == 200
    assert called is False


def test_uninstall_purges_account_data(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "development")
    purged = {}

    async def fake_purge(account_id):
        purged["account_id"] = account_id
        return True

    async def fail_set_plan(account_id, plan):  # must NOT run on uninstall
        raise AssertionError("set_plan should not be called on uninstall")

    monkeypatch.setattr(repository, "purge_account", fake_purge)
    monkeypatch.setattr(repository, "set_plan", fail_set_plan)

    resp = client.post(SUBSCRIPTION_URL, json={"type": "uninstall", "data": {"account_id": 777}})

    assert resp.status_code == 200
    assert purged == {"account_id": 777}


def test_uninstall_requires_auth_in_production(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "production")
    called = False

    async def fake_purge(account_id):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(repository, "purge_account", fake_purge)

    resp = client.post(SUBSCRIPTION_URL, json={"type": "uninstall", "data": {"account_id": 777}})

    assert resp.status_code == 401
    assert called is False


def test_uninstall_purge_failure_still_returns_200(monkeypatch):
    monkeypatch.setattr(main, "NODE_ENV", "development")

    async def boom_purge(account_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(repository, "purge_account", boom_purge)

    resp = client.post(SUBSCRIPTION_URL, json={"type": "uninstall", "data": {"account_id": 777}})

    # Fail-open: a purge error is logged/reported but the webhook still acks, so
    # monday doesn't hammer us with retries.
    assert resp.status_code == 200


# --- audit export ------------------------------------------------------------

EXPORT_URL = "/monday/export_action"
DOWNLOAD_URL = "/audit/export"


def _export_jwt(secret="test-secret", account_id=777, user_id=42):
    return jwt.encode(
        {"shortLivedToken": "slt-1", "accountId": account_id, "userId": user_id, "aud": "x"},
        secret,
        algorithm="HS256",
    )


def test_export_action_challenge_is_echoed():
    resp = client.post(EXPORT_URL, json={"challenge": "abc"})
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc"}


def test_export_action_requires_account(monkeypatch):
    # Dev fallback auth has no accountId, so there's no tenant to scope to.
    monkeypatch.setattr(main, "NODE_ENV", "development")
    monkeypatch.setenv("MONDAY_API_TOKEN", "dev-token")

    resp = client.post(EXPORT_URL, json={"payload": {"inboundFieldValues": {"itemId": "1"}}})

    assert resp.status_code == 400


def test_export_action_sends_notification_link(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "test-secret")
    sent = {}

    async def fake_notify(user_id, item_id, text, api_token):
        sent["user_id"] = user_id
        sent["item_id"] = item_id
        sent["text"] = text

    monkeypatch.setattr(main, "create_notification", fake_notify)

    resp = client.post(
        EXPORT_URL,
        json={"payload": {"inboundFieldValues": {"itemId": {"itemId": "456"}}}},
        headers={"Authorization": f"Bearer {_export_jwt()}"},
    )

    assert resp.status_code == 200
    assert sent["user_id"] == 42
    assert sent["item_id"] == "456"
    # The notification carries a tokenized download link.
    assert "/audit/export?token=" in sent["text"]


class _FakeRequest:
    def __init__(self, headers, base_url):
        self.headers = headers
        self.base_url = base_url


def test_public_base_url_prefers_configured(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.monday.app/")
    req = _FakeRequest(
        {"x-forwarded-host": "proxy.example", "x-forwarded-proto": "https"},
        "http://internal.a.run.app/",
    )
    assert main.public_base_url(req) == "https://app.monday.app"


def test_public_base_url_prefers_jwt_aud_origin(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    req = _FakeRequest(
        {"x-forwarded-host": "proxy.example", "x-forwarded-proto": "https"},
        "http://internal.a.run.app/",
    )
    auth = {"aud": "https://a70b7-service.us.monday.app/monday/export_action"}
    assert main.public_base_url(req, auth) == "https://a70b7-service.us.monday.app"


def test_public_base_url_ignores_non_url_aud(monkeypatch):
    # aud isn't always a URL; fall through to the next source rather than emit junk.
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    req = _FakeRequest(
        {"x-forwarded-host": "live1-service.us.monday.app", "x-forwarded-proto": "https"},
        "http://internal.a.run.app/",
    )
    assert (
        main.public_base_url(req, {"aud": "some-app-id"}) == "https://live1-service.us.monday.app"
    )


def test_public_base_url_falls_back_to_forwarded_host(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    req = _FakeRequest(
        {"x-forwarded-host": "live1-service.us.monday.app", "x-forwarded-proto": "https"},
        "http://internal.a.run.app/",
    )
    assert main.public_base_url(req) == "https://live1-service.us.monday.app"


def test_public_base_url_falls_back_to_base_url(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    req = _FakeRequest({}, "http://localhost:3000/")
    assert main.public_base_url(req) == "http://localhost:3000"


def test_export_link_uses_forwarded_host(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "test-secret")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    sent = {}

    async def fake_notify(user_id, item_id, text, api_token):
        sent["text"] = text

    monkeypatch.setattr(main, "create_notification", fake_notify)

    resp = client.post(
        EXPORT_URL,
        json={"payload": {"inboundFieldValues": {"itemId": {"itemId": "456"}}}},
        headers={
            "Authorization": f"Bearer {_export_jwt()}",
            "X-Forwarded-Host": "live1-service.us.monday.app",
            "X-Forwarded-Proto": "https",
        },
    )

    assert resp.status_code == 200
    # The link points at the public monday.app host, not the internal Cloud Run URL.
    assert "https://live1-service.us.monday.app/audit/export?token=" in sent["text"]
    assert ".a.run.app" not in sent["text"]


def test_export_link_uses_jwt_aud_host(monkeypatch):
    # The real monday action JWT carries the public endpoint URL in `aud`; the
    # link must be built from that host (it differs per draft/live deployment).
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "test-secret")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    sent = {}

    async def fake_notify(user_id, item_id, text, api_token):
        sent["text"] = text

    monkeypatch.setattr(main, "create_notification", fake_notify)

    token = jwt.encode(
        {
            "shortLivedToken": "slt-1",
            "accountId": 777,
            "userId": 42,
            "aud": "https://a70b7-service.us.monday.app/monday/export_action",
        },
        "test-secret",
        algorithm="HS256",
    )

    resp = client.post(
        EXPORT_URL,
        json={"payload": {"inboundFieldValues": {"itemId": {"itemId": "456"}}}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert "https://a70b7-service.us.monday.app/audit/export?token=" in sent["text"]
    assert ".a.run.app" not in sent["text"]


def test_download_rejects_invalid_token():
    resp = client.get(DOWNLOAD_URL, params={"token": "garbage"})
    assert resp.status_code == 401


def test_download_streams_csv_for_valid_token(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "test-secret")

    import datetime as dt

    async def fake_list_events(account_id, board_id=None, limit=10_000):
        assert account_id == 777
        return [
            {
                "created_at": dt.datetime(2026, 7, 11, 9, 0, tzinfo=dt.UTC),
                "board_id": 123,
                "item_id": 456,
                "vendor_name": "Bad Actor",
                "country": "Russia",
                "risk_level": "Critical",
                "match_type": "sanction",
                "score": 0.95,
                "match_id": "ent-1",
                "match_caption": "Bad Actor",
            }
        ]

    monkeypatch.setattr(repository, "list_events", fake_list_events)

    token = main.export_token.issue(777)
    resp = client.get(DOWNLOAD_URL, params={"token": token})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.text
    assert (
        "created_at,board_id,item_id,vendor_name,country,risk_level,match_type,"
        "score,match_id,match_caption" in body
    )
    assert "Bad Actor" in body
    assert "Critical" in body
    assert "Russia" in body
    assert "sanction" in body
    assert "0.95" in body


# --- board view --------------------------------------------------------------

VIEW_JSON_URL = "/view/audit.json"
VIEW_CSV_URL = "/view/audit.csv"


def _session_jwt(secret="client-secret", account_id=777, user_id=42):
    return jwt.encode(
        {"dat": {"account_id": account_id, "user_id": user_id}},
        secret,
        algorithm="HS256",
    )


def test_board_view_serves_html():
    resp = client.get("/view")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Screening Audit" in resp.text


def test_board_view_json_requires_session_token(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    resp = client.get(VIEW_JSON_URL, params={"boardId": "123"})
    assert resp.status_code == 401


def test_board_view_json_requires_board_id(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    resp = client.get(VIEW_JSON_URL, headers={"Authorization": f"Bearer {_session_jwt()}"})
    assert resp.status_code == 400


def test_board_view_json_returns_board_scoped_rows(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")

    import datetime as dt

    async def fake_list_events(account_id, board_id=None, limit=10_000):
        assert account_id == 777
        assert board_id == "123"
        return [
            {
                "created_at": dt.datetime(2026, 7, 11, 9, 0, tzinfo=dt.UTC),
                "board_id": 123,
                "item_id": 456,
                "vendor_name": "Bad Actor",
                "country": "Russia",
                "risk_level": "Critical",
                "match_type": "sanction",
                "score": 0.95,
                "match_id": "ent-1",
                "match_caption": "Bad Actor",
            }
        ]

    monkeypatch.setattr(repository, "list_events", fake_list_events)

    resp = client.get(
        VIEW_JSON_URL,
        params={"boardId": "123"},
        headers={"Authorization": f"Bearer {_session_jwt()}"},
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["vendor_name"] == "Bad Actor"
    assert rows[0]["country"] == "Russia"
    assert rows[0]["match_type"] == "sanction"
    assert rows[0]["created_at"] == "2026-07-11T09:00:00+00:00"


def test_board_view_csv_streams_board_scoped(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")

    async def fake_list_events(account_id, board_id=None, limit=10_000):
        assert board_id == "123"
        return []

    monkeypatch.setattr(repository, "list_events", fake_list_events)

    resp = client.get(
        VIEW_CSV_URL,
        params={"boardId": "123"},
        headers={"Authorization": f"Bearer {_session_jwt()}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "board-123" in resp.headers["content-disposition"]


def test_board_view_csv_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    resp = client.get(
        VIEW_CSV_URL,
        params={"boardId": "123"},
        headers={"Authorization": f"Bearer {_session_jwt(secret='attacker')}"},
    )
    assert resp.status_code == 401
