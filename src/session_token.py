import os

import jwt

# Auth for the client-side Board view. A board view renders in an iframe inside
# monday and calls this backend with the JWT from `monday.get('sessionToken')`.
# That token is signed with the app's CLIENT SECRET (distinct from the Signing
# Secret used to verify recipe action/trigger requests — mixing the two up fails
# silently), and carries the acting account/user. We verify the signature and
# read accountId to scope the export to that tenant.


def _secret() -> str:
    return os.getenv("MONDAY_CLIENT_SECRET") or ""


def verify(token: str):
    """Return {"account_id", "user_id"} for a valid monday session token, else None.

    None on any failure — bad signature, expiry, or a missing secret — so callers
    treat every failure identically as an unauthenticated request. `aud` is not
    enforced (monday's session-token audience isn't reliably documented), matching
    how the action JWT is verified."""
    secret = _secret()
    if not secret or not token:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})
    except jwt.PyJWTError:
        return None

    # monday nests the identifiers under a `dat` claim on the session token;
    # fall back to top-level claims for robustness across token shapes.
    data = payload.get("dat") if isinstance(payload.get("dat"), dict) else payload
    account_id = data.get("account_id") or data.get("accountId") or payload.get("accountId")
    user_id = data.get("user_id") or data.get("userId") or payload.get("userId")
    if account_id is None:
        return None
    return {"account_id": account_id, "user_id": user_id}
