import jwt

import session_token


def _token(secret, dat=None, **claims):
    payload = {"dat": dat} if dat is not None else {}
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


def test_verify_reads_account_and_user_from_dat(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    token = _token("client-secret", dat={"account_id": 777, "user_id": 42})
    assert session_token.verify(token) == {"account_id": 777, "user_id": 42}


def test_verify_reads_top_level_claims(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    token = _token("client-secret", accountId=555, userId=9)
    assert session_token.verify(token) == {"account_id": 555, "user_id": 9}


def test_verify_rejects_wrong_secret(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    token = _token("attacker-secret", dat={"account_id": 777})
    assert session_token.verify(token) is None


def test_verify_none_without_secret(monkeypatch):
    monkeypatch.delenv("MONDAY_CLIENT_SECRET", raising=False)
    token = _token("whatever", dat={"account_id": 777})
    assert session_token.verify(token) is None


def test_verify_none_for_empty_token(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    assert session_token.verify("") is None


def test_verify_none_when_no_account(monkeypatch):
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", "client-secret")
    token = _token("client-secret", dat={"user_id": 42})
    assert session_token.verify(token) is None
