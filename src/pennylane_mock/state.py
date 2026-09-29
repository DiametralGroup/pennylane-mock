"""Mutable server state.

`state.dataset` is the world; `state.reset(seed=…)` rebuilds it;
`state.advance_evolution()` makes it live. Alongside that, an index
(type, id) → entity, invalidated on every mutation — without it, every
`GET /{id}` would walk the entire collection, which becomes visible as soon
as the ledger passes a thousand rows.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .evolution import Evolution
from .injection import engine
from .settings import settings


def build_dataset(seed: int = 42) -> dict[str, Any]:
    """Builds the "Boréal Conseil" accounting world."""
    from .dataset.realiste import build_realiste_dataset

    return build_realiste_dataset(seed)


class MockState:
    """Mutable server state, used to simulate remote changes and failures."""

    def __init__(self) -> None:
        self.dataset: dict[str, Any] = {}
        self.seed: int = settings.seed
        self.evolution: Evolution = Evolution(self.seed, time.time())
        self._index: dict[tuple[str, int], dict[str, Any]] | None = None
        self.reset()

    def reset(self, seed: int | None = None) -> None:
        """Rebuilds the dataset and resets EVERYTHING to the baseline.

        WARNING: "the baseline" is the one DECLARED BY THE ENVIRONMENT, not
        an empty state. Three things depend on this, each for its own
        reason:

          • **configuration** is reread (`settings.reload()`). Without it, a
            test that removed a scope via `/__admin/scopes` would remove it
            for ALL the following ones: the control plane mutates a global
            object, and rebuilding the dataset doesn't restore it. The
            failure is silent and shows up tests later, on a 403 that has
            nothing to do with what was being tested;
          • **injection rules** are reset to the environment's baseline. If
            a `reset` cleared them, the first request of a suite would wipe
            out a rate limit configured at the compose level;
          • **evolution** is REARMED: the timeline starts over from zero.
        """
        settings.reload()
        self.seed = settings.seed if seed is None else seed
        self.dataset = build_dataset(self.seed)
        engine.clear()
        engine.reset_counters()
        self.evolution = Evolution(self.seed, time.time())
        self.invalidate_caches()
        _apply_base_injections()

    # ── Derived caches ───────────────────────────────────────────────────────

    def invalidate_caches(self) -> None:
        """Call after ANY mutation of the dataset (admin, evolution)."""
        self._index = None

    def advance_evolution(self, now: float) -> None:
        """Advances the company's life; invalidates caches if it moved."""
        if self.evolution.advance(self.dataset, now):
            self.invalidate_caches()

    def index(self) -> dict[tuple[str, int], dict[str, Any]]:
        """(collection, id) → element, across all served lists."""
        if self._index is None:
            index: dict[tuple[str, int], dict[str, Any]] = {}
            for key, elements in self.dataset.items():
                if not isinstance(elements, list):
                    continue
                for element in elements:
                    if isinstance(element, dict) and isinstance(element.get("id"), int):
                        index[(key, element["id"])] = element
            self._index = index
        return self._index

    def totals(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.dataset.items() if isinstance(v, list)}


def _apply_base_injections() -> None:
    """Injection rules declared by the environment, reapplied on every reset.

    Lets a docker-compose or a CI sidecar keep the mock permanently degraded
    (a low rate limit, for instance) without a test accidentally cancelling
    it.
    """
    if (threshold := os.environ.get("PENNYLANE_MOCK_RATE_LIMIT_AFTER")) is not None:
        engine.add(
            kind="rate_limit",
            scope="/api/external/v2/*",
            after_requests=int(threshold),
            retry_after_seconds=int(os.environ.get("PENNYLANE_MOCK_RETRY_AFTER", "1")),
        )


state = MockState()
