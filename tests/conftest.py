"""Test harness.

Two environment settings, set BEFORE importing the package (configuration is
read at import time):

  * the control plane `/__admin` is mounted — the mounting is conditional;
  * the evolution interval is pushed to 3600 s: no event fires on wall-clock
    time during the suite, even on a slow CI. Evolution tests advance time
    EXPLICITLY via `/__admin/clock` or `/__admin/evolve` — that's what makes
    them deterministic.
"""

from __future__ import annotations

import os

os.environ.setdefault("PENNYLANE_MOCK_ADMIN_ENABLED", "true")
os.environ.setdefault("PENNYLANE_MOCK_EVOLUTION_INTERVAL", "3600")

import pytest
from fastapi.testclient import TestClient

import pennylane_mock as mock

BASE = "/api/external/v2"

#: The credentials of a well-formed request. A single header — this is the
#: simplest authentication scheme of the five mocks in the ecosystem.
H = {"Authorization": "Bearer mock-pennylane-token"}
ADMIN = {"X-Mock-Admin-Token": "mock-admin-token"}


@pytest.fixture()
def client():
    """A client on a FRESHLY RESET state — before AND after, so that one test
    hands down neither an injection rule, nor an evolution event, nor an
    amputated scope to the next."""
    c = TestClient(mock.app)
    mock.settings.reload()
    mock.state.reset()
    yield c
    mock.settings.reload()
    mock.state.reset()


@pytest.fixture()
def donnees(client):  # noqa: ARG001 — the fixture chains the reset
    """The dataset, for tests that inspect it directly."""
    return mock.state.dataset


def tout_paginer(client, chemin: str, limite: int = 7) -> list[dict]:
    """The full pagination walk — the end-to-end tests' tool.

    It REPLAYS the filter on every page, as the documentation requires, and
    stops on a false `has_more`, not on a short page: that's the dialect's
    rule, and a helper that took the shortcut would mask precisely the defect
    being sought.
    """
    separateur = "&" if "?" in chemin else "?"
    elements: list[dict] = []
    curseur = None
    for _ in range(200):
        url = f"{chemin}{separateur}limit={limite}" + (f"&cursor={curseur}" if curseur else "")
        reponse = client.get(url, headers=H)
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        elements.extend(corps["items"])
        if not corps["has_more"]:
            return elements
        curseur = corps["next_cursor"]
    raise AssertionError(f"pagination did not finish after 200 pages on {chemin}")
