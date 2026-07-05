import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

import jwt
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from monday_service import get_item_name, update_vendor_record
from sanctions_service import check_vendor_with_retry

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vendorscreen")

# APP_ENV is the Python-native name; NODE_ENV is still honored so existing
# Monday Code deployments keep production behavior without re-configuring.
APP_ENV = os.getenv("APP_ENV") or os.getenv("NODE_ENV") or "development"

# Max 3 concurrent vendor checks — prevents flooding OpenSanctions during bulk imports
CONCURRENCY = 3
vendor_semaphore = asyncio.Semaphore(CONCURRENCY)
# Keep strong references to background tasks so they aren't garbage-collected
background_tasks: set[asyncio.Task] = set()

app = FastAPI()


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
        return jwt.decode(token, os.getenv("MONDAY_SIGNING_SECRET"), algorithms=["HS256"])
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


# Health check — Monday Code / monitoring pings this
@app.get("/")
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
    # Per-account short-lived token from the JWT (dev: MONDAY_API_TOKEN)
    api_token = auth.get("shortLivedToken")

    if not board_id or not item_id or not status_column_id or not details_column_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Missing required input fields (boardId, itemId, statusColumnId, detailsColumnId)"
            },
        )

    # Enqueue compliance check — max 3 concurrent to avoid rate limiting.
    # Respond immediately: Monday times out if we wait for the full check.
    task = asyncio.create_task(
        process_vendor(board_id, item_id, status_column_id, details_column_id, api_token)
    )
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    log.info("[queue] pending=%d", len(background_tasks))

    return {}


async def process_vendor(board_id, item_id, status_column_id, details_column_id, api_token):
    async with vendor_semaphore:
        try:
            vendor_name = await get_item_name(item_id, api_token)
            if not vendor_name:
                log.error("[vendor] Could not resolve name for item %s — skipping", item_id)
                return

            log.info('[vendor] Checking: "%s" (item %s, board %s)', vendor_name, item_id, board_id)

            result = await check_vendor_with_retry(vendor_name)

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
        except Exception as err:
            log.error("[vendor] Failed to process item %s: %s", item_id, err)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    log.info("VendorScreen AI listening on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
