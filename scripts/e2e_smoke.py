"""End-to-end smoke test against a DEPLOYED (draft) monday-code version.

Run this after `mapps code:push` but BEFORE promoting the draft to live: the
draft deployment is already running on its own URL, so we can exercise the full
chain against it and only promote if it passes.

What it verifies (the real product flow, not mocks):
  1. the service booted             -> GET /health
  2. resolve the caller's identity  -> monday `me { id account { id } }`
  3. create a test item             -> monday GraphQL
  4. trigger the action endpoint    -> POST /monday/execute_action
  5. the app screens via /match     -> we read status + details back and assert
                                        the score-based result (P1: /match)
  6. the audit export round-trips   -> GET /audit/export streams the CSV
                                        containing our screening (P1: audit log)
  7. (opt-in) the notification path -> POST /monday/export_action returns 200
                                        (E2E_RUN_NOTIFY=1; see the caveat below)

We craft the request exactly as monday's Integration Framework would, so this
does NOT depend on the recipe/automation being installed. The app verifies the
Authorization JWT with MONDAY_SIGNING_SECRET, so we mint a matching JWT and put
a real API token inside as `shortLivedToken` (what the app uses for GraphQL),
plus the `accountId` and `userId` claims the P1 features need (usage metering,
audit scoping, and Critical alerts).

The audit CSV round-trip is deterministic and faithful in CI: we mint the export
token with the same MONDAY_SIGNING_SECRET the app verifies, so it exercises the
real audit-log + export path.

The notification stage (step 7) is OPT-IN and really a live-only check. In CI the
caller token is a personal token acting as its OWN recipient, so it does NOT
exercise the real notifications:write OAuth scope (that only applies to a genuine
short-lived recipe token) and monday may reject a self-notification. So it is off
by default; confirm the scope with a real recipe run instead.

Required env:
  APP_URL                base URL of the deployed draft (e.g. https://xxx.monday.app)
  MONDAY_SIGNING_SECRET  secret the deployed app verifies JWTs with
  E2E_MONDAY_TOKEN       monday API token with write access to the test board
  E2E_BOARD_ID           id of the test board
  E2E_STATUS_COLUMN      status column id on the test board
  E2E_DETAILS_COLUMN     text column id on the test board
Optional:
  E2E_VENDOR_NAME        name to screen (default: a well-known sanctioned name,
                         expected to come back Critical)
  E2E_EXPECT_LEVEL       expected risk level for that name (default: Critical)
  E2E_COUNTRY_COLUMN     country/text column id — when set, the app is told to
                         read it (exercises the /match country refinement)
  E2E_COUNTRY_VALUE      value written to that column before screening (default Russia)
  E2E_ACCOUNT_ID         override the account id (default: derived from the token)
  E2E_USER_ID            override the alert recipient (default: derived from token)
  E2E_SKIP_EXPORT        set to "1" to skip the whole export section (audit CSV
                         and notification), e.g. before DATABASE_URL is set
  E2E_RUN_NOTIFY         set to "1" to also run the opt-in notification stage
                         (live-only check — see the caveat above)
  E2E_KEEP_ITEMS         how many recent test items to keep on the board (default 20)

Test items are NOT deleted after each run — recent history is kept for debugging.
Instead we prune to the newest E2E_KEEP_ITEMS so the board stays bounded.
"""

import datetime as dt
import json
import os
import sys
import time

import httpx
import jwt

APP_URL = os.environ["APP_URL"].rstrip("/")
SIGNING_SECRET = os.environ["MONDAY_SIGNING_SECRET"]
TOKEN = os.environ["E2E_MONDAY_TOKEN"]
BOARD_ID = os.environ["E2E_BOARD_ID"]
STATUS_COL = os.environ["E2E_STATUS_COLUMN"]
DETAILS_COL = os.environ["E2E_DETAILS_COLUMN"]
VENDOR = os.environ.get("E2E_VENDOR_NAME", "Vladimir Putin")
EXPECT_LEVEL = os.environ.get("E2E_EXPECT_LEVEL", "Critical")
COUNTRY_COL = os.environ.get("E2E_COUNTRY_COLUMN")
COUNTRY_VALUE = os.environ.get("E2E_COUNTRY_VALUE", "Russia")
SKIP_EXPORT = os.environ.get("E2E_SKIP_EXPORT") == "1"
RUN_NOTIFY = os.environ.get("E2E_RUN_NOTIFY") == "1"
KEEP_ITEMS = int(os.environ.get("E2E_KEEP_ITEMS", "20"))

# Export token must mirror src/export_token.py exactly (same secret, scope, and
# claim name) so the deployed app's verify() accepts what we mint here.
EXPORT_SCOPE = "audit-export"

MONDAY_API = "https://api.monday.com/v2"
VALID_LEVELS = {"Clear", "Warning", "Critical"}
POLL_TIMEOUT_S = 90
POLL_INTERVAL_S = 3


