import json

import httpx

MONDAY_API_URL = "https://api.monday.com/v2"


async def monday_request(query, variables, api_token):
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            MONDAY_API_URL,
            json={"query": query, "variables": variables},
            headers={
                "Content-Type": "application/json",
                "Authorization": api_token,
                "API-Version": "2024-01",
            },
        )
    response.raise_for_status()
    data = response.json()

    if data.get("errors"):
        raise RuntimeError(f"Monday GraphQL error: {json.dumps(data['errors'])}")

    return data


async def get_item_name(item_id, api_token):
    """Fetch the item's name (vendor name) so we don't depend on the trigger
    output mapping — works on any client board."""
    query = "query ($itemId: [ID!]) { items (ids: $itemId) { name } }"
    data = await monday_request(query, {"itemId": [str(item_id)]}, api_token)
    items = (data.get("data") or {}).get("items") or []
    return items[0].get("name") if items else None


async def get_item_column_text(item_id, column_id, api_token):
    """Read the display text of a single column on an item (e.g. the country the
    client mapped in the recipe). Returns None if the column is empty or absent —
    country is an optional refinement, never required for screening."""
    query = """
      query ($itemId: [ID!], $columnIds: [String!]) {
        items (ids: $itemId) {
          column_values (ids: $columnIds) { id text }
        }
      }
    """
    data = await monday_request(
        query, {"itemId": [str(item_id)], "columnIds": [str(column_id)]}, api_token
    )
    items = (data.get("data") or {}).get("items") or []
    if not items:
        return None
    for cv in items[0].get("column_values") or []:
        if cv.get("id") == str(column_id):
            return cv.get("text") or None
    return None


async def update_vendor_record(
    *, board_id, item_id, status_column_id, details_column_id, risk_level, details, api_token
):
    """Write the risk result to the columns the CLIENT mapped in the recipe.

    Status is written by LABEL (not index): Monday resolves the label to the
    correct index on any board regardless of order, so it works on client
    boards whose status columns differ from ours. Labels must match the status
    column options ("Clear" / "Warning" / "Critical").
    """
    column_values = json.dumps(
        {
            status_column_id: {"label": risk_level},
            details_column_id: details,
        }
    )

    query = """
      mutation ($boardId: ID!, $itemId: ID!, $columnValues: JSON!) {
        change_multiple_column_values(
          board_id: $boardId,
          item_id: $itemId,
          column_values: $columnValues,
          create_labels_if_missing: true
        ) {
          id
        }
      }
    """

    return await monday_request(
        query,
        {
            "boardId": str(board_id),
            "itemId": str(item_id),
            "columnValues": column_values,
        },
        api_token,
    )
