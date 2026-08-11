import asyncio
import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

import jwt
import sentry_sdk
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

import db
import export_token
import repository
import session_token
from monday_service import (
    create_notification,
    get_item_board_id,
    get_item_column_text,
    get_item_name,
    update_vendor_record,
)
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

# Static assets for the client-side Board view (served at /view).
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Gates auth enforcement: "production" => a valid JWT is required on every action.
# Read from the NODE_ENV env var (already set on the monday Code deploy) and
# defaults to "production" (fail-closed) — an unset or misspelled value must never
# silently disable JWT verification on a live deploy. Local dev opts out with
# NODE_ENV=development.
NODE_ENV = os.getenv("NODE_ENV") or "production"

# Initialize error tracking before the app is created so the ASGI integration
# wraps it. No-op unless SENTRY_DSN is set.
init_sentry(NODE_ENV)

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
        if NODE_ENV != "production":
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

    # Some triggers (e.g. "When button clicked") don't reliably pass boardId
    # as a context variable the way "When item created" does. Fall back to
    # resolving it from the item itself rather than failing the whole run.
    if not board_id and item_id:
        try:
            board_id = await get_item_board_id(item_id, api_token)
        except Exception as err:
            log.error("[action] Could not resolve board id for item %s: %s", item_id, err)
    # Tenant key for usage limits. Present on real Monday JWTs; absent in dev
    # (no signed token) — enforcement is then skipped for that request.
    account_id = auth.get("accountId")
    # User who owns the automation — the recipient of Critical-risk alerts.
    # Absent in dev (no signed token); alerting is then skipped.
    user_id = auth.get("userId")

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
            user_id=user_id,
        )
    )
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    log.info("[queue] pending=%d", len(background_tasks))

    return {}


# monday.com Monetization webhook — fired when a customer subscribes, changes,
# renews, or cancels a paid plan (Developer Center → Monetization →
# subscription webhook URL). Keeps accounts.plan in sync with the plan the
# customer is actually paying for, so repository.check_quota enforces the
# right allowance without any manual DB edits.
#
# monday's exact event `type` string isn't pinned down here — matched
# defensively by substring so an exact name mismatch fails open (ignored,
# logged) instead of crashing. Verify against real webhook deliveries during
# setup and tighten the match if needed.
@app.post("/monday/subscription_webhook")
async def subscription_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    # URL verification challenge, same handshake as execute_action.
    if body.get("challenge"):
        return {"challenge": body["challenge"]}

    if NODE_ENV == "production" and extract_auth(request) is None:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    event_type = (body.get("type") or "").lower()
    data = body.get("data") or {}
    account_id = data.get("account_id")

    if not account_id or "subscription" not in event_type:
        return {}

    subscription = data.get("subscription")
    if "cancel" in event_type or not subscription:
        plan = repository.DEFAULT_PLAN
    else:
        # Plan ids configured in the Developer Center Monetization tab must
        # match PLAN_LIMITS keys (see repository.py) — no translation layer.
        plan = subscription.get("plan_id") or repository.DEFAULT_PLAN
        if plan not in repository.PLAN_LIMITS:
            log.warning(
                "[subscription] unknown monday plan_id %r for account %s — defaulting to %s",
                plan,
                account_id,
                repository.DEFAULT_PLAN,
            )
            plan = repository.DEFAULT_PLAN

    try:
        await repository.set_plan(account_id, plan)
        log.info("[subscription] account %s -> plan %s (event %s)", account_id, plan, event_type)
    except Exception as err:
        log.error("[subscription] failed to update plan for account %s: %s", account_id, err)
        sentry_sdk.capture_exception(err)

    return {}


def _url_origin(value):
    """Return scheme://host for an http(s) URL string, else None."""
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def public_base_url(request, auth=None):
    """The customer-facing base URL used to build the tokenized download link.

    On monday Code the app runs behind a proxy on Cloud Run, so request.base_url
    resolves to the internal *.a.run.app host (over http), and PUBLIC_BASE_URL is
    no good either — env vars are shared across the draft and live deployments, so
    a single value can't be right for both. The monday action JWT's `aud` claim is
    the public endpoint URL monday actually called, so it yields the correct host
    per deployment automatically. Resolve in order: an explicit PUBLIC_BASE_URL
    override, then the `aud` origin, then the proxy's forwarded host/proto, then
    request.base_url (correct for local dev where there's no proxy)."""
    configured = os.getenv("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/")
    if auth:
        aud = auth.get("aud")
        for candidate in aud if isinstance(aud, list) else [aud]:
            origin = _url_origin(candidate)
            if origin:
                return origin
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        proto = request.headers.get("x-forwarded-proto", "https")
        return f"{proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


