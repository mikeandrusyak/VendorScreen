# GraphQL Cookbook: Boards, Items, Columns, and More

Working query/mutation examples for monday's core objects. For auth, rate limits, pagination mechanics, and error handling, see [api-auth-and-limits.md](api-auth-and-limits.md) — this file is just the object model and example payloads.

Endpoint: `POST https://api.monday.com/v2`, GraphQL body, `Authorization` header carries the token.

## Contents
- [Boards](#boards)
- [Items](#items)
- [Columns & column values](#columns--column-values-the-critical-section) ⭐ most error-prone part of the API
- [Writing column values: which mutation](#writing-column-values-which-mutation)
- [Groups](#groups)
- [Updates](#updates)
- [Users](#users)
- [Notifications](#notifications)
- [Workspaces](#workspaces)

## Boards

A **Board** is the top-level container: `columns` define the schema, `groups` are row-sections, items are the rows.

```graphql
query {
  boards(ids: [1234567890]) {
    id
    name
    description
    board_kind
    state
    workspace_id
    columns { id title type description }
    groups { id title color position }
  }
}
```

Other confirmed `Board` fields: `permissions`, `hierarchy_type`, `items_page`, `items_count`, `owners`, `creator`, `subscribers`, `url`, `updated_at`, `access_level`.

## Items

An **Item** is a row. Confirmed fields: `id`, `name`, `column_values`, `board`, `group`, `created_at`, `updated_at`, `creator`, `subitems`, `parent_item`, `linked_items`, `state`, `url`.

**By id:**
```graphql
query {
  items(ids: [9876543210, 9876543211]) {
    id
    name
    column_values { id text value type }
  }
}
```
`items` args: `ids` (max 100), `limit` (default 25, max 100), `page`, `newest_first`, `exclude_nonactive`. **For reading all items on a board, use `items_page` instead of `items`** — that's the documented recommendation, not just a style choice.

**All items on a board, paginated:**
```graphql
query {
  boards(ids: [1234567890]) {
    items_page(limit: 50) {
      cursor
      items { id name column_values { id text value } }
    }
  }
}
```
`items_page` args: `limit` (default 25, max 500), `query_params` (filter/sort), `cursor`. `query_params` and `cursor` are mutually exclusive in the same call. Next page:
```graphql
query {
  next_items_page(cursor: "MSw5NzI4MDA5MDAsaV9YcmxJb0p1VEdYc1VWeGlxeF9kLDg4MiwzNXw0MTQ1NzU1MTE5", limit: 50) {
    cursor
    items { id name }
  }
}
```
`next_items_page` is root-level only (can't nest under `boards`). Cursors expire **60 minutes** after the *first* `items_page` call in the sequence — a long paginated export needs to finish within that window.

**Find items by column value** (instead of paging through everything):
```graphql
query {
  items_page_by_column_values(
    board_id: 1234567890
    limit: 50
    columns: [
      { column_id: "text", column_values: ["This is a text column"] }
      { column_id: "country", column_values: ["US", "IL"] }
    ]
  ) {
    cursor
    items { id name }
  }
}
```

**Create an item:**
```graphql
mutation {
  create_item(
    board_id: 1234567890
    group_id: "group_one"
    item_name: "New vendor screening"
    column_values: "{\"status\": {\"label\": \"Working on it\"}, \"date\": \"2026-07-13\"}"
    create_labels_if_missing: true
  ) {
    id
    name
    url
  }
}
```
`column_values` is a **JSON-encoded string**, not an inline GraphQL object — true for every write below too. Full arg list: `board_id!`, `item_name!`, `group_id`, `column_values`, `create_labels_if_missing`, `position_relative_method`, `relative_to`.

## Columns & column values (the critical section)

Every column exposes a generic `ColumnValue` interface (`id`, `text`, `value`, `type`, `column`) plus a type-specific implementation reachable via inline fragments:
```graphql
query {
  items(ids: [1234567890]) {
    column_values {
      id
      type
      text
      value
      ... on StatusValue { label index }
      ... on DropdownValue { text values { id label } }
    }
  }
}
```

**Two write paths exist for every type:** a plain-string form via `change_simple_column_value` (monday parses it the way it would parse you typing into the cell), and a structured-JSON form used either as `change_column_value`'s `value` argument or as one entry in `change_multiple_column_values`'s `column_values` object. Both are shown per type below. **The JSON shapes are asymmetric on purpose — they mirror UI quirks — and are the single biggest source of "why did my write silently no-op" bugs.** Prefer [../scripts/column_values.py](../scripts/column_values.py) over memorizing this table.

| Type | Write JSON (inside `column_values`) | Gotcha |
|---|---|---|
| `status` | `{"status": {"label": "Done"}}` or `{"index": 1}` | supports `create_labels_if_missing` |
| `text` | `{"text": "Sample text"}` | plain string, no wrapper |
| `long_text` | `{"long_text": {"text": "Line one\nLine two"}}` | **requires** the `{"text": ...}` wrapper, unlike plain `text` |
| `dropdown` | `{"dropdown": {"labels": ["Marketing"]}}` or `{"ids": [1,2]}` | plural `labels` (supports multi-select); supports `create_labels_if_missing` |
| `date` | `{"date": {"date": "2026-06-15", "time": "09:00:00"}}` | `time` optional; write value is UTC even though read `text`/`time` are localized |
| `people` | `{"people": {"personsAndTeams": [{"id": 123, "kind": "person"}]}}` | write key is **camelCase** `personsAndTeams`; read field is snake_case `persons_and_teams`; `kind` is literally `"person"` or `"team"` |
| `numbers` | `{"numbers": "42.5"}` | value is a **string**, even though the column stores a float |
| `checkbox` | `{"checkbox": {"checked": "true"}}` | value is the **string** `"true"`/`"false"`, not a boolean, even though the read `value` is a real boolean |
| `link` | `{"link": {"url": "https://...", "text": "Go to monday!"}}` | write key is `text`; the corresponding **read** field is `url_text`, not `text` |

Clearing a value: `""` for text/number columns; `null` for JSON-shaped columns (per the type's own page); `{"clear_all": true}` for file/asset columns.

The `Column` type itself: `id!`, `title!`, `type!` (`ColumnType!`), `archived!`, `description`, `width`, `settings` (JSON — label/color mappings), `revision!`.

## Writing column values: which mutation

All three accept an optional `create_labels_if_missing: Boolean` (confirmed on the canonical reference page for all three, despite some other pages only mentioning it for one or two):

| Mutation | Value argument | Scope |
|---|---|---|
| `change_column_value` | `value: JSON!` | one column |
| `change_simple_column_value` | `value: String` | one column, plain-string form |
| `change_multiple_column_values` | `column_values: JSON!` | many columns in one call — **prefer this for multi-column writes**, it's both fewer round-trips and cheaper against your complexity budget |

```graphql
mutation ($board_id: ID!, $item_id: ID!, $column_values: JSON!, $createLabels: Boolean) {
  change_multiple_column_values(
    board_id: $board_id
    item_id: $item_id
    column_values: $column_values
    create_labels_if_missing: $createLabels
  ) {
    id
    name
    column_values { id text }
  }
}
```
```json
{
  "board_id": 1234567890,
  "item_id": 9876543210,
  "column_values": "{\"status\": {\"label\": \"Done\"}, \"date4\": {\"date\": \"2026-07-13\"}, \"text\": \"note\"}",
  "createLabels": true
}
```

`change_simple_column_value`, single column, plain string:
```graphql
mutation ($board_id: ID!, $item_id: ID!, $column_id: String!, $value: String, $createLabels: Boolean) {
  change_simple_column_value(
    board_id: $board_id
    item_id: $item_id
    column_id: $column_id
    value: $value
    create_labels_if_missing: $createLabels
  ) { id }
}
```

## Groups

```graphql
query {
  boards(ids: 1234567890) {
    groups { id title color position }
  }
}
```
```graphql
mutation {
  create_group(
    board_id: 1234567890
    group_name: "New group"
    relative_to: "test_group"
    group_color: "#ff642e"
    position_relative_method: before_at
  ) { id }
}
```
`group_name` max 255 chars; `group_color` is a `#`-prefixed hex string; `position_relative_method` is `before_at` or `after_at`.

## Updates

An **Update** is a comment/activity post on an item — not a column value.
```graphql
query {
  updates(limit: 50, from_date: "2026-01-01", to_date: "2026-07-13") {
    id
    body
    created_at
    creator { id name }
  }
}
```
(Can also nest under an item: `items(ids: [...]) { updates { id body } }`.)

```graphql
mutation {
  create_update(
    item_id: 9876543210
    body: "Screening complete — see the report."
    mentions_list: [{ id: 1234567890, type: User }]
  ) {
    id
    body
    created_at
    creator { id name }
  }
}
```
`body` supports basic HTML tags (`<b>`, `<i>`, `<br>`). **Don't put a literal `@` in `body` to mention someone — use `mentions_list`** (objects with `id` + `type`, where `type` is `User`/`Team`/`Board`/`Project`); the docs explicitly warn plain `@` text doesn't create a real mention. `parent_id` replies to an existing update for threading.

## Users

```graphql
query {
  users(limit: 50) { id name email }
}
```
Args include `ids`, `emails`, `name` (fuzzy search), `limit` (max 1000 as of recent API versions), `page`, `status`, `user_kind`, `sort`. Requires `users:read` scope.

```graphql
query { me { id name email is_guest created_at } }
```
`me` returns the user tied to the current token — useful for "who is making this call" without hardcoding an id. Root-level only; requires `me:read` scope.

## Notifications

`create_notification` sends an in-app notification (bell icon; may also email, per the recipient's preferences) — monday's mechanism for a bot/integration to point a user at something. It's fire-and-forget: **notifications are asynchronous and you can't query back a notification id.**

```graphql
mutation {
  create_notification(
    user_id: 48202303
    target_id: 11971936030
    text: "Your vendor screening report is ready — check the item for details."
    target_type: Project
  ) {
    text
  }
}
```
All four args required. `target_type` has exactly two values, and `target_id`'s meaning depends on which:
- `Project` — `target_id` is an **item or board** id
- `Post` — `target_id` is an **update or reply** id

## Workspaces

A **Workspace** groups related boards (like a folder per team/department/client).
```graphql
query { workspaces(ids: [1234567]) { id name kind description } }
```
```graphql
query { boards(workspace_ids: [1234567], limit: 50) { id name } }
```
`Board.workspace_id` is nullable — boards in the default workspace have a null id.

```graphql
mutation {
  create_workspace(name: "New Cool Workspace", kind: open, description: "...") {
    id
    description
  }
}
```
Requires `workspaces:write` scope.
