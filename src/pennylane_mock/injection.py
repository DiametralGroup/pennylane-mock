"""Injection de pannes — le vrai intérêt d'un mock.

« The point of the mock is to reproduce failure modes, not just happy paths »
(spec insights360, §4.1). Des règles déclaratives, pilotables par HTTP via
`/__admin`, parce que le mock tourne en CONTENEUR chez le consommateur : hors
du processus, on ne peut plus muter l'état en Python.

Un SEUL point de dispatch, ordonné, évalué AVANT l'authentification, pour que
`auth_reject` puisse préempter. Chaque règle porte un compteur `times`
optionnel : une panne « transitoire » doit cesser d'elle-même, sinon on ne
teste pas un retry, on teste un échec.

┌─ LES DEUX KINDS PROPRES À PENNYLANE ────────────────────────────────────────┐
│ `scope_reject`  — force un 403 « Access to this resource requires scope      │
│                   "x". » sur un périmètre donné. C'est la panne la plus      │
│                   fréquente en intégration réelle (un jeton régénéré sans    │
│                   une case cochée), et la seule qui NOMME sa cause.          │
│ `rate_limit`    — 429 à corps TEXTE, en-tête `retry-after`, et les en-têtes  │
│                   `ratelimit-*` qui, eux, partent sur toutes les réponses.   │
│                   Un client qui appelle `.json()` sur le 429 casse ici.      │
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
    """Une règle d'injection.

    `scope` est un motif glob sur le chemin (`/api/external/v2/customer_invoices`,
    `/api/external/v2/*`, `*`), ce qui permet de viser une ressource précise
    sans énumérer ses routes.
    """

    id: str
    kind: Kind
    scope: str = "*"
    #: Applications restantes. None = illimité. C'est ce qui fait la différence
    #: entre une panne transitoire (que le retry doit absorber) et une panne
    #: persistante (qui doit faire échouer le run avec un code non nul).
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
    scope_manquant: str = "customer_invoices:readonly"

    extra: dict[str, Any] = field(default_factory=dict)

    def matches(self, path: str) -> bool:
        return fnmatch.fnmatch(path, self.scope)

    def consume(self) -> bool:
        """Décrémente le compteur. Rend False quand la règle est épuisée."""
        if self.times is None:
            return True
        if self.times <= 0:
            return False
        self.times -= 1
        return True


class InjectionEngine:
    """Le moteur, et le compteur de requêtes par chemin dont il dépend."""

    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self.request_counts: dict[str, int] = {}
        self.last_query_params: dict[str, dict[str, str]] = {}
        self._next_id = 1
        # Horloge virtuelle : permet d'éprouver des fenêtres temporelles (la
        # rétention de 4 semaines du changelog, l'évolution du jeu de données)
        # sans `sleep`, donc sans rendre la suite lente ni dépendante du timing.
        self.clock_offset: float = 0.0

    # ── Gestion des règles ───────────────────────────────────────────────────

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
        """Enregistre le passage d'une requête et rend son rang (1-based).

        `last_query_params` est porteur : c'est ce qui permet à un consommateur
        de PROUVER qu'il a bien envoyé son `cursor`, son `filter` et sa
        `start_date`, au lieu de simplement tolérer leur absence. Un pipeline
        qui aurait oublié son curseur passerait sinon tous ses tests — il
        rechargerait la première page à chaque fois, sans que rien ne le dise.
        """
        self.request_counts[path] = self.request_counts.get(path, 0) + 1
        self.last_query_params[path] = dict(params)
        return self.request_counts[path]

    def now(self) -> float:
        return time.time() + self.clock_offset

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def first(self, kind: Kind, path: str) -> Rule | None:
        """Première règle active du type demandé pour ce chemin."""
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
                    champ: valeur
                    for champ, valeur in (
                        ("after_requests", r.after_requests),
                        ("retry_after_seconds", r.retry_after_seconds),
                        ("status", r.status),
                        ("seconds", r.seconds),
                        ("after_page", r.after_page),
                        ("mode", r.mode),
                        ("scope_manquant", r.scope_manquant),
                    )
                    if champ in _CHAMPS_PAR_KIND.get(r.kind, frozenset())
                },
            }
            for r in self.rules
        ]


_CHAMPS_PAR_KIND: dict[str, frozenset[str]] = {
    "rate_limit": frozenset({"after_requests", "retry_after_seconds"}),
    "status": frozenset({"status"}),
    "latency": frozenset({"seconds"}),
    "page_drift": frozenset({"after_page", "mode"}),
    "auth_reject": frozenset({"status"}),
    "scope_reject": frozenset({"scope_manquant"}),
    "cursor_reject": frozenset(),
}


engine = InjectionEngine()