# Audit-log export, request half (P1). A recipe action the customer runs from
# monday (e.g. a board button "Export screening audit"). It mints a short-lived
# signed token and DMs the customer a download link via a monday notification —
# the token is the one-time credential, so the browser download needs no session.
# The export is scoped to the account in the JWT; the mapped item is only the
# notification's anchor. See export_token.py for why this shape.
@app.post("/monday/export_action")
async def export_action(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    if body.get("challenge"):
        return {"challenge": body["challenge"]}

    auth = extract_auth(request)
    if not auth:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    account_id = auth.get("accountId")
    user_id = auth.get("userId")
    api_token = auth.get("shortLivedToken")

    payload = body.get("payload") or {}
    fields = payload.get("inboundFieldValues") or payload.get("inputFields") or {}
    item_id = field_value(fields.get("itemId"), "itemId", "linkedPulseId", "id", "value")

    # Export is per-tenant; without a signed account there's nothing to scope to.
    if not account_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Export requires an authenticated monday account"},
        )
    # We deliver the link by notifying the user against the triggering item.
    if not user_id or not item_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Export requires a userId and an itemId to notify"},
        )

    token = export_token.issue(account_id)
    # Build the link from the public host (see public_base_url) — request.base_url
    # alone points at the internal Cloud Run host behind monday's proxy.
    link = f"{public_base_url(request, auth)}/audit/export?token={token}"
    text = (
        f"Your VendorScreen screening audit export is ready. Download it within 15 minutes: {link}"
    )

    try:
        await create_notification(user_id, item_id, text, api_token)
        log.info("[export] audit link sent to user %s (account %s)", user_id, account_id)
    except Exception as err:
        log.error("[export] failed to notify user %s: %s", user_id, err)
        sentry_sdk.capture_exception(err)
        return JSONResponse(
            status_code=502, content={"error": "Could not send the export notification"}
        )

    return {}


AUDIT_COLUMNS = (
    "created_at",
    "board_id",
    "item_id",
    "vendor_name",
    "country",
    "risk_level",
    "match_type",
    "score",
    "match_id",
    "match_caption",
)


def _audit_row(event):
    """Serialize one screening event to a JSON-safe dict (created_at -> ISO str)."""
    created = event["created_at"]
    row = dict(event)
    row["created_at"] = created.isoformat() if hasattr(created, "isoformat") else created
    return {col: row.get(col) for col in AUDIT_COLUMNS}


def _audit_csv_response(events, filename):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(AUDIT_COLUMNS)
    for event in events:
        row = _audit_row(event)
        writer.writerow([row[col] for col in AUDIT_COLUMNS])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Audit-log export, download half (P1). Reached by the tokenized link from the
# notification above — the token both authenticates and scopes the download to a
# single account, so this route needs no monday JWT. Streams CSV.
@app.get("/audit/export")
async def audit_export(token: str = ""):
    account_id = export_token.verify(token)
    if account_id is None:
        return PlainTextResponse("Invalid or expired export link.", status_code=401)

    events = await repository.list_events(account_id)
    return _audit_csv_response(events, f"vendorscreen-audit-{account_id}.csv")


# --- Board view (client-side feature) ---------------------------------------
# A board view renders our iframe UI (served at /view) inside monday. It reads
# the board's screening audit as a table and offers a native CSV download. Both
# data routes authenticate the caller with the monday session token (verified
# with the app's Client Secret) and scope results to the token's account plus the
# requested board — so a tenant only ever sees its own account's data.


def _board_view_auth(request):
    """Return (account_id, session) for a valid session token, else (None, None)."""
    header = request.headers.get("authorization") or ""
    token = header.replace("Bearer ", "", 1) or request.query_params.get("sessionToken", "")
    session = session_token.verify(token)
    if session is None:
        return None, None
    return session["account_id"], session


@app.get("/view")
async def board_view():
    """Serve the board-view frontend (static HTML that loads monday-sdk-js)."""
    return FileResponse(os.path.join(STATIC_DIR, "board_view.html"), media_type="text/html")


@app.get("/view/audit.json")
async def board_view_audit_json(request: Request, boardId: str = ""):
    account_id, _ = _board_view_auth(request)
    if account_id is None:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    if not boardId:
        return JSONResponse(status_code=400, content={"error": "boardId is required"})

    events = await repository.list_events(account_id, board_id=boardId)
    return {"rows": [_audit_row(e) for e in events]}


