import datetime as dt
import os

import jwt

# Short-lived, signed download token for audit-log export. The app is a headless
# recipe backend with no user session: it only ever holds a monday JWT during a
# recipe action. So we can't gate a browser download by session — instead the
# export recipe action mints one of these tokens, delivered to the user via a
# monday notification link. The token IS the one-time credential: it is signed
# with MONDAY_SIGNING_SECRET (already the app's trust anchor), scoped to a single
# account, and expires quickly. No password store, no extra secret to manage.
SCOPE = "audit-export"
DEFAULT_TTL_SECONDS = 900  # 15 minutes — long enough to click, short enough to expire


def _secret() -> str:
    return os.getenv("MONDAY_SIGNING_SECRET") or ""


def issue(account_id, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed export token bound to one account, expiring after ttl."""
    now = dt.datetime.now(dt.UTC)
    return jwt.encode(
        {
            "accountId": int(account_id),
            "scope": SCOPE,
            "exp": now + dt.timedelta(seconds=ttl_seconds),
            "iat": now,
        },
        _secret(),
        algorithm="HS256",
    )


def verify(token: str):
    """Return the account_id a valid token authorizes, or None.

    None on any failure — bad signature, expiry, tampering, or wrong scope — so
    the caller treats all of them identically as an invalid link.
    """
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("scope") != SCOPE:
        return None
    return payload.get("accountId")
