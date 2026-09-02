"""État mutable du serveur.

`state.dataset` est le monde ; `state.reset(seed=…)` le reconstruit ;
`state.avancer_evolution()` le fait vivre. S'y ajoute un index (type, id) →
entité, invalidé à chaque mutation — sans lui, chaque `GET /{id}` reparcourrait
la collection entière, ce qui se voit dès que le grand livre passe le millier
de lignes.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .evolution import Evolution
from .injection import engine
from .settings import settings


def build_dataset(seed: int = 42) -> dict[str, Any]:
    """Construit le monde comptable « Boréal Conseil »."""
    from .dataset.realiste import build_realiste_dataset

    return build_realiste_dataset(seed)


class MockState:
    """État serveur mutable, pour simuler des changements distants et des pannes."""

    def __init__(self) -> None:
        self.dataset: dict[str, Any] = {}
        self.seed: int = settings.seed
        self.evolution: Evolution = Evolution(self.seed, time.time())
        self._index: dict[tuple[str, int], dict[str, Any]] | None = None
        self.reset()

    def reset(self, seed: int | None = None) -> None:
        """Reconstruit le jeu de données et remet TOUT à la ligne de base.

        ⚠️ « La ligne de base » est celle DÉCLARÉE PAR L'ENVIRONNEMENT, pas un
        état vide. Trois choses en dépendent, et chacune a sa raison :

          • **la configuration** est relue (`settings.reload()`). Sans elle, un
            test qui a retiré un scope par `/__admin/scopes` le retire pour
            TOUS les suivants : le plan de contrôle mute un objet global, et
            reconstruire le jeu de données ne le rétablit pas. La panne est
            silencieuse et se manifeste des tests plus loin, sur un 403 qui n'a
            rien à voir avec ce qu'on croyait tester ;
          • **les règles d'injection** sont remises à la baseline de
            l'environnement. Si un `reset` les vidait, la première requête
            d'une suite effacerait une limite de débit configurée au niveau du
            compose ;
          • **l'évolution** est RÉARMÉE : la chronologie repart de zéro.
        """
        settings.reload()
        self.seed = settings.seed if seed is None else seed
        self.dataset = build_dataset(self.seed)
        engine.clear()
        engine.reset_counters()
        self.evolution = Evolution(self.seed, time.time())
        self.invalider_caches()
        _appliquer_injections_de_base()

    # ── Caches dérivés ───────────────────────────────────────────────────────

    def invalider_caches(self) -> None:
        """À appeler après TOUTE mutation du dataset (admin, évolution)."""
        self._index = None

    def avancer_evolution(self, maintenant: float) -> None:
        """Fait avancer la vie de l'entreprise ; invalide les caches si elle a bougé."""
        if self.evolution.avancer(self.dataset, maintenant):
            self.invalider_caches()

    def index(self) -> dict[tuple[str, int], dict[str, Any]]:
        """(collection, id) → élément, sur toutes les listes servies."""
        if self._index is None:
            index: dict[tuple[str, int], dict[str, Any]] = {}
            for cle, elements in self.dataset.items():
                if not isinstance(elements, list):
                    continue
                for element in elements:
                    if isinstance(element, dict) and isinstance(element.get("id"), int):
                        index[(cle, element["id"])] = element
            self._index = index
        return self._index

    def totals(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.dataset.items() if isinstance(v, list)}


def _appliquer_injections_de_base() -> None:
    """Règles d'injection déclarées par l'environnement, réappliquées à chaque reset.

    Permet à un docker-compose ou à un sidecar CI de faire tourner le mock en
    permanence dégradé (limite de débit basse, par exemple) sans qu'un test ne
    l'annule par inadvertance.
    """
    if (seuil := os.environ.get("PENNYLANE_MOCK_RATE_LIMIT_AFTER")) is not None:
        engine.add(
            kind="rate_limit",
            scope="/api/external/v2/*",
            after_requests=int(seuil),
            retry_after_seconds=int(os.environ.get("PENNYLANE_MOCK_RETRY_AFTER", "1")),
        )


state = MockState()