@app.get("/view/audit.csv")
async def board_view_audit_csv(request: Request, boardId: str = ""):
    account_id, _ = _board_view_auth(request)
    if account_id is None:
        return PlainTextResponse("Unauthorized", status_code=401)
    if not boardId:
        return PlainTextResponse("boardId is required", status_code=400)

    events = await repository.list_events(account_id, board_id=boardId)
    return _audit_csv_response(events, f"vendorscreen-audit-board-{boardId}.csv")


async def _record_audit(account_id, board_id, item_id, vendor_name, result, country=None):
    """Append a screening outcome to the audit log (P1). Scoped to a real tenant:
    skipped when the DB is off or the request has no account (dev), matching how
    the export endpoint is scoped by account. Fail-open — an audit write must
    never break or block a screening that already reached the board."""
    if not (account_id and db.is_configured()):
        return
    try:
        await repository.record_event(
            account_id=account_id,
            board_id=board_id,
            item_id=item_id,
            vendor_name=vendor_name,
            risk_level=result["riskLevel"],
            score=result.get("score"),
            match_id=result.get("matchId"),
            match_caption=result.get("matchCaption"),
            country=country,
            match_type=result.get("matchType"),
        )
    except Exception as err:
        log.error("[audit] failed to record event for item %s: %s", item_id, err)
        sentry_sdk.capture_exception(err)


async def _alert_critical(user_id, item_id, vendor_name, result, api_token):
    """Send a monday notification to the automation owner when a vendor screens
    as Critical (P1). Only Critical fires an alert — Clear/Warning stay silent so
    the bell isn't noise. Needs a userId (the alert recipient); absent in dev, so
    alerting is then skipped. Fail-open — a notification failure must never break
    or block a screening that already reached the board."""
    if not user_id or result["riskLevel"] != RISK_LEVEL["CRITICAL"]:
        return
    text = (
        f"⚠️ VendorScreen: '{vendor_name}' screened as CRITICAL and needs review. "
        f"{result['details']}"
    )
    try:
        await create_notification(user_id, item_id, text, api_token)
        log.info("[alert] Critical alert sent to user %s for item %s", user_id, item_id)
    except Exception as err:
        log.error("[alert] failed to notify user %s for item %s: %s", user_id, item_id, err)
        sentry_sdk.capture_exception(err)


async def _mark_unavailable(
    board_id, item_id, status_column_id, details_column_id, api_token, account_id, vendor_name
):
    """Write the fail-safe 'Screening Failed' status so an item is never left
    blank after an error, whatever the cause. Never raises — a failure here is
    logged/reported rather than propagated, since the caller is already in its
    own error-handling path."""
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
        await _record_audit(account_id, board_id, item_id, vendor_name, result)
    except Exception as update_err:
        log.error("[vendor] Could not write failure status for item %s: %s", item_id, update_err)
        sentry_sdk.capture_exception(update_err)


async def process_vendor(
    board_id,
    item_id,
    status_column_id,
    details_column_id,
    api_token,
    account_id=None,
    country_column_id=None,
    user_id=None,
):
    async with vendor_semaphore:
        vendor_name = None
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
                    # Record the skip too — the audit trail should show the item
                    # was received but not screened because the quota was spent.
                    await _record_audit(
                        account_id,
                        board_id,
                        item_id,
                        None,
                        {"riskLevel": RISK_LEVEL["UNAVAILABLE"]},
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
            await _record_audit(account_id, board_id, item_id, vendor_name, result, country=country)
            await _alert_critical(user_id, item_id, vendor_name, result, api_token)
        except SanctionsUnavailableError as err:
            # OpenSanctions itself is down/rate-limited after retries. Mark the
            # board so the client sees the check needs a re-run instead of a
            # blank status. Reported to Sentry so we can track outages.
            log.error("[vendor] OpenSanctions unavailable for item %s: %s", item_id, err)
            sentry_sdk.capture_exception(err)
            await _mark_unavailable(
                board_id,
                item_id,
                status_column_id,
                details_column_id,
                api_token,
                account_id,
                vendor_name,
            )
        except Exception as err:
            # Anything else unexpected (a non-retryable OpenSanctions error, a
            # monday GraphQL failure resolving the item, a bug) — same fail-safe
            # applies: never leave the board blank, whatever the cause.
            log.error("[vendor] Failed to process item %s: %s", item_id, err)
            sentry_sdk.capture_exception(err)
            await _mark_unavailable(
                board_id,
                item_id,
                status_column_id,
                details_column_id,
                api_token,
                account_id,
                vendor_name,
            )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    log.info("VendorScreen listening on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
