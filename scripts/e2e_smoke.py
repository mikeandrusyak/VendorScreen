"""End-to-end smoke test against a DEPLOYED (draft) monday-code version.

Run this after `mapps code:push` but BEFORE promoting the draft to live: the
draft deployment is already running on its own URL, so we can exercise the full
chain against it and only promote if it passes.

What it verifies (the real product flow, not mocks):
  1. the service booted            -> GET /health
  2. create a test item            -> monday GraphQL
  3. trigger the action endpoint   -> POST /monday/execute_action
  4. the app screens the vendor    -> (app calls OpenSanctions itself)
  5. the app writes the result     -> we read the status column back

We craft the request exactly as monday's Integration Framework would, so this
does NOT depend on the recipe/automation being installed. The app verifies the
Authorization JWT with MONDAY_SIGNING_SECRET, so we mint a matching JWT and put
a real API token inside as `shortLivedToken` (what the app uses for GraphQL).

Required env:
  APP_URL                base URL of the deployed draft (e.g. https://xxx.monday.app)
  MONDAY_SIGNING_SECRET  secret the deployed app verifies JWTs with
  E2E_MONDAY_TOKEN       monday API token with write access to the test board
  E2E_BOARD_ID           id of the test board
  E2E_STATUS_COLUMN      status column id on the test board
  E2E_DETAILS_COLUMN     text column id on the test board
Optional:
  E2E_VENDOR_NAME        name to screen (default: a well-known sanctioned/PEP name)
  E2E_KEEP_ITEMS         how many recent test items to keep on the board (default 20)

Test items are NOT deleted after each run — recent history is kept for debugging.
Instead we prune to the newest E2E_KEEP_ITEMS so the board stays bounded.
"""

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
KEEP_ITEMS = int(os.environ.get("E2E_KEEP_ITEMS", "20"))

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


def create_item():
    query = "mutation ($b: ID!, $n: String!) { create_item (board_id: $b, item_name: $n) { id } }"
    return gql(query, {"b": BOARD_ID, "n": VENDOR})["create_item"]["id"]


def read_status(item_id):
    query = (
        "query ($i: [ID!]) { items (ids: $i) { "
        f'column_values (ids: ["{STATUS_COL}"]) {{ text }} }} }}'
    )
    items = gql(query, {"i": [item_id]})["items"]
    values = items[0]["column_values"] if items else []
    return values[0]["text"] if values else None


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


def main():
    # 1. Service booted and secrets loaded?
    health = httpx.get(f"{APP_URL}/health", timeout=15.0)
    if health.status_code != 200 or health.json().get("status") != "ok":
        raise SystemExit(f"health check failed: {health.status_code} {health.text}")
    print("[smoke] health OK")

    # 2. Seed a test item.
    item_id = create_item()
    print(f'[e2e] created item {item_id} ("{VENDOR}") on board {BOARD_ID}')

    try:
        # 3. Call the action endpoint exactly like monday would.
        auth = jwt.encode({"shortLivedToken": TOKEN}, SIGNING_SECRET, algorithm="HS256")
        payload = {
            "payload": {
                "inboundFieldValues": {
                    "boardId": {"boardId": BOARD_ID},
                    "itemId": {"itemId": item_id},
                    "statusColumnId": {"columnId": STATUS_COL},
                    "detailsColumnId": {"columnId": DETAILS_COL},
                }
            }
        }
        resp = httpx.post(
            f"{APP_URL}/monday/execute_action",
            json=payload,
            headers={"Authorization": f"Bearer {auth}"},
            timeout=20.0,
        )
        if resp.status_code != 200:
            raise SystemExit(f"action endpoint returned {resp.status_code}: {resp.text}")
        print("[e2e] action accepted; waiting for async screening to write back...")

        # 4. The screening runs in a background task; poll the column.
        deadline = time.time() + POLL_TIMEOUT_S
        status = None
        while time.time() < deadline:
            status = read_status(item_id)
            if status in VALID_LEVELS:
                break
            time.sleep(POLL_INTERVAL_S)

        if status not in VALID_LEVELS:
            raise SystemExit(
                f"expected a risk level {sorted(VALID_LEVELS)} within "
                f"{POLL_TIMEOUT_S}s, got {status!r}"
            )
        print(f"[e2e] PASS — board updated with '{status}'")
    finally:
        # Keep recent history for debugging; just bound the board size.
        prune_old_items(KEEP_ITEMS)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as err:
        print(f"[e2e] FAIL — {err}")
        sys.exit(1)
