"""Le plan de contrôle `/__admin` — hors de la surface fournisseur.

Il existe pour une raison précise : le mock tourne en CONTENEUR chez son
consommateur (docker compose, service GitHub Actions, Deployment de dev). Hors
du processus, un test ne peut plus muter l'état en Python — il lui faut du
HTTP. Sans ce plan, éprouver un 429 ou une extraction incrémentale depuis
insights360 serait impossible.

Il est FERMÉ par défaut (`PENNYLANE_MOCK_ADMIN_ENABLED`), et quand il est
fermé la surface n'existe pas du tout — cf. le montage conditionnel dans
`app.py`. Le préfixe `__admin` ne peut collisionner avec aucun chemin
Pennylane, qui vivent tous sous `/api/external/v2`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .evolution import _horodatage
from .injection import Kind, engine
from .settings import settings
from .state import state

router = APIRouter(prefix="/__admin", include_in_schema=False, tags=["mock control plane"])


def _instant(horodatage: str) -> datetime:
    texte = horodatage[:-1] + "+00:00" if horodatage.endswith("Z") else horodatage
    quand = datetime.fromisoformat(texte)
    return quand if quand.tzinfo else quand.replace(tzinfo=UTC)


def _refuse(jeton: str | None) -> JSONResponse | None:
    if jeton != settings.admin_token:
        return JSONResponse(status_code=403, content={"error": "Forbidden", "status": 403})
    return None


class DemandeReset(BaseModel):
    seed: int | None = None


class DemandeInjection(BaseModel):
    """Une règle d'injection. `times` absent = panne PERSISTANTE.

    La distinction porte tout : une panne transitoire doit être absorbée par le
    retry du consommateur et laisser le run vert ; une panne persistante doit
    le faire échouer avec un code de sortie non nul. Les deux se testent, et
    elles ne se testent pas avec la même règle.
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
    scope_manquant: str = "customer_invoices:readonly"


class DemandeHorloge(BaseModel):
    advance_seconds: float = Field(description="Décalage à AJOUTER à l'horloge virtuelle du mock.")


class DemandeMutation(BaseModel):
    """Mute un élément et pousse son `updated_at` au-dessus de tous les autres.

    C'est l'outil du test d'incrémentalité : après cet appel, exactement UNE
    ligne doit être rechargée par le consommateur. S'il en recharge plus, il
    n'envoie pas son curseur ; s'il n'en recharge aucune, il ne l'avance pas.
    """

    collection: str
    id: int
    champs: dict[str, Any] = Field(default_factory=dict)


class DemandeEvolution(BaseModel):
    pas: int = 1


@router.post("/reset")
def reset(demande: DemandeReset, x_mock_admin_token: str | None = Header(default=None)) -> Any:
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    state.reset(seed=demande.seed)
    return {"status": "reset", "seed": state.seed, "totals": state.totals()}


@router.get("/state")
def etat(x_mock_admin_token: str | None = Header(default=None)) -> Any:
    """L'observabilité du mock.

    `last_query_params_by_path` est la clé de voûte des tests aval : c'est ce
    qui permet de PROUVER qu'un consommateur a bien envoyé son `cursor`, son
    `filter` ou sa `start_date`. Sans cette preuve, un pipeline qui aurait
    oublié son curseur passerait tous ses tests — il rechargerait simplement
    la première page à chaque fois, ce qui ne se voit dans aucune assertion
    portant sur le contenu.
    """
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
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
            "rang": state.evolution.rang,
            "journal": state.evolution.journal[-20:],
        },
        "scopes": sorted(settings.scopes),
    }


@router.post("/inject")
def injecter(
    demande: DemandeInjection, x_mock_admin_token: str | None = Header(default=None)
) -> Any:
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    regle = engine.add(**demande.model_dump())
    return {"status": "injected", "rule": {"id": regle.id, **demande.model_dump()}}


@router.delete("/inject/{rule_id}")
def retirer(rule_id: str, x_mock_admin_token: str | None = Header(default=None)) -> Any:
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    return {"status": "removed" if engine.remove(rule_id) else "unknown", "id": rule_id}


