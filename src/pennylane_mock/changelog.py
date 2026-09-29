"""The `/changelogs/*` endpoints — native incremental extraction.

This is THE mechanism the provider recommends for syncing: polling an event
log rather than relisting the whole universe. The dialect has four rules,
all reproduced here because each one can break a connector in production
without ever showing up in a test:

  1. **Ascending chronological order** — oldest first. A consumer that
     assumes the opposite sets its resume point on the FIRST event of the
     page and loses everything again on every pass.

  2. **Four-week retention.** An older `start_date` renders **422**, not a
     truncated list. That's the difference between "I received nothing, so
     nothing changed" and "my window is too wide": without the 422, a
     pipeline stopped for five weeks would believe it had caught up.

  3. **`start_date` and `cursor` are MUTUALLY EXCLUSIVE** — both together
     render **400**. Pagination continues a window; it doesn't open a new
     one. This is what rules out the classic "I resend my start_date on
     every page" bug, which replays the same first page forever.

  4. **The resume point only advances once `has_more` is false.** The mock
     can't enforce this, but it can make it observable: the `/__admin/state`
     control plane exposes the last parameters received per path, letting a
     downstream test PROVE that the consumer paginated all the way through
     before moving its bound.

An event carries the ID, the operation and three timestamps — **never** the
resource's state. A second call is required to get it, and the provider
recommends doing so in batches:
`filter=[{"field":"id","operator":"in","value":[…]}]`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .settings import settings


class WindowTooOld(ValueError):
    """`start_date` beyond retention → 422."""


class ExclusiveParameters(ValueError):
    """`start_date` AND `cursor` in the same request → 400."""


class InvalidDate(ValueError):
    """`start_date` that isn't RFC 3339 → 400."""


def parse_start_date(raw: str | None) -> datetime | None:
    """RFC 3339, `Z` accepted — the form the provider emits."""
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidDate("start_date must follow RFC3339 (e.g. 2026-07-15T09:00:00Z)") from exc
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def check_window(since: datetime | None, now: datetime) -> None:
    """The four-week retention. Beyond it: 422, not a truncated list."""
    if since is None:
        return
    limit = now - timedelta(days=settings.changelog_retention_days)
    if since < limit:
        raise WindowTooOld(
            f"start_date is older than the {settings.changelog_retention_days}-day retention window"
        )


def select(
    events: list[dict[str, Any]],
    since: datetime | None,
    now: datetime,
) -> list[dict[str, Any]]:
    """The events retained, later than the bound, in chronological order.

    ┌─ RETENTION PURGES, IT DOESN'T JUST REFUSE ──────────────────────────────┐
    │ "Changes are retained for 4 weeks." An older event no longer EXISTS: it  │
    │ isn't just unreachable via `start_date`, it also doesn't appear in the   │
    │ response without a bound. A mock that served the full history would      │
    │ teach the consumer that a full resync is possible through the           │
    │ changelog — it isn't, and that's exactly what breaks a pipeline stopped  │
    │ for five weeks.                                                          │
    └─────────────────────────────────────────────────────────────────────────┘

    The sort is redone here rather than assumed: evolution events are
    APPENDED to the base dataset's log, so the raw list isn't sorted once the
    world has moved. A consumer that receives events out of order sets a
    false resume point, and does so silently.
    """
    limit = now - timedelta(days=settings.changelog_retention_days)
    retained = [e for e in events if _instant(e["processed_at"]) >= limit]
    ordered = sorted(retained, key=lambda e: (e["processed_at"], e["id"]))
    if since is None:
        return ordered
    bound = since.astimezone(UTC)
    return [e for e in ordered if _instant(e["processed_at"]) >= bound]


def _instant(timestamp: str) -> datetime:
    text = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=UTC)
