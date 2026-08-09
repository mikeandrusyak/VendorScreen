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
    monkeypatch.setattr(main, "APP_ENV", "development")
    monkeypatch.setenv("MONDAY_API_TOKEN", "dev-token")

    resp = client.post(ACTION_URL, json={"payload": {"inboundFieldValues": {}}})

    assert resp.status_code == 400


def test_valid_payload_enqueues_and_returns_empty(monkeypatch):
    monkeypatch.setattr(main, "APP_ENV", "development")
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
    assert resp.json() == {}
    assert seen["args"] == ("123", "456", "status", "details", "dev-token")
    # Dev-mode fallback has no signed JWT, so there's no tenant to meter.
    assert seen["account_id"] is None


def test_missing_board_id_resolves_from_item(monkeypatch):
    # "When button clicked" doesn't reliably supply boardId (unlike "When item
    # created") — the app must fall back to resolving it from the item.
    monkeypatch.setattr(main, "APP_ENV", "development")
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
    monkeypatch.setattr(main, "APP_ENV", "development")
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
    monkeypatch.setattr(main, "APP_ENV", "production")
    resp = client.post(SUBSCRIPTION_URL, json=_subscription_event("subscription_created"))
    assert resp.status_code == 401


def test_subscription_created_upgrades_plan(monkeypatch):
    monkeypatch.setattr(main, "APP_ENV", "development")
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
    monkeypatch.setattr(main, "APP_ENV", "development")
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
    monkeypatch.setattr(main, "APP_ENV", "development")
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
    monkeypatch.setattr(main, "APP_ENV", "development")
    called = False

    async def fake_set_plan(account_id, plan):
        nonlocal called
        called = True

    monkeypatch.setattr(repository, "set_plan", fake_set_plan)

    resp = client.post(SUBSCRIPTION_URL, json={"type": "item_created", "data": {"account_id": 1}})

    assert resp.status_code == 200
    assert called is False


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
    monkeypatch.setattr(main, "APP_ENV", "development")
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


def test_download_rejects_invalid_token():
    resp = client.get(DOWNLOAD_URL, params={"token": "garbage"})
    assert resp.status_code == 401


def test_download_streams_csv_for_valid_token(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "test-secret")

    import datetime as dt

    async def fake_list_events(account_id, limit=10_000):
        assert account_id == 777
        return [
            {
                "created_at": dt.datetime(2026, 7, 11, 9, 0, tzinfo=dt.UTC),
                "board_id": 123,
                "item_id": 456,
                "vendor_name": "Bad Actor",
                "risk_level": "Critical",
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
    assert "created_at,board_id,item_id,vendor_name,risk_level,score,match_id,match_caption" in body
    assert "Bad Actor" in body
    assert "Critical" in body
    assert "0.95" in body