@router.post("/inject/clear")
def vider(x_mock_admin_token: str | None = Header(default=None)) -> Any:
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    engine.clear()
    engine.reset_counters()
    return {"status": "cleared"}


@router.post("/clock")
def horloge(demande: DemandeHorloge, x_mock_admin_token: str | None = Header(default=None)) -> Any:
    """Avance l'horloge VIRTUELLE du mock.

    Les tests d'évolution ne dorment JAMAIS : ils font défiler le temps
    explicitement. C'est ce qui les rend déterministes et rapides — une suite
    qui `sleep(60)` pour voir un événement est une suite qu'on finit par
    désactiver.
    """
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    engine.clock_offset += demande.advance_seconds
    state.avancer_evolution(engine.now())
    return {"status": "advanced", "clock_offset": engine.clock_offset}


@router.post("/evolve")
def evoluer(
    demande: DemandeEvolution, x_mock_admin_token: str | None = Header(default=None)
) -> Any:
    """Force N événements d'évolution, sans toucher à l'horloge."""
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    state.evolution.forcer(state.dataset, demande.pas)
    state.invalider_caches()
    return {"status": "evolved", "rang": state.evolution.rang, "totals": state.totals()}


@router.post("/mutate")
def muter(
    demande: DemandeMutation,
    request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> Any:
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    element = state.index().get((demande.collection, demande.id))
    if element is None:
        return JSONResponse(status_code=404, content={"error": "Not Found", "status": 404})

    element.update(demande.champs)
    horodatage = _horodatage_de_mutation(demande.collection)
    element["updated_at"] = horodatage
    famille = demande.collection
    if famille in state.dataset["changelogs"]:
        state.dataset["changelogs"][famille].append(
            {
                "id": element["id"],
                "operation": "update",
                "processed_at": horodatage,
                "created_at": element["created_at"],
                "updated_at": horodatage,
            }
        )
    state.invalider_caches()
    del request
    return {"status": "mutated", "collection": famille, "id": demande.id, "updated_at": horodatage}


def _horodatage_de_mutation(collection: str) -> str:
    """Un horodatage STRICTEMENT au-dessus de tous ceux de la collection.

    Deux exigences qui se contredisent en apparence :

      • il doit passer au-dessus de tout, sinon un curseur posé avant la
        mutation ne la verrait pas et le test d'incrémentalité échouerait pour
        une raison qui n'a rien à voir avec le consommateur ;
      • il doit rester dans le TEMPS DU MOCK, pas celui de l'horloge murale —
        un horodatage au 2 septembre sur un jeu ancré au 15 juillet sortirait
        de la fenêtre de rétention du changelog dans un sens, puis dans l'autre
        selon le jour où la suite tourne.

    D'où : l'instant virtuel courant, ou une seconde après le plus récent
    horodatage existant si celui-ci l'a déjà dépassé.
    """
    from .app import maintenant_virtuel

    elements = state.dataset.get(collection, [])
    dernier = max(
        (e["updated_at"] for e in elements if isinstance(e, dict) and "updated_at" in e),
        default="",
    )
    candidat = maintenant_virtuel()
    if dernier and _horodatage(candidat) <= dernier:
        candidat = _instant(dernier) + timedelta(seconds=1)
    return _horodatage(candidat)


@router.post("/scopes")
def scopes(
    corps: dict[str, list[str]], x_mock_admin_token: str | None = Header(default=None)
) -> Any:
    """Redéfinit les scopes du jeton — le levier « 403 » du dialecte.

    Retirer `customer_invoices:readonly` reproduit exactement la panne la plus
    fréquente en intégration Pennylane : un jeton régénéré sans une case
    cochée. Le message d'erreur NOMME le scope manquant, et c'est la seule
    information actionnable de toute l'API.
    """
    if (refus := _refuse(x_mock_admin_token)) is not None:
        return refus
    settings.scopes = frozenset(corps.get("scopes", []))
    return {"status": "updated", "scopes": sorted(settings.scopes)}
