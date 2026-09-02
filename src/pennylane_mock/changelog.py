"""Les endpoints `/changelogs/*` — l'extraction incrémentale native.

C'est LE mécanisme que le fournisseur recommande pour se synchroniser : on
poll un journal d'événements plutôt que de relister l'univers. Le dialecte a
quatre règles, toutes reproduites ici parce que chacune peut casser un
connecteur en production sans se voir en test :

  1. **Ordre chronologique croissant** — le plus ancien d'abord. Un
     consommateur qui suppose l'inverse pose son point de reprise sur le
     PREMIER événement de la page et reperd tout à chaque passage.

  2. **Rétention de quatre semaines.** Une `start_date` plus ancienne rend
     **422**, pas une liste tronquée. C'est la différence entre « je n'ai rien
     reçu, donc rien n'a bougé » et « ma fenêtre est trop large » : sans le
     422, un pipeline arrêté cinq semaines croirait avoir rattrapé son retard.

  3. **`start_date` et `cursor` sont EXCLUSIFS** — les deux ensemble rendent
     **400**. La pagination continue une fenêtre ; elle n'en ouvre pas une
     nouvelle. C'est ce qui interdit le bug classique « je renvoie ma
     start_date à chaque page » et rejoue la même première page en boucle.

  4. **Le point de reprise ne s'avance qu'une fois `has_more` faux.** Le mock
     ne peut pas l'imposer, mais il peut le rendre observable : le plan de
     contrôle `/__admin/state` expose les derniers paramètres reçus par
     chemin, ce qui permet à un test aval de PROUVER que le consommateur a
     paginé jusqu'au bout avant de bouger sa borne.

Un événement porte l'ID, l'opération et trois horodatages — **jamais** l'état
de la ressource. Il faut un second appel pour l'obtenir, et le fournisseur
recommande de le faire par lots :
`filter=[{"field":"id","operator":"in","value":[…]}]`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .settings import settings


class FenetreTropAncienne(ValueError):
    """`start_date` au-delà de la rétention → 422."""


class ParametresExclusifs(ValueError):
    """`start_date` ET `cursor` dans la même requête → 400."""


class DateInvalide(ValueError):
    """`start_date` qui n'est pas du RFC 3339 → 400."""


def analyser_start_date(brut: str | None) -> datetime | None:
    """RFC 3339, `Z` accepté — c'est la forme que le fournisseur émet."""
    if not brut:
        return None
    texte = brut.strip()
    if texte.endswith("Z"):
        texte = texte[:-1] + "+00:00"
    try:
        quand = datetime.fromisoformat(texte)
    except ValueError as exc:
        raise DateInvalide("start_date must follow RFC3339 (e.g. 2026-07-15T09:00:00Z)") from exc
    return quand if quand.tzinfo else quand.replace(tzinfo=UTC)


def verifier_fenetre(depuis: datetime | None, maintenant: datetime) -> None:
    """La rétention de quatre semaines. Au-delà : 422, pas une liste tronquée."""
    if depuis is None:
        return
    limite = maintenant - timedelta(days=settings.changelog_retention_days)
    if depuis < limite:
        raise FenetreTropAncienne(
            f"start_date is older than the {settings.changelog_retention_days}-day retention window"
        )


def selectionner(
    evenements: list[dict[str, Any]],
    depuis: datetime | None,
    maintenant: datetime,
) -> list[dict[str, Any]]:
    """Les événements retenus, postérieurs à la borne, en ordre chronologique.

    ┌─ LA RÉTENTION PURGE, ELLE NE SE CONTENTE PAS DE REFUSER ────────────────┐
    │ « Changes are retained for 4 weeks. » Un événement plus ancien n'existe  │
    │ PLUS : il n'est pas seulement inaccessible par `start_date`, il ne       │
    │ figure pas non plus dans la réponse sans borne. Un mock qui servirait    │
    │ tout l'historique apprendrait au consommateur qu'une resynchronisation   │
    │ complète est possible par le changelog — elle ne l'est pas, et c'est     │
    │ exactement ce qui casse un pipeline arrêté cinq semaines.                │
    └─────────────────────────────────────────────────────────────────────────┘

    Le tri est refait ici plutôt que supposé : les événements d'évolution sont
    APPENDUS au journal du jeu de base, donc la liste brute n'est pas triée dès
    que le monde a bougé. Un consommateur qui reçoit des événements dans le
    désordre pose un point de reprise faux, et le fait silencieusement.
    """
    limite = maintenant - timedelta(days=settings.changelog_retention_days)
    retenus = [e for e in evenements if _instant(e["processed_at"]) >= limite]
    ordonnes = sorted(retenus, key=lambda e: (e["processed_at"], e["id"]))
    if depuis is None:
        return ordonnes
    borne = depuis.astimezone(UTC)
    return [e for e in ordonnes if _instant(e["processed_at"]) >= borne]


def _instant(horodatage: str) -> datetime:
    texte = horodatage[:-1] + "+00:00" if horodatage.endswith("Z") else horodatage
    quand = datetime.fromisoformat(texte)
    return quand if quand.tzinfo else quand.replace(tzinfo=UTC)
