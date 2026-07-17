"""
Verify inbound requests from monday.com.

monday signs two DIFFERENT categories of request with two DIFFERENT secrets, and using
the wrong one fails verification SILENTLY (the JWT just won't decode) — which is easy
to misdiagnose as "monday isn't sending a real header" rather than "wrong secret." See
../references/integrations-and-automations.md for the full explanation and sources.

  - Custom action/trigger block invocations (Subscribe/Invoke/Run URLs), and
    board-level webhooks registered via the `create_webhook` GraphQL mutation
    -> signed with your app's SIGNING SECRET.
  - App lifecycle events (install/uninstall) and Monetization subscription webhooks
    -> signed with your app's CLIENT SECRET.

Both secrets live in Developer Center -> your app -> General settings -> App credentials.
monday's JWTs are HMAC-signed (HS256) and arrive with a "Bearer " prefix on the
Authorization header (confirmed from a live production integration, not just docs).

Confirmed gotcha: the token carries an `aud` claim, but PyJWT (unlike the Node
`jsonwebtoken` library, which ignores `aud` by default) rejects the token unless you
explicitly pass `options={"verify_aud": False}` or supply a matching `audience=`.
Skipping this makes every request look like an auth failure even though the signature
and secret are both correct — this is the single most common false-negative here.

Requires: pip install pyjwt
"""

from __future__ import annotations

import jwt


class MondayAuthError(Exception):
    """Raised when a request's Authorization JWT fails verification."""


def _decode(auth_header: str | None, secret: str, *, expected_aud: str | None) -> dict:
    if not auth_header:
        raise MondayAuthError("missing Authorization header")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else auth_header
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=expected_aud,
            options={"verify_aud": expected_aud is not None},
        )
    except jwt.PyJWTError as exc:
        raise MondayAuthError(f"JWT verification failed: {exc}") from exc


def verify_action_request(
    auth_header: str | None, signing_secret: str, *, expected_aud: str | None = None
) -> dict:
    """Verify a custom action/trigger invocation, or a create_webhook-registered
    board webhook. Claims: accountId, userId, shortLivedToken, aud, iat, exp.

    `expected_aud` is optional because monday's docs describe `aud` as "your app's
    endpoint URL" without confirming whether that's the specific Run/Subscribe URL or
    your app's base URL — pass it only once you've confirmed the real value against a
    live request, otherwise you risk rejecting genuine requests on a false mismatch.
    """
    return _decode(auth_header, signing_secret, expected_aud=expected_aud)


def verify_lifecycle_webhook(auth_header: str | None, client_secret: str) -> dict:
    """Verify an app-lifecycle or Monetization subscription webhook
    (install/uninstall/app_subscription_*). Do NOT use the Signing Secret here."""
    return _decode(auth_header, client_secret, expected_aud=None)


def extract_short_lived_token(claims: dict) -> str:
    """The `shortLivedToken` claim from a verified action/trigger JWT is directly
    usable as an API bearer credential for 5 minutes, scoped to whatever the acting
    accountId/userId can already do. Not suitable for work that outlives that window
    (e.g. an async action's later callback) — use an OAuth token for that instead."""
    return claims["shortLivedToken"]
