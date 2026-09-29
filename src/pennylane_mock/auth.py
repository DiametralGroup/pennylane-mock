"""Authentication — static Bearer token and scopes, Pennylane's exact dialect.

The simplest regime of the ecosystem's five mocks: no JWT to forge
(BoondManager), no client_credentials exchange (Entra), no RS256 assertion
(GA4), no version header (LinkedIn). A long-lived company token, carried by
`Authorization: Bearer <TOKEN>`.

What makes the dialect interesting is elsewhere: the **scopes**.

┌─ 401 AND 403 ARE NOT HANDLED THE SAME WAY ──────────────────────────────────┐
│ 401 — the token is missing, invalid or expired. The three cases are         │
│       INDISTINGUISHABLE at the provider: a single message, no hint as to    │
│       which of the three. A new token must be regenerated.                  │
│ 403 — the token is valid, but does not carry the required scope. The        │
│       message NAMES the missing scope, and it's the only actionable        │
│       information in the whole API : without it you regenerate a token     │
│       at random.                                                            │
│                                                                              │
│ Neither is worth retrying. A client that replays a 403 hoping for better    │
│ loops until it exhausts its attempts, then fails on a timeout message that  │
│ says nothing about the real problem.                                        │
└──────────────────────────────────────────────────────────────────────────────┘

A route's scope is declared in `app.py` (the `scope` field of
`ResourceSpec`) and comes from the official OpenAPI, operation by operation.
`GET /me` is the only endpoint WITHOUT a required scope — it is precisely
the one used to discover which scopes are available.
"""

from __future__ import annotations

from .settings import settings


def token_from_header(value: str | None) -> str | None:
    """Extracts the token from `Authorization: Bearer <TOKEN>`.

    The prefix is compared case-insensitively: HTTP libraries write both
    `Bearer` and `bearer`, and refusing the latter form would be a strictness
    the provider does not have.
    """
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def token_is_valid(token: str | None) -> bool:
    return token is not None and token == settings.token


def scope_granted(required: str | None) -> bool:
    """Does the token carry the required scope?

    An endpoint documented as "requires one of `x:readonly`, `x:all`" is
    declared with `x:readonly`: carrying `x:all` must be enough, since it's
    the broader scope. Hence the explicit tolerance below — without it, a
    write token would be refused read access, which does not happen in
    reality.
    """
    if required is None:
        return True
    if required in settings.scopes:
        return True
    base, sep, suffix = required.partition(":")
    if sep and suffix == "readonly":
        return f"{base}:all" in settings.scopes
    return False
