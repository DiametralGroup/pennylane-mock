"""Pennylane API mock (Company API v2) — container image and Python package.

The surface reproduces the **91 documented GET operations** of the v2 API
(https://pennylane.readme.io), served on ONE coherent accounting dataset —
the books of "Boréal Conseil", the French IT consultancy that already
populates `boondmanager-mock`, `entra-mock`, `linkedin-mock` and `ga-mock`.
The world EVOLVES over time to exercise incremental extraction (cf.
evolution.py and changelog.py).

Two usage modes, deliberately both maintained:

  • **In-process** — `TestClient(pennylane_mock.app)`. The property that
    must not be lost: the application the stack queries IS the one the
    tests exercise.

  • **Containerized** — `python -m pennylane_mock`, in docker compose as in
    a CI service. This is the mode that makes the `/__admin` control plane
    indispensable: outside the process, state can no longer be mutated in
    Python.

Re-exports so nothing needs to know the internal structure:

    app                  the FastAPI application
    openapi_contract      the PUBLISHED contract (without /__admin or /health)
    state                the mutable state (dataset, reset, evolution)
    engine               the failure-injection engine
    settings             configuration reread from the environment
    build_dataset        dataset construction
"""

from __future__ import annotations

from .app import app, openapi_contract
from .injection import engine
from .settings import settings
from .state import build_dataset, state

__all__ = [
    "app",
    "build_dataset",
    "engine",
    "openapi_contract",
    "settings",
    "state",
]

__version__ = "0.2.0"
