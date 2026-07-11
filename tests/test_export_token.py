import datetime as dt

import jwt

import export_token


def test_issue_then_verify_roundtrips_account(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "s3cr3t")

    token = export_token.issue(4321)

    assert export_token.verify(token) == 4321


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "s3cr3t")

    token = export_token.issue(4321, ttl_seconds=-1)  # already expired

    assert export_token.verify(token) is None


def test_tampered_token_is_rejected(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "s3cr3t")
    token = export_token.issue(4321)

    # Flip a character in the signature segment.
    head, payload, sig = token.split(".")
    bad = f"{head}.{payload}.{sig[:-2]}xx"

    assert export_token.verify(bad) is None


def test_wrong_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "right")
    token = export_token.issue(4321)

    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "wrong")
    assert export_token.verify(token) is None


def test_wrong_scope_is_rejected(monkeypatch):
    monkeypatch.setenv("MONDAY_SIGNING_SECRET", "s3cr3t")
    # A validly-signed token that isn't an export token must not authorize export.
    other = jwt.encode(
        {
            "accountId": 4321,
            "scope": "something-else",
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5),
        },
        "s3cr3t",
        algorithm="HS256",
    )

    assert export_token.verify(other) is None
