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
        board_id, item_id, status_column_id, details_column_id, api_token, account_id=None
    ):
        seen["args"] = (board_id, item_id, status_column_id, details_column_id, api_token)
        seen["account_id"] = account_id

    monkeypatch.setattr(main, "process_vendor", fake_process)

    resp = client.post(ACTION_URL, json={"payload": {"inboundFieldValues": _valid_fields()}})

    assert resp.status_code == 200
    assert resp.json() == {}
    assert seen["args"] == ("123", "456", "status", "details", "dev-token")
    # Dev-mode fallback has no signed JWT, so there's no tenant to meter.
    assert seen["account_id"] is None


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
