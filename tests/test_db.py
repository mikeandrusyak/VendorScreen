import db


async def test_init_db_noop_without_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Must not attempt any connection when the variable is absent.
    assert await db.init_db() is False
    assert db.get_pool() is None
    assert db.is_configured() is False


def test_is_configured_reflects_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    assert db.is_configured() is True
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.is_configured() is False


async def test_close_db_safe_without_pool():
    # Closing when nothing was ever opened must not raise.
    await db.close_db()
    assert db.get_pool() is None


def test_migrations_are_ordered_and_unique():
    versions = [version for version, _ in db.MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
