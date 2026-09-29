"""Cursor pagination — the ecosystem's fifth pagination dialect.

    {"items": [...], "has_more": true, "next_cursor": "eyJhZnRlciI6IDEwMH0"}

Compare with the other four mocks: `page`/`maxResults` at BoondManager,
`@odata.nextLink` at Graph, `start`/`count` Rest.li at LinkedIn,
`limit`/`offset` at GA4. A connector that has "one" generic pagination loop
breaks here, and that's the point.

┌─ THREE TRAPS REPRODUCED ON PURPOSE ─────────────────────────────────────────┐
│ 1. THE CURSOR DOES NOT ENCODE FILTERS. The documentation is explicit:        │
│    "Omitting the filters on page 2+ will return unfiltered results from     │
│    the cursor position." So NO 400, NO warning — just extra rows,           │
│    silently. A pipeline that forgets to replay its `filter` loads rows it   │
│    thought it had excluded. Reproduced as-is.                               │
│                                                                              │
│ 2. `limit` OUT OF BOUNDS RENDERS 400, it isn't clamped. 1..100 on ordinary   │
│    lists, 1..1000 on changelogs — two ceilings, declared this way endpoint   │
│    by endpoint in the official OpenAPI.                                     │
│                                                                              │
│ 3. `next_cursor` is `null` — not absent, not "" — when `has_more` is        │
│    false. `additionalProperties: false`: the three keys, always, and        │
│    nothing else.                                                            │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ WHAT THE CURSOR REALLY IS ─────────────────────────────────────────────────┐
│ The provider's documentation gives THREE incompatible encodings:            │
│   • the pagination guide            : `eyJpZCI6MTAwfQ==`  → {"id":100}      │
│   • the /bank_accounts example      : `dXBkYXRlZF9hdDoxNjc0MTIzNDU2`        │
│                                                    → updated_at:1674123456   │
│   • the changelogs example          : `MjAyNS0wMS0wOVQwODoyNDozOC44MTI0NTha`│
│                                                    → 2025-01-09T08:24:38…Z   │
│                                                                              │
│ Three forms, one common point: it's base64. The conclusion follows from     │
│ what the doc itself states — "the cursor is an opaque string" — and a       │
│ consumer that decodes it leans on an implementation detail that has         │
│ already changed three times.                                                │
│                                                                              │
│ The mock therefore emits base64url of JSON, the pagination guide's form,    │
│ and logs it in docs/UNVERIFIED-FIELDS.md. It does NOT sign the cursor:      │
│ that would invent a strictness the provider doesn't have.                   │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from .settings import settings


class InvalidCursor(ValueError):
    """Unreadable cursor → 400, as with the provider."""


class InvalidLimit(ValueError):
    """`limit` out of bounds → 400. The message carries the real bounds."""


def encode_cursor(payload: dict[str, Any]) -> str:
    """base64url WITHOUT padding — the trailing `=` is a character that must
    be escaped in a query string, and the provider's examples sometimes
    carry it, sometimes don't."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursor("Invalid cursor") from exc
    if not isinstance(payload, dict):
        raise InvalidCursor("Invalid cursor")
    return payload


def requested_limit(raw: str | None, *, maximum: int | None = None) -> int:
    """Validates `limit` and returns the effective value.

    The provider REFUSES an out-of-bounds value instead of clamping it. We
    reproduce that refusal, including for a non-integer value — a
    `limit=abc` silently brought back to 20 would make a broken client's
    test pass.
    """
    cap = settings.max_limit if maximum is None else maximum
    if raw is None or raw == "":
        return settings.default_limit
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvalidLimit(f"limit must be an integer between 1 and {cap}") from exc
    if value < 1 or value > cap:
        raise InvalidLimit(f"limit must be between 1 and {cap}")
    return value


def paginate(
    elements: list[dict[str, Any]],
    *,
    cursor: str | None,
    limit: int,
    key: str = "id",
    offset: bool = False,
) -> dict[str, Any]:
    """Slices an ALREADY sorted and filtered list into one page of the dialect.

    The cursor carries the rank of the last element served (`after`) AND the
    value of its key (`key`). Rank alone would be enough to paginate, but it
    would make the page drift if the dataset changes between two calls; the
    key allows the exact position to be found again, and falls back to the
    rank when the element has disappeared (deleted in the meantime). This is
    the behavior of a real cursor: stable over data that lives.
    """
    start = 0
    if cursor:
        payload = decode_cursor(cursor)
        value = payload.get("key")
        start = int(payload.get("after", 0))
        if value is not None:
            positions = [i for i, e in enumerate(elements) if e.get(key) == value]
            if positions:
                start = positions[0] + 1

    page = elements[start : start + limit]
    has_more = start + len(page) < len(elements)
    next_cursor: str | None = None
    if has_more and page:
        next_cursor = encode_cursor({"after": start + len(page), "key": page[-1].get(key)})
    body: dict[str, Any] = {"items": page, "has_more": has_more, "next_cursor": next_cursor}
    if offset:
        # ┌─ FOUR EXTRA, INERT KEYS, ON JUST FOUR COLLECTIONS ─────────────────┐
        # │ Observed on 2026-09-04 against a real instance: `journals`,       │
        # │ `ledger_accounts`, `ledger_entries` and `fiscal_years` render an  │
        # │ OFFSET pagination alongside the cursor. Not `ledger_entry_lines`, │
        # │ although it's in the same family — no rule to guess, only an     │
        # │ observation to reproduce.                                         │
        # │                                                                     │
        # │ And here's the trap: the four keys are `null`. They are PRESENT   │
        # │ and EMPTY. A consumer that tests their presence to choose its     │
        # │ pagination mode finds them, switches to offset, and reads `null`   │
        # │ everywhere — without an error.                                     │
        # │                                                                     │
        # │ The first version of this fix COMPUTED them, which was more       │
        # │ useful and therefore more wrong: a mock that returns a total       │
        # │ where the provider returns `null` validates code that breaks in   │
        # │ production. `scripts/compare_real.py` said so on the first pass.  │
        # │                                                                     │
        # │ Not observed, therefore not reproduced: what these keys return if  │
        # │ paginating by `page`/`per_page`. The mock does not accept these    │
        # │ parameters, and inventing it would be a third dialect. Cf.         │
        # │ docs/UNVERIFIED-FIELDS.md.                                         │
        # └─────────────────────────────────────────────────────────────────────┘
        body["current_page"] = None
        body["per_page"] = None
        body["total_items"] = None
        body["total_pages"] = None
    return body
