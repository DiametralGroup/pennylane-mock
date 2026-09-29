"""Failure injection — the real point of a mock.

"The point of the mock is to reproduce failure modes, not just happy paths"
(insights360 spec, §4.1). Declarative rules, drivable over HTTP via
`/__admin`, because the mock runs in a CONTAINER at the consumer's side:
outside the process, state can no longer be mutated in Python.

A SINGLE dispatch point, ordered, evaluated BEFORE authentication, so that
`auth_reject` can preempt it. Each rule carries an optional `times` counter:
a "transient" failure must stop on its own, otherwise you're not testing a
retry, you're testing a failure.

┌─ THE TWO KINDS SPECIFIC TO PENNYLANE ───────────────────────────────────────┐
│ `scope_reject`  — forces a 403 "Access to this resource requires scope       │
│                   \"x\"." on a given scope. The most frequent failure in     │
│                   real integrations (a token regenerated without a box       │
│                   checked), and the only one that NAMES its cause.           │
│ `rate_limit`    — 429 with a TEXT body, `retry-after` header, and the       │
│                   `ratelimit-*` headers, which themselves go out on every    │
│                   response. A client that calls `.json()` on the 429        │
│                   breaks here.                                              │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal[
    "rate_limit",
    "status",
    "latency",
    "page_drift",
    "auth_reject",
    "scope_reject",
    "cursor_reject",
]


@dataclass
class Rule:
    """An injection rule.

    `scope` is a glob pattern on the path (`/api/external/v2/customer_invoices`,
    `/api/external/v2/*`, `*`), which lets a specific resource be targeted
    without enumerating its routes.
    """

    id: str
    kind: Kind
    scope: str = "*"
    #: Remaining applications. None = unlimited. This is what makes the
    #: difference between a transient failure (that a retry must absorb) and
    #: a persistent failure (that must make the run fail with a non-zero
    #: exit code).
    times: int | None = None

    # rate_limit
    after_requests: int = 0
    retry_after_seconds: int = 1
    # status
    status: int = 500
    # latency
    seconds: float = 0.0
    # page_drift
    after_page: int = 1
    mode: Literal["insert", "remove"] = "insert"
    # scope_reject
    missing_scope: str = "customer_invoices:readonly"

    extra: dict[str, Any] = field(default_factory=dict)

    def matches(self, path: str) -> bool:
        return fnmatch.fnmatch(path, self.scope)

    def consume(self) -> bool:
        """Decrements the counter. Returns False once the rule is exhausted."""
        if self.times is None:
            return True
        if self.times <= 0:
            return False
        self.times -= 1
        return True


class InjectionEngine:
    """The engine, and the per-path request counter it depends on."""

    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self.request_counts: dict[str, int] = {}
        self.last_query_params: dict[str, dict[str, str]] = {}
        self._next_id = 1
        # Virtual clock: lets time windows be exercised (the changelog's
        # four-week retention, dataset evolution) without `sleep`, so
        # without making the suite slow or timing-dependent.
        self.clock_offset: float = 0.0

    # ── Rule management ──────────────────────────────────────────────────────

    def add(self, **kwargs: Any) -> Rule:
        rule = Rule(id=f"r{self._next_id}", **kwargs)
        self._next_id += 1
        self.rules.append(rule)
        return rule

    def remove(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        return len(self.rules) != before

    def clear(self) -> None:
        self.rules.clear()

    def reset_counters(self) -> None:
        self.request_counts.clear()
        self.last_query_params.clear()
        self.clock_offset = 0.0

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(self, path: str, params: dict[str, str]) -> int:
        """Records a request's passage and returns its rank (1-based).

        `last_query_params` carries its weight: it's what lets a consumer
        PROVE it really sent its `cursor`, its `filter` and its
        `start_date`, instead of merely tolerating their absence. A pipeline
        that forgot its cursor would otherwise pass all its tests — it would
        just reload the first page every time, with nothing to say so.
        """
        self.request_counts[path] = self.request_counts.get(path, 0) + 1
        self.last_query_params[path] = dict(params)
        return self.request_counts[path]

    def now(self) -> float:
        return time.time() + self.clock_offset

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def first(self, kind: Kind, path: str) -> Rule | None:
        """First active rule of the requested kind for this path."""
        for rule in self.rules:
            if rule.kind == kind and rule.matches(path):
                if rule.times is not None and rule.times <= 0:
                    continue
                return rule
        return None

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "scope": r.scope,
                "times_left": r.times,
                **{
                    field: value
                    for field, value in (
                        ("after_requests", r.after_requests),
                        ("retry_after_seconds", r.retry_after_seconds),
                        ("status", r.status),
                        ("seconds", r.seconds),
                        ("after_page", r.after_page),
                        ("mode", r.mode),
                        ("missing_scope", r.missing_scope),
                    )
                    if field in _FIELDS_BY_KIND.get(r.kind, frozenset())
                },
            }
            for r in self.rules
        ]


_FIELDS_BY_KIND: dict[str, frozenset[str]] = {
    "rate_limit": frozenset({"after_requests", "retry_after_seconds"}),
    "status": frozenset({"status"}),
    "latency": frozenset({"seconds"}),
    "page_drift": frozenset({"after_page", "mode"}),
    "auth_reject": frozenset({"status"}),
    "scope_reject": frozenset({"missing_scope"}),
    "cursor_reject": frozenset(),
}


engine = InjectionEngine()
