"""Error envelope — the shape declared by the official v2 OpenAPI.

┌─ TWO SOURCES THAT DISAGREE ──────────────────────────────────────────────────┐
│ The "Error Handling & Status Codes" guide (checked 2026-02-06) shows         │
│                                                                              │
│     {"error": "unprocessable_entity", "message": "...", "details": {...}}    │
│                                                                              │
│ while the OpenAPI embedded in EACH of the 91 GET operations in the           │
│ reference declares, uniformly:                                              │
│                                                                              │
│     {"error": "<readable message>", "status": <integer>}                    │
│                                                                              │
│ The OpenAPI is authoritative here: it's machine-readable, versioned with    │
│ the endpoints, and it's the one the provider publishes as the contract.     │
│ The divergence is logged in docs/UNVERIFIED-FIELDS.md — a probe against a   │
│ real instance will settle it.                                                │
└──────────────────────────────────────────────────────────────────────────────┘

Two shape exceptions, also attested:

  • **429**: the body is NOT JSON. It's plain text,
    `Rate limit exceeded. Please retry in X seconds.` ("Rate Limiting in
    API v2" doc). A consumer that calls `response.json()` on a 429 breaks —
    exactly the kind of trap a mock should set.
  • **400**: the OpenAPI declares an `anyOf` of six shapes. The simplest one
    (`{error, status}`) is the one emitted; the other five describe body
    validation errors, i.e. writes — out of scope.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

#: The exact messages given as `example` by the official OpenAPI. Centralized
#: here so a probing campaign against a real instance can fix them in a
#: single diff.
MESSAGE_401 = "The access token is invalid"
MESSAGE_404 = "Not Found"


def error(
    status_code: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """The Pennylane error envelope: `{"error", "status"}`, nothing else.

    `additionalProperties: false` in the OpenAPI — adding a "useful" key
    (a `code`, a `request_id`) would make the contract lie.
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": message, "status": status_code},
        headers=headers or {},
    )


def scope_error(scope: str) -> JSONResponse:
    """The missing-scope 403, word for word (the OpenAPI's `example`)."""
    return error(403, f'Access to this resource requires scope "{scope}".')


def token_error() -> JSONResponse:
    """401 — token missing, invalid or expired. The three cases are
    indistinguishable at the provider: a single message, no hint as to which
    of the three."""
    return error(401, MESSAGE_401)


def not_found_error() -> JSONResponse:
    return error(404, MESSAGE_404)


def rate_limit_error(retry_after: int, headers: dict[str, str]) -> PlainTextResponse:
    """429 — PLAIN TEXT body, not JSON. See the module's callout above."""
    return PlainTextResponse(
        status_code=429,
        content=f"Rate limit exceeded. Please retry in {retry_after} seconds.",
        headers={**headers, "retry-after": str(retry_after)},
    )


def rate_limit_headers(limit: int, remaining: int, reset: int) -> dict[str, str]:
    """The `ratelimit-*` headers, present on EVERY response — not just on
    429s. This is what lets a consumer throttle itself before getting rate
    limited, and a client that doesn't read them should be caught out here
    rather than in production."""
    return {
        "ratelimit-limit": str(limit),
        "ratelimit-remaining": str(max(0, remaining)),
        "ratelimit-reset": str(reset),
    }


def unknown_route_detail(request: Request) -> JSONResponse:
    """An unknown route renders the Pennylane envelope, not FastAPI's 404."""
    del request  # the provider never echoes back the requested path
    return not_found_error()


#: Reused on every route (`responses=ERROR_RESPONSES`): this is what turns the
#: generated contract from a "list of paths" into an actual contract.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "description": (
            "Invalid parameter — `limit` out of bounds, unreadable `cursor`, "
            "malformed `filter`, or `start_date` and `cursor` sent together."
        )
    },
    401: {"description": "Token missing, invalid or expired. NOT retryable."},
    403: {
        "description": (
            "The token is valid but does not carry the required scope. "
            "NOT retryable: a new token must be regenerated."
        )
    },
    404: {"description": "Unknown resource, or belonging to another company."},
    422: {"description": "Business rule violated (unbalanced entry, inconsistent VAT…)."},
    429: {
        "description": (
            "Rate limit reached (25 requests / 5 s per token). "
            "**Plain text body, not JSON.** `retry-after` header provided."
        )
    },
    500: {"description": "Injected failure."},
    503: {"description": "Injected transient failure."},
}
