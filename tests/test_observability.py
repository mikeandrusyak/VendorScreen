import observability


def test_init_sentry_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    # Must not touch the SDK when DSN is absent.
    called = {"init": False}
    monkeypatch.setattr(
        observability.sentry_sdk, "init", lambda **kw: called.__setitem__("init", True)
    )

    assert observability.init_sentry("development") is False
    assert called["init"] is False


def test_init_sentry_initializes_with_dsn(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@o1.ingest.sentry.io/1")
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)

    captured = {}
    monkeypatch.setattr(observability.sentry_sdk, "init", lambda **kw: captured.update(kw))

    assert observability.init_sentry("production") is True
    assert captured["environment"] == "production"
    # KYC/AML tool — PII must never be sent to a third party by default.
    assert captured["send_default_pii"] is False
    assert captured["traces_sample_rate"] == 0.0
