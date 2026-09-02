"""Enveloppe d'erreur — la forme déclarée par l'OpenAPI officiel de la v2.

┌─ DEUX SOURCES QUI NE DISENT PAS LA MÊME CHOSE ──────────────────────────────┐
│ Le guide « Error Handling & Status Codes » (relevé le 2026-02-06) montre     │
│                                                                              │
│     {"error": "unprocessable_entity", "message": "...", "details": {...}}    │
│                                                                              │
│ tandis que l'OpenAPI embarqué dans CHACUNE des 91 opérations GT de la        │
│ référence déclare, uniformément :                                            │
│                                                                              │
│     {"error": "<message lisible>", "status": <entier>}                      │
│                                                                              │
│ C'est l'OpenAPI qui fait foi ici : il est machine-readable, versionné avec   │
│ les endpoints, et c'est LUI que le fournisseur publie comme contrat. La      │
│ divergence est inscrite dans docs/UNVERIFIED-FIELDS.md — un relevé contre    │
│ une vraie instance la tranchera.                                             │
└──────────────────────────────────────────────────────────────────────────────┘

Deux exceptions de forme, attestées elles aussi :

  • **429** : le corps n'est PAS du JSON. C'est du texte brut,
    `Rate limit exceeded. Please retry in X seconds.` (doc « Rate Limiting in
    API v2 »). Un consommateur qui fait `response.json()` sur un 429 casse —
    c'est exactement le genre de piège qu'un mock doit poser.
  • **400** : l'OpenAPI déclare un `anyOf` de six formes. La plus simple
    (`{error, status}`) est celle qu'on émet ; les cinq autres décrivent des
    erreurs de validation de corps, donc des écritures — hors périmètre.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

#: Les messages exacts donnés en `example` par l'OpenAPI officiel. Centralisés
#: ici pour qu'une campagne de sondes contre une vraie instance les corrige en
#: un seul diff.
MESSAGE_401 = "The access token is invalid"
MESSAGE_404 = "Not Found"


def erreur(
    status_code: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """L'enveloppe d'erreur Pennylane : `{"error", "status"}`, rien d'autre.

    `additionalProperties: false` dans l'OpenAPI — ajouter une clé « utile »
    (un `code`, un `request_id`) ferait mentir le contrat.
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": message, "status": status_code},
        headers=headers or {},
    )


def erreur_scope(scope: str) -> JSONResponse:
    """Le 403 de scope manquant, au mot près (`example` de l'OpenAPI)."""
    return erreur(403, f'Access to this resource requires scope "{scope}".')


def erreur_jeton() -> JSONResponse:
    """401 — jeton absent, invalide ou expiré. Les trois cas sont indistincts
    chez le fournisseur : un seul message, aucun indice sur LEQUEL des trois."""
    return erreur(401, MESSAGE_401)


def erreur_introuvable() -> JSONResponse:
    return erreur(404, MESSAGE_404)


def erreur_debit(retry_after: int, headers: dict[str, str]) -> PlainTextResponse:
    """429 — corps en TEXTE BRUT, pas en JSON. Cf. l'encadré du module."""
    return PlainTextResponse(
        status_code=429,
        content=f"Rate limit exceeded. Please retry in {retry_after} seconds.",
        headers={**headers, "retry-after": str(retry_after)},
    )


def entetes_debit(limite: int, restant: int, reset: int) -> dict[str, str]:
    """Les en-têtes `ratelimit-*`, présents sur TOUTE réponse — pas seulement
    sur les 429. C'est ce qui permet à un consommateur de se réguler avant de
    se faire limiter, et un client qui ne les lit pas doit pouvoir être pris en
    défaut ici plutôt qu'en production."""
    return {
        "ratelimit-limit": str(limite),
        "ratelimit-remaining": str(max(0, restant)),
        "ratelimit-reset": str(reset),
    }


def detail_route_inconnue(request: Request) -> JSONResponse:
    """Une route inexistante rend l'enveloppe Pennylane, pas le 404 de FastAPI."""
    del request  # le fournisseur ne renvoie aucun écho du chemin demandé
    return erreur_introuvable()


#: Réutilisé sur chaque route (`responses=REPONSES_ERREUR`) : c'est ce qui fait
#: passer le contrat généré de « liste de chemins » à contrat véritable.
REPONSES_ERREUR: dict[int | str, dict[str, Any]] = {
    400: {
        "description": (
            "Paramètre invalide — `limit` hors bornes, `cursor` illisible, "
            "`filter` mal formé, ou `start_date` et `cursor` envoyés ensemble."
        )
    },
    401: {"description": "Jeton absent, invalide ou expiré. N'est PAS retentable."},
    403: {
        "description": (
            "Le jeton est valide mais ne porte pas le scope requis. "
            "N'est PAS retentable : il faut regénérer un jeton."
        )
    },
    404: {"description": "Ressource inconnue, ou appartenant à une autre société."},
    422: {"description": "Règle métier violée (écriture déséquilibrée, TVA incohérente…)."},
    429: {
        "description": (
            "Limite de débit atteinte (25 requêtes / 5 s au jeton). "
            "**Corps en texte brut, pas en JSON.** En-tête `retry-after` fourni."
        )
    },
    500: {"description": "Panne injectée."},
    503: {"description": "Panne transitoire injectée."},
}
