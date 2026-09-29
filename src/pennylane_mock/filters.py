"""The `filter` parameter and the `sort` parameter — the selection dialect.

`filter` is a **JSON array passed in the query string**, each object carrying
exactly `{field, operator, value}` ("Filter API Data" guide, 2025-10-23):

    ?filter=[{"field":"date","operator":"gteq","value":"2024-01-01"}]

Nine operators, not one more: `eq`, `not_eq`, `lt`, `lteq`, `gt`, `gteq`,
`in`, `not_in`, `start_with`. Filters within the same array combine with AND.

`sort` is a field name, prefixed with `-` for descending order. The default
declared by the OpenAPI is **`-id`** on almost every list — a consumer that
paginates without stating its sort therefore inherits descending order,
which is counter-intuitive and worth hitting in a test.

┌─ WHAT ISN'T VALIDATED, AND WHY ─────────────────────────────────────────────┐
│ The OpenAPI declares, endpoint by endpoint, WHICH fields are filterable    │
│ and with which operators. The mock accepts any field present on the        │
│ element: rejecting an undeclared field would require copying 40            │
│ allow-lists that the docs themselves admit are moving targets, and would   │
│ make the mock fail where the provider, meanwhile, would have added the     │
│ field.                                                                     │
│                                                                             │
│ An unknown OPERATOR, on the other hand, renders 400: the list of nine is   │
│ short, stable, and an operator invented on the consumer side is a real     │
│ bug.                                                                       │
│                                                                             │
│ This choice is logged in docs/UNVERIFIED-FIELDS.md.                        │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
from typing import Any

OPERATORS = frozenset({"eq", "not_eq", "lt", "lteq", "gt", "gteq", "in", "not_in", "start_with"})


class InvalidFilter(ValueError):
    """Malformed `filter` → 400."""


class InvalidSort(ValueError):
    """Malformed `sort` → 400."""


def _comparable(value: Any) -> Any:
    """Renders a value comparable in a stable way.

    Amounts in the v2 dialect are STRINGS (`"1234.56"`): comparing
    `"900.00" < "1000.00"` lexicographically would give a false result. We
    therefore try the number first, and fall back to the string — which
    still lets ISO dates compare correctly, since they sort lexicographically.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _matches(left: Any, operator: str, right: Any) -> bool:  # noqa: PLR0911
    if operator == "in":
        return left in right if isinstance(right, list) else False
    if operator == "not_in":
        return left not in right if isinstance(right, list) else True
    if operator == "start_with":
        return isinstance(left, str) and left.lower().startswith(str(right).lower())
    if operator == "eq":
        return bool(left == right)
    if operator == "not_eq":
        return bool(left != right)

    if left is None or right is None:
        # An ordering comparison on a null field is meaningless: the row
        # doesn't match, rather than making the request fall over in a
        # TypeError.
        return False
    left_value, right_value = _comparable(left), _comparable(right)
    if type(left_value) is not type(right_value):
        left_value, right_value = str(left), str(right)
    if operator == "lt":
        return bool(left_value < right_value)
    if operator == "lteq":
        return bool(left_value <= right_value)
    if operator == "gt":
        return bool(left_value > right_value)
    return bool(left_value >= right_value)


def parse_filter(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise InvalidFilter("filter must be a JSON array of objects") from exc
    if not isinstance(parsed, list):
        raise InvalidFilter("filter must be a JSON array of objects")
    for entry in parsed:
        if not isinstance(entry, dict) or {"field", "operator", "value"} - entry.keys():
            raise InvalidFilter("each filter must have field, operator and value")
        if entry["operator"] not in OPERATORS:
            raise InvalidFilter(
                f"unknown operator {entry['operator']!r} "
                f"(available: {', '.join(sorted(OPERATORS))})"
            )
    return list(parsed)


def apply_filter(
    elements: list[dict[str, Any]], filters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Filters within the same array combine with AND."""
    result = elements
    for f in filters:
        field, operator, value = f["field"], f["operator"], f["value"]
        result = [e for e in result if _matches(e.get(field), operator, value)]
    return result


def apply_sort(
    elements: list[dict[str, Any]], raw: str | None, *, default: str = "-id"
) -> list[dict[str, Any]]:
    """`id` ascending, `-id` descending. Default `-id`, as declared by the OpenAPI."""
    expression = (raw or default).strip()
    if not expression:
        expression = default
    descending = expression.startswith("-")
    field = expression[1:] if descending else expression
    if not field:
        raise InvalidSort("sort must be a field name, optionally prefixed with '-'")

    def key(element: dict[str, Any]) -> tuple[int, int, float, str]:
        """A HOMOGENEOUS sort key, whatever the field carries.

        The same field can carry values of different natures: account
        numbers in the trial balance are `"411000"` for general accounts and
        `"411LUMIN"` for auxiliary ones. Comparing the two makes `sorted`
        fall over in a TypeError — and it falls on the detailed balance, not
        the aggregated one, so not in the first test you write.

        Hence a four-component key: null last, then numbers before strings,
        then the value within its own space.
        """
        value = element.get(field)
        if value is None:
            return (1, 0, 0.0, "")
        comparable_value = _comparable(value)
        if isinstance(comparable_value, (int, float)) and not isinstance(comparable_value, bool):
            return (0, 0, float(comparable_value), "")
        return (0, 1, 0.0, str(comparable_value))

    return sorted(elements, key=key, reverse=descending)
