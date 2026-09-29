"""The `/__admin` control plane — outside the provider surface.

It exists for a precise reason: the mock runs in a CONTAINER at its
consumer's side (docker compose, GitHub Actions service, dev Deployment).
Outside the process, a test can no longer mutate state in Python — it needs
HTTP. Without this control plane, exercising a 429 or an incremental
extraction from insights360 would be impossible.

It is CLOSED by default (`PENNYLANE_MOCK_ADMIN_ENABLED`), and when closed the
surface doesn't exist at all — cf. the conditional mount in `app.py`. The
`__admin` prefix can't collide with any Pennylane path, which all live under
`/api/external/v2`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .evolution import _timestamp
from .injection import Kind, engine
from .settings import settings
from .state import state

router = APIRouter(prefix="/__admin", include_in_schema=False, tags=["mock control plane"])


def _instant(timestamp: str) -> datetime:
    text = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _reject(token: str | None) -> JSONResponse | None:
    if token != settings.admin_token:
        return JSONResponse(status_code=403, content={"error": "Forbidden", "status": 403})
    return None


class ResetRequest(BaseModel):
    seed: int | None = None


class InjectionRequest(BaseModel):
    """An injection rule. `times` absent = PERSISTENT failure.

    The distinction carries everything: a transient failure must be absorbed
    by the consumer's retry and leave the run green; a persistent failure
    must make it fail with a non-zero exit code. Both are worth testing, and
    they aren't tested by the same rule.
    """

    kind: Kind
    scope: str = "*"
    times: int | None = None
    after_requests: int = 0
    retry_after_seconds: int = 1
    status: int = 500
    seconds: float = 0.0
    after_page: int = 1
    mode: Literal["insert", "remove"] = "insert"
    missing_scope: str = "customer_invoices:readonly"


class ClockRequest(BaseModel):
    advance_seconds: float = Field(description="Offset to ADD to the mock's virtual clock.")


class MutationRequest(BaseModel):
    """Mutates an element and pushes its `updated_at` above all others.

    This is the incrementality test's tool: after this call, exactly ONE row
    must be reloaded by the consumer. If it reloads more, it isn't sending
    its cursor; if it reloads none, it isn't advancing it.
    """

    collection: str
    id: int
    fields: dict[str, Any] = Field(default_factory=dict)


class EvolutionRequest(BaseModel):
    steps: int = 1


@router.post("/reset")
def reset(request: ResetRequest, x_mock_admin_token: str | None = Header(default=None)) -> Any:
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    state.reset(seed=request.seed)
    return {"status": "reset", "seed": state.seed, "totals": state.totals()}


@router.get("/state")
def get_state(x_mock_admin_token: str | None = Header(default=None)) -> Any:
    """The mock's observability.

    `last_query_params_by_path` is the linchpin of downstream tests: it's
    what lets you PROVE that a consumer really sent its `cursor`, its
    `filter` or its `start_date`. Without this proof, a pipeline that forgot
    its cursor would pass all its tests — it would just reload the first
    page every time, which shows up in no assertion about the content.
    """
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    return {
        "seed": state.seed,
        "totals": state.totals(),
        "request_counts_by_path": dict(engine.request_counts),
        "last_query_params_by_path": dict(engine.last_query_params),
        "injections": engine.snapshot(),
        "clock_offset": engine.clock_offset,
        "evolution": {
            "enabled": settings.evolution_enabled,
            "interval": settings.evolution_interval,
            "rank": state.evolution.rank,
            "log": state.evolution.log[-20:],
        },
        "scopes": sorted(settings.scopes),
    }


@router.post("/inject")
def inject(request: InjectionRequest, x_mock_admin_token: str | None = Header(default=None)) -> Any:
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    rule = engine.add(**request.model_dump())
    return {"status": "injected", "rule": {"id": rule.id, **request.model_dump()}}


@router.delete("/inject/{rule_id}")
def remove_rule(rule_id: str, x_mock_admin_token: str | None = Header(default=None)) -> Any:
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    return {"status": "removed" if engine.remove(rule_id) else "unknown", "id": rule_id}


@router.post("/inject/clear")
def clear_injections(x_mock_admin_token: str | None = Header(default=None)) -> Any:
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    engine.clear()
    engine.reset_counters()
    return {"status": "cleared"}


@router.post("/clock")
def advance_clock(
    request: ClockRequest, x_mock_admin_token: str | None = Header(default=None)
) -> Any:
    """Advances the mock's VIRTUAL clock.

    Evolution tests NEVER sleep: they fast-forward time explicitly. This is
    what makes them deterministic and fast — a suite that `sleep(60)`s to
    see an event is a suite that eventually gets disabled.
    """
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    engine.clock_offset += request.advance_seconds
    state.advance_evolution(engine.now())
    return {"status": "advanced", "clock_offset": engine.clock_offset}


@router.post("/evolve")
def evolve(request: EvolutionRequest, x_mock_admin_token: str | None = Header(default=None)) -> Any:
    """Forces N evolution events, without touching the clock."""
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    state.evolution.force(state.dataset, request.steps)
    state.invalidate_caches()
    return {"status": "evolved", "rank": state.evolution.rank, "totals": state.totals()}


@router.post("/mutate")
def mutate(
    request: MutationRequest,
    http_request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> Any:
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    element = state.index().get((request.collection, request.id))
    if element is None:
        return JSONResponse(status_code=404, content={"error": "Not Found", "status": 404})

    element.update(request.fields)
    timestamp = _mutation_timestamp(request.collection)
    element["updated_at"] = timestamp
    family = request.collection
    if family in state.dataset["changelogs"]:
        state.dataset["changelogs"][family].append(
            {
                "id": element["id"],
                "operation": "update",
                "processed_at": timestamp,
                "created_at": element["created_at"],
                "updated_at": timestamp,
            }
        )
    state.invalidate_caches()
    del http_request
    return {"status": "mutated", "collection": family, "id": request.id, "updated_at": timestamp}


def _mutation_timestamp(collection: str) -> str:
    """A timestamp STRICTLY above every one in the collection.

    Two requirements that seem to contradict each other:

      • it must land above everything, otherwise a cursor set before the
        mutation wouldn't see it and the incrementality test would fail for
        a reason that has nothing to do with the consumer;
      • it must stay within the MOCK's time, not the wall clock's — a
        timestamp on September 2nd on a dataset anchored to July 15th would
        fall out of the changelog's retention window one way, then the
        other, depending on the day the suite runs.

    Hence: the current virtual instant, or one second after the most recent
    existing timestamp if that one has already passed it.
    """
    from .app import current_virtual_time

    elements = state.dataset.get(collection, [])
    latest = max(
        (e["updated_at"] for e in elements if isinstance(e, dict) and "updated_at" in e),
        default="",
    )
    candidate = current_virtual_time()
    if latest and _timestamp(candidate) <= latest:
        candidate = _instant(latest) + timedelta(seconds=1)
    return _timestamp(candidate)


@router.post("/scopes")
def update_scopes(
    body: dict[str, list[str]], x_mock_admin_token: str | None = Header(default=None)
) -> Any:
    """Redefines the token's scopes — the dialect's "403" lever.

    Removing `customer_invoices:readonly` reproduces exactly the most
    frequent failure in real Pennylane integrations: a token regenerated
    without a box checked. The error message NAMES the missing scope, and
    it's the only actionable information in the whole API.
    """
    if (rejection := _reject(x_mock_admin_token)) is not None:
        return rejection
    settings.scopes = frozenset(body.get("scopes", []))
    return {"status": "updated", "scopes": sorted(settings.scopes)}
