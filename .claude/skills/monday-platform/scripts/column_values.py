"""
Build correctly-shaped monday.com `column_values` JSON.

monday's column-value JSON is asymmetric across types on purpose (it mirrors each
column's own UI quirks): checkbox takes the *string* "true"/"false" rather than a
boolean, people writes use camelCase `personsAndTeams` even though the read field is
snake_case, link's write key is `text` but the read field is `url_text`, and so on.
These asymmetries are the most common source of a write that "succeeds" (200 OK) but
silently doesn't change anything. See ../references/graphql-cookbook.md for the full
per-type table this module implements, including citations.

Each `*_value` function returns the value for ONE column, ready to drop into a
`column_values` dict. `build_column_values` assembles several into the JSON string
`change_multiple_column_values` (or `create_item`) expects.

Usage:
    from column_values import status_value, date_value, build_column_values

    payload = build_column_values({
        "status": status_value("Critical"),
        "text_details": text_value("2 sanctions matches found"),
        "date4": date_value("2026-07-13"),
    })
    # payload is now a JSON string ready for the `column_values` GraphQL argument
"""

from __future__ import annotations

import json
from typing import Literal


def status_value(label: str) -> dict:
    """Write a status column by label (auto-creates the label if you also pass
    create_labels_if_missing=True on the mutation)."""
    return {"label": label}


def status_value_by_index(index: int) -> dict:
    return {"index": index}


def text_value(text: str) -> str:
    """Plain text column — the write value is a bare string, no wrapper object."""
    return text


def long_text_value(text: str) -> dict:
    """Unlike plain `text`, long_text REQUIRES the {"text": ...} wrapper."""
    return {"text": text}


def dropdown_value(labels: list[str]) -> dict:
    """Dropdown supports multiple selections, hence the plural `labels` key."""
    return {"labels": labels}


def dropdown_value_by_ids(ids: list[int]) -> dict:
    return {"ids": ids}


def date_value(date: str, time: str | None = None) -> dict:
    """date: "YYYY-MM-DD", time (optional): "HH:MM:SS". Written value is UTC."""
    value: dict = {"date": date}
    if time is not None:
        value["time"] = time
    return value


def people_value(people: list[tuple[int, Literal["person", "team"]]]) -> dict:
    """people/team ids: e.g. [(48202303, "person"), (51166, "team")].
    Write key is camelCase `personsAndTeams` (the read field is snake_case)."""
    return {"personsAndTeams": [{"id": pid, "kind": kind} for pid, kind in people]}


def numbers_value(number: float) -> str:
    """Written as a STRING even though the column stores a float."""
    return str(number)


def checkbox_value(checked: bool) -> dict:
    """Written as the STRING "true"/"false" — the read `value` is a real boolean,
    but the write shape is not; passing a bare JSON boolean here is a common bug."""
    return {"checked": "true" if checked else "false"}


def link_value(url: str, text: str = "") -> dict:
    """Write key is `text`; the corresponding READ field is `url_text`, not `text`."""
    return {"url": url, "text": text}


def build_column_values(values: dict[str, object]) -> str:
    """Assemble several column values (column_id -> value from the helpers above)
    into the JSON string `change_multiple_column_values` / `create_item` expects."""
    return json.dumps(values)


if __name__ == "__main__":
    example = build_column_values(
        {
            "status": status_value("Critical"),
            "text_details": text_value("2 sanctions matches, top score 0.91"),
            "date4": date_value("2026-07-13"),
            "checkbox_reviewed": checkbox_value(False),
        }
    )
    print(example)
