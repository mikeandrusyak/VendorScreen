import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import jwt
import sentry_sdk
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import db
import repository
from monday_service import get_item_column_text, get_item_name, update_vendor_record
from observability import init_sentry
from sanctions_service import (
    RISK_LEVEL,
    SanctionsUnavailableError,
    check_vendor_with_retry,
    unavailable_result,
    with_disclaimer,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vendorscreen")

# APP_ENV is the Python-native name; NODE_ENV is still honored so existing
# Monday Code deployments keep production behavior without re-configuring.
APP_ENV = os.getenv("APP_ENV") or os.getenv("NODE_ENV") or "development"

# Initialize error tracking before the app is created so the ASGI integration
# wraps it. No-op unless SENTRY_DSN is set.
init_sentry(APP_ENV)

# Max 3 concurrent vendor checks — prevents flooding OpenSanctions during bulk imports
CONCURRENCY = 3
vendor_semaphore = asyncio.Semaphore(CONCURRENCY)
# Keep strong references to background tasks so they aren't garbage-collected
background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open the DB pool and apply migrations on startup; no-op when DATABASE_URL
    # is unset. A failure here (Neon unreachable, bad URL) disables usage limits
    # but must NOT take the app down — core screening works without the DB — so
    # we report it and carry on, mirroring the runtime fail-open in
    # process_vendor. Closed on shutdown so connections don't leak between
    # deploys.
    try:
        await db.init_db()
    except Exception as err:
        log.error("[db] startup init failed — usage limits disabled: %s", err)
        sentry_sdk.capture_exception(err)
    yield
    await db.close_db()


app = FastAPI(lifespan=lifespan)


def extract_auth(request: Request):
    """Return the decoded JWT payload (contains shortLivedToken) or None if invalid.

    In dev mode, falls back to the personal MONDAY_API_TOKEN when no
    Authorization header is present.
    """
    auth_header = request.headers.get("authorization")

    if not auth_header:
        if APP_ENV != "production":
            log.warning("[auth] No Authorization header — using MONDAY_API_TOKEN (dev mode)")
            return {"shortLivedToken": os.getenv("MONDAY_API_TOKEN")}
        return None

    token = auth_header.replace("Bearer ", "")
    try:
        # Monday's JWT carries an `aud` claim; the Node `jsonwebtoken` library
        # ignored it by default, but PyJWT rejects the token unless audience
        # verification is explicitly disabled.
        return jwt.decode(
            token,
            os.getenv("MONDAY_SIGNING_SECRET"),
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as err:
        log.error("[auth] JWT verification failed: %s", err)
        return None


def field_value(field, *keys):
    """Unwrap an inboundFieldValues entry that may be a primitive or an object
    wrapper (e.g. {"columnId": "status"}). Returns the first matching key, or
    the value itself when it's already a primitive."""
    if field is None:
        return None
    if not isinstance(field, dict):
        return field
    for key in keys:
        if field.get(key) is not None:
            return field[key]
    return None


# Health check — Monday Code pings HEAD /health; keep / for manual checks.
# FastAPI does not auto-serve HEAD for GET routes, so register both methods.
@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}


# Automation Block action endpoint. Monday calls this when the automation's
# trigger fires ("When an item is created, screen it..."). The board and the
# columns are chosen by the CLIENT in the automation UI and arrive in the
# payload — NOT from our .env — so it works on any client board.
@app.post("/monday/execute_action")
async def execute_action(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Monday URL verification challenge (sent when the action URL is registered)
    if body.get("challenge"):
        return {"challenge": body["challenge"]}

    auth = extract_auth(request)
    if not auth:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    # New monday workflows infra sends `inboundFieldValues`; the older recipe
    # (sentence builder) infra used `inputFields`. Accept both for safety.
    payload = body.get("payload") or {}
    fields = payload.get("inboundFieldValues") or payload.get("inputFields") or {}

    # Column / board / item pickers arrive wrapped in an object
    # (e.g. {"columnId": "status"}), not as a bare string. Unwrap to the id,
    # otherwise the stringified object is rejected by Monday with
    # InvalidColumnIdException.
    board_id = field_value(fields.get("boardId"), "boardId", "id", "value")
    item_id = field_value(fields.get("itemId"), "itemId", "linkedPulseId", "id", "value")
    status_column_id = field_value(fields.get("statusColumnId"), "columnId", "id", "value")
    details_column_id = field_value(fields.get("detailsColumnId"), "columnId", "id", "value")
    # Optional: the client can map a country column to sharpen the /match query
    # and cut false positives. Absent → screen on name alone (prior behavior).
    country_column_id = field_value(fields.get("countryColumnId"), "columnId", "id", "value")
    # Per-account short-lived token from the JWT (dev: MONDAY_API_TOKEN)
    api_token = auth.get("shortLivedToken")
    # Tenant key for usage limits. Present on real Monday JWTs; absent in dev
    # (no signed token) — enforcement is then skipped for that request.
    account_id = auth.get("accountId")

    if not board_id or not item_id or not status_column_id or not details_column_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    "Missing required input fields "
                    "(boardId, itemId, statusColumnId, detailsColumnId)"
                )
            },
        )

    # Enqueue compliance check — max 3 concurrent to avoid rate limiting.
    # Respond immediately: Monday times out if we wait for the full check.
    task = asyncio.create_task(
        process_vendor(
            board_id,
            item_id,
            status_column_id,
            details_column_id,
            api_token,
            account_id=account_id,
            country_column_id=country_column_id,
        )
    )
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    log.info("[queue] pending=%d", len(background_tasks))

    return {}