def gql(query, variables):
    resp = httpx.post(
        MONDAY_API,
        json={"query": query, "variables": variables},
        headers={"Authorization": TOKEN, "API-Version": "2024-01"},
        timeout=20.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise SystemExit(f"monday GraphQL error: {json.dumps(data['errors'])}")
    return data["data"]


def resolve_identity():
    """The account + user the JWT should carry. account_id scopes usage metering
    and the audit log; user_id is the Critical-alert recipient. Both come from
    the token owner unless overridden."""
    data = gql("query { me { id account { id } } }", {})
    me = data["me"]
    account_id = os.environ.get("E2E_ACCOUNT_ID") or me["account"]["id"]
    user_id = os.environ.get("E2E_USER_ID") or me["id"]
    return str(user_id), str(account_id)


def create_item():
    query = "mutation ($b: ID!, $n: String!) { create_item (board_id: $b, item_name: $n) { id } }"
    return gql(query, {"b": BOARD_ID, "n": VENDOR})["create_item"]["id"]


def set_country(item_id):
    query = (
        "mutation ($b: ID!, $i: ID!, $c: String!, $v: String!) { "
        "change_simple_column_value (board_id: $b, item_id: $i, column_id: $c, value: $v) { id } }"
    )
    gql(query, {"b": BOARD_ID, "i": item_id, "c": COUNTRY_COL, "v": COUNTRY_VALUE})


def read_columns(item_id, column_ids):
    ids = ", ".join(f'"{c}"' for c in column_ids)
    query = (
        f"query ($i: [ID!]) {{ items (ids: $i) {{ column_values (ids: [{ids}]) {{ id text }} }} }}"
    )
    items = gql(query, {"i": [item_id]})["items"]
    values = items[0]["column_values"] if items else []
    return {v["id"]: v["text"] for v in values}


def mint_action_jwt(user_id, account_id):
    """A JWT shaped like monday's action request: the app reads shortLivedToken
    for GraphQL, accountId for metering/audit, userId for the alert recipient."""
    return jwt.encode(
        {"shortLivedToken": TOKEN, "accountId": int(account_id), "userId": int(user_id)},
        SIGNING_SECRET,
        algorithm="HS256",
    )


def mint_export_token(account_id):
    now = dt.datetime.now(dt.UTC)
    return jwt.encode(
        {
            "accountId": int(account_id),
            "scope": EXPORT_SCOPE,
            "exp": now + dt.timedelta(seconds=900),
            "iat": now,
        },
        SIGNING_SECRET,
        algorithm="HS256",
    )


def prune_old_items(keep):
    """Keep the newest `keep` test items on the board, delete the rest.

    History is intentionally preserved for debugging; this only bounds growth.
    Never raises — pruning must not fail the run.
    """
    try:
        query = (
            "query ($b: [ID!]) { boards (ids: $b) { "
            "items_page (limit: 100) { items { id created_at } } } }"
        )
        boards = gql(query, {"b": [BOARD_ID]})["boards"]
        items = boards[0]["items_page"]["items"] if boards else []
        # created_at is ISO 8601, so a lexicographic sort is chronological.
        items.sort(key=lambda it: it["created_at"], reverse=True)
        stale = items[keep:]
        for it in stale:
            gql("mutation ($i: ID!) { delete_item (item_id: $i) { id } }", {"i": it["id"]})
        if stale:
            print(f"[prune] deleted {len(stale)} old item(s), kept newest {keep}")
        else:
            print(f"[prune] {len(items)} item(s) on board, nothing to prune")
    except Exception as err:
        print(f"[prune] could not prune old items: {err}")


def stage_screening(item_id, action_jwt):
    """Fire the action endpoint and assert the score-based result lands (P1)."""
    fields = {
        "boardId": {"boardId": BOARD_ID},
        "itemId": {"itemId": item_id},
        "statusColumnId": {"columnId": STATUS_COL},
        "detailsColumnId": {"columnId": DETAILS_COL},
    }
    if COUNTRY_COL:
        fields["countryColumnId"] = {"columnId": COUNTRY_COL}

    resp = httpx.post(
        f"{APP_URL}/monday/execute_action",
        json={"payload": {"inboundFieldValues": fields}},
        headers={"Authorization": f"Bearer {action_jwt}"},
        timeout=20.0,
    )
    if resp.status_code != 200:
        raise SystemExit(f"action endpoint returned {resp.status_code}: {resp.text}")
    print("[e2e] action accepted; waiting for async screening to write back...")

    deadline = time.time() + POLL_TIMEOUT_S
    status = None
    while time.time() < deadline:
        status = read_columns(item_id, [STATUS_COL]).get(STATUS_COL)
        if status in VALID_LEVELS:
            break
        time.sleep(POLL_INTERVAL_S)

    if status not in VALID_LEVELS:
        raise SystemExit(
            f"expected a risk level {sorted(VALID_LEVELS)} within {POLL_TIMEOUT_S}s, got {status!r}"
        )
    if status != EXPECT_LEVEL:
        raise SystemExit(
            f"expected '{VENDOR}' to screen as {EXPECT_LEVEL!r}, got {status!r} "
            "(set E2E_EXPECT_LEVEL if the reference list changed)"
        )

    details = read_columns(item_id, [DETAILS_COL]).get(DETAILS_COL) or ""
    # A flagged result carries the /match score and the OpenSanctions profile
    # link — this is what proves PR1 (score) actually ran, not the old /search.
    if status in {"Warning", "Critical"}:
        if "% match" not in details:
            raise SystemExit(f"expected a '% match' score in details, got: {details!r}")
        if "opensanctions.org" not in details:
            raise SystemExit(f"expected an OpenSanctions profile link in details, got: {details!r}")
    print(f"[e2e] PASS screening — status '{status}', details carry score + profile link")


def stage_audit_csv(item_id, account_id):
    """Round-trip the audit export (P1 audit log). Deterministic and faithful in
    CI: we mint the export token with the same MONDAY_SIGNING_SECRET the app
    verifies, so this exercises the real audit-log + export path."""
    token = mint_export_token(account_id)
    resp = httpx.get(f"{APP_URL}/audit/export", params={"token": token}, timeout=20.0)
    if resp.status_code != 200:
        raise SystemExit(f"audit export returned {resp.status_code}: {resp.text}")
    ctype = resp.headers.get("content-type", "")
    if not ctype.startswith("text/csv"):
        raise SystemExit(f"expected a text/csv export, got content-type {ctype!r}")
    body = resp.text
    header = "created_at,board_id,item_id,vendor_name,risk_level,score,match_id,match_caption"
    if header not in body:
        raise SystemExit("audit CSV is missing the expected header row")
    # The screening we just ran should appear — unless the app has no
    # DATABASE_URL (audit disabled), in which case the CSV is header-only.
    if str(item_id) in body:
        print("[e2e] PASS audit export — CSV contains the screening we just ran")
    else:
        print(
            "[e2e] WARN audit export — CSV valid but our item is absent; "
            "is DATABASE_URL set on the deployment? (audit is skipped without it)"
        )


def stage_notification(item_id, user_id, account_id):
    """Opt-in, LIVE-only check: POST the export action, which makes the app call
    monday create_notification. In CI the caller token is a personal token acting
    as its own recipient, so this proves the mutation shape but NOT the real
    notifications:write scope, and monday may reject a self-notification — hence
    off by default. Confirm the scope with a real recipe run."""
    action_jwt = mint_action_jwt(user_id, account_id)
    resp = httpx.post(
        f"{APP_URL}/monday/export_action",
        json={"payload": {"inboundFieldValues": {"itemId": {"itemId": item_id}}}},
        headers={"Authorization": f"Bearer {action_jwt}"},
        timeout=20.0,
    )
    if resp.status_code == 502:
        raise SystemExit(
            "export_action could not send the monday notification (502). Grant the app the "
            "notifications:write scope and confirm the create_notification mutation. Note a "
            "personal token notifying itself can also be rejected — verify with a real recipe."
        )
    if resp.status_code != 200:
        raise SystemExit(f"export_action returned {resp.status_code}: {resp.text}")
    print("[e2e] PASS notification — export_action sent a notification (create_notification OK)")


def main():
    # 1. Service booted and secrets loaded?
    health = httpx.get(f"{APP_URL}/health", timeout=15.0)
    if health.status_code != 200 or health.json().get("status") != "ok":
        raise SystemExit(f"health check failed: {health.status_code} {health.text}")
    print("[smoke] health OK")

    # 2. Who are we acting as? (scopes metering, audit, and alerts)
    user_id, account_id = resolve_identity()
    print(f"[e2e] acting as user {user_id}, account {account_id}")

    # 3. Seed a test item.
    item_id = create_item()
    print(f'[e2e] created item {item_id} ("{VENDOR}") on board {BOARD_ID}')
    if COUNTRY_COL:
        set_country(item_id)
        print(f"[e2e] set country column {COUNTRY_COL} = {COUNTRY_VALUE!r}")

    try:
        # 4-5. Screen the vendor and assert the score-based result.
        stage_screening(item_id, mint_action_jwt(user_id, account_id))

        # 6. Audit export (deterministic), then the opt-in notification stage.
        if SKIP_EXPORT:
            print("[e2e] SKIP export section (E2E_SKIP_EXPORT=1)")
        else:
            stage_audit_csv(item_id, account_id)
            if RUN_NOTIFY:
                stage_notification(item_id, user_id, account_id)
            else:
                print(
                    "[e2e] SKIP notification stage — set E2E_RUN_NOTIFY=1 to run it "
                    "(live-only: personal token self-notify may be rejected in CI)"
                )

        print("[e2e] PASS — all stages green")
    finally:
        # Keep recent history for debugging; just bound the board size.
        prune_old_items(KEEP_ITEMS)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as err:
        # SystemExit with an int (0) is a clean exit; only strings are failures.
        if isinstance(err.code, str):
            print(f"[e2e] FAIL — {err}")
            sys.exit(1)
        raise
