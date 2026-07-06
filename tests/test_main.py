import jwt
from fastapi.testclient import TestClient

import main
from main import app, field_value

client = TestClient(app)

ACTION_URL = "/monday/execute_action"


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

    async def fake_process(board_id, item_id, status_column_id, details_column_id, api_token):
        seen["args"] = (board_id, item_id, status_column_id, details_column_id, api_token)

    monkeypatch.setattr(main, "process_vendor", fake_process)

    resp = client.post(ACTION_URL, json={"payload": {"inboundFieldValues": _valid_fields()}})

    assert resp.status_code == 200
    assert resp.json() == {}
    assert seen["args"] == ("123", "456", "status", "details", "dev-token")


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
