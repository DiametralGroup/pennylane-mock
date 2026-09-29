"""Base models — the envelope, the error, and the honesty markers.

Typing gives you the OpenAPI contract, the /docs page, and shapes consumers
can use, all at once. But typing PUSHES YOU TO INVENT: as soon as a field is
missing from the documentation, the temptation is to guess it. The
safeguard is structural, and it's the same one used in the other four mocks:

  • `extra="allow"` everywhere — the model describes what we KNOW, not what
    IS;
  • `x-pennylane-confidence` on every field not backed by the official
    OpenAPI;
  • a test fails if an `unverified` field isn't logged in
    docs/UNVERIFIED-FIELDS.md — honesty is a build constraint.

This mock's source of truth is the **OpenAPI embedded in each of the 163
reference pages** at pennylane.readme.io (checked 2026-09-02, merged into a
single spec — cf. docs/EXTRACTION.md). Everything that comes from it is
attested. Everything else is marked.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def unverified(description: str) -> dict[str, Any]:
    """Marks a field whose name, shape or values are NOT attested.

    Used via `json_schema_extra`. Any field so marked MUST appear in
    `docs/UNVERIFIED-FIELDS.md` — `tests/test_contract_is_current.py`
    checks it.
    """
    return {"x-pennylane-confidence": "unverified", "x-pennylane-note": description}


def invented(description: str) -> dict[str, Any]:
    """Marks a field or behavior that does NOT exist at Pennylane."""
    return {"x-pennylane-confidence": "invented", "x-pennylane-note": description}


class Permissive(BaseModel):
    """Common base: unknown fields pass through instead of being rejected.

    A strict model would turn every change to the real API into a mock
    failure. The models describe what is emitted, not everything Pennylane
    can expose.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Link(Permissive):
    """A nested collection — `{"url": "…"}`, never an array.

    This is the most structural shape difference between v1 and v2: an
    invoice's lines are not IN the invoice, they're behind a link. A
    connector written for v1 reads an empty list and loads zero rows without
    an error.
    """

    url: str


class Reference(Permissive):
    """A pointer to another resource — `{"id": 42, "url": "…"}`.

    Some references carry ONLY `id` (`ledger_entry`, `billing_subscription`,
    `category_group`); others also carry `url`. The provider isn't uniform
    about this, so `url` is optional.
    """

    id: int
    url: str | None = None


class Page[T](BaseModel):
    """The pagination envelope: `{items, has_more, next_cursor}`.

    `next_cursor` is **`null`**, not absent and not `""`, when there's
    nothing left: a consumer that tests `if "next_cursor" in body` loops
    forever.

    ┌─ THE ENVELOPE ISN'T UNIFORM, AND THE OPENAPI DOESN'T SAY SO ───────────┐
    │ This model used to carry `additionalProperties: false` — "exactly     │
    │ three keys" — on the strength of the official OpenAPI. Checked        │
    │ against a real instance on 2026-09-04 (`scripts/compare_real.py`),     │
    │ that's wrong: FOUR collections out of sixteen add an OFFSET            │
    │ pagination alongside the cursor — `current_page`, `per_page`,          │
    │ `total_items`, `total_pages`.                                          │
    │                                                                         │
    │ These are `journals`, `ledger_accounts`, `ledger_entries` and          │
    │ `fiscal_years`. Not `ledger_entry_lines`, although it's in the same    │
    │ family: there is no rule to guess here, only an observation to        │
    │ reproduce.                                                             │
    │                                                                         │
    │ So the mock was asserting a regularity the provider doesn't have — and │
    │ a mock more regular than reality is the same defect as a mock too      │
    │ permissive: it validates code that breaks elsewhere. A consumer that   │
    │ wanted to display a total, count pages or short-circuit the cursor     │
    │ would have found the field in production and not here.                │
    │                                                                         │
    │ And they hold `null`: present, empty. A consumer that tests their      │
    │ PRESENCE to pick its pagination mode finds them, switches to offset,    │
    │ and reads `null` everywhere — without an error.                        │
    │                                                                          │
    │ Hence `extra="allow"`: the four keys are rendered where they were       │
    │ OBSERVED, and nowhere else. Cf. docs/UNVERIFIED-FIELDS.md.              │
    └─────────────────────────────────────────────────────────────────────────┘
    """

    model_config = ConfigDict(extra="allow")

    #: OFFSET pagination, served only by the collections where it was
    #: observed. Declared here FOR THE CONTRACT: the response is a
    #: `JSONResponse` built on `paginate`'s dict, so these keys are really
    #: ABSENT elsewhere, not rendered as `null`. Rendering `"total_pages":
    #: null` on a collection that doesn't carry it would be a third,
    #: invented dialect.
    current_page: int | None = Field(
        default=None, description="Page rank — `null` under cursor pagination."
    )
    per_page: int | None = Field(
        default=None, description="Page size — `null` under cursor pagination."
    )
    total_items: int | None = Field(
        default=None, description="Total element count — `null` under cursor pagination."
    )
    total_pages: int | None = Field(
        default=None, description="Total page count — `null` under cursor pagination."
    )

    items: list[T]
    has_more: bool = Field(description="Does another page exist?")
    next_cursor: str | None = Field(
        default=None,
        description="Cursor of the next page; `null` at the end of the results.",
    )


class PennylaneError(Permissive):
    """The error body — `{"error", "status"}`, and nothing else.

    WARNING: the "Error Handling & Status Codes" guide describes a DIFFERENT
    shape (`{"error", "message", "details"}`). The OpenAPI, meanwhile,
    declares this one uniformly across the 91 GET operations. Divergence
    logged in docs/UNVERIFIED-FIELDS.md; the OpenAPI is the one followed.

    The **429 has no JSON body at all**: it renders plain text.
    """

    error: str
    status: int


#: Reused on every route (`responses=ERROR_RESPONSES`): without it, the
#: generated contract would only describe the happy path, and a consumer
#: wouldn't know which failures it needs to handle.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "model": PennylaneError,
        "description": (
            "Invalid parameter: `limit` out of bounds (it is NOT clamped), "
            "unreadable `cursor`, malformed `filter`, or `start_date` and `cursor` "
            "sent together on a changelog."
        ),
    },
    401: {
        "model": PennylaneError,
        "description": (
            "Token missing, invalid or expired — the three cases are indistinguishable. "
            "NOT retryable."
        ),
    },
    403: {
        "model": PennylaneError,
        "description": (
            "The token does not carry the required scope. The message NAMES the "
            "missing scope. NOT retryable."
        ),
    },
    404: {
        "model": PennylaneError,
        "description": "Unknown resource, or belonging to another company.",
    },
    422: {
        "model": PennylaneError,
        "description": (
            "Business rule violated. On a changelog: `start_date` beyond the "
            "four-week retention window."
        ),
    },
    429: {
        "description": (
            "Rate limit reached — 25 requests / 5 s per token. "
            "**The body is PLAIN TEXT, not JSON**; `retry-after` header. "
            "The `ratelimit-*` headers are present on ALL responses."
        ),
        "content": {"text/plain": {"schema": {"type": "string"}}},
    },
    500: {"model": PennylaneError, "description": "Injected failure."},
    503: {"model": PennylaneError, "description": "Injected transient failure."},
}