async def process_vendor(
    board_id,
    item_id,
    status_column_id,
    details_column_id,
    api_token,
    account_id=None,
    country_column_id=None,
):
    async with vendor_semaphore:
        try:
            # Enforce the account's monthly quota before doing any paid work
            # (the OpenSanctions call). Skipped when the DB is disabled or the
            # request has no account (dev). A DB error never blocks screening —
            # we log it and fall through rather than fail closed.
            if account_id and db.is_configured():
                try:
                    quota = await repository.check_quota(account_id)
                except Exception as quota_err:
                    log.error(
                        "[quota] check failed for account %s: %s — allowing screening",
                        account_id,
                        quota_err,
                    )
                    sentry_sdk.capture_exception(quota_err)
                    quota = None

                if quota is not None and not quota.allowed:
                    log.info(
                        "[quota] account %s over limit (%d/%d) — skipping item %s",
                        account_id,
                        quota.used,
                        quota.limit,
                        item_id,
                    )
                    await update_vendor_record(
                        board_id=board_id,
                        item_id=item_id,
                        status_column_id=status_column_id,
                        details_column_id=details_column_id,
                        risk_level=RISK_LEVEL["UNAVAILABLE"],
                        details=with_disclaimer(
                            f"Monthly screening limit reached for the {quota.plan} plan "
                            f"({quota.limit}/month). This item was not screened — upgrade "
                            "your plan or wait until the next period."
                        ),
                        api_token=api_token,
                    )
                    return

            vendor_name = await get_item_name(item_id, api_token)
            if not vendor_name:
                log.error("[vendor] Could not resolve name for item %s — skipping", item_id)
                return

            # Optional country refinement. A failure here must not abort the
            # screening — fall back to name-only rather than losing the check.
            country = None
            if country_column_id:
                try:
                    country = await get_item_column_text(item_id, country_column_id, api_token)
                except Exception as country_err:
                    log.warning(
                        "[vendor] Could not read country for item %s: %s — screening on name only",
                        item_id,
                        country_err,
                    )

            log.info(
                '[vendor] Checking: "%s" (country=%s, item %s, board %s)',
                vendor_name,
                country or "-",
                item_id,
                board_id,
            )

            result = await check_vendor_with_retry(vendor_name, country)

            log.info('[vendor] Result for "%s": %s', vendor_name, result["riskLevel"])

            await update_vendor_record(
                board_id=board_id,
                item_id=item_id,
                status_column_id=status_column_id,
                details_column_id=details_column_id,
                risk_level=result["riskLevel"],
                details=result["details"],
                api_token=api_token,
            )

            log.info("[vendor] Monday.com updated for item %s", item_id)
        except SanctionsUnavailableError as err:
            # Screening could not run — don't lose it silently. Mark the board so
            # the client sees the check needs a re-run instead of a blank status.
            # Reported to Sentry too so we can track OpenSanctions outages.
            log.error("[vendor] OpenSanctions unavailable for item %s: %s", item_id, err)
            sentry_sdk.capture_exception(err)
            result = unavailable_result()
            try:
                await update_vendor_record(
                    board_id=board_id,
                    item_id=item_id,
                    status_column_id=status_column_id,
                    details_column_id=details_column_id,
                    risk_level=result["riskLevel"],
                    details=result["details"],
                    api_token=api_token,
                )
                log.info("[vendor] Marked item %s as '%s'", item_id, result["riskLevel"])
            except Exception as update_err:
                log.error(
                    "[vendor] Could not write unavailable status for item %s: %s",
                    item_id,
                    update_err,
                )
                sentry_sdk.capture_exception(update_err)
        except Exception as err:
            log.error("[vendor] Failed to process item %s: %s", item_id, err)
            sentry_sdk.capture_exception(err)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    log.info("VendorScreen listening on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
