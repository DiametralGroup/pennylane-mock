"""Modèles de base — l'enveloppe, l'erreur, et les marqueurs d'honnêteté.

Typer donne d'un coup le contrat OpenAPI, la page /docs et des formes
exploitables par les consommateurs. Mais typer POUSSE À INVENTER : dès qu'un
champ manque à la documentation, la tentation est de le déduire. La parade est
structurelle, et c'est la même que dans les quatre autres mocks :

  • `extra="allow"` partout — le modèle décrit ce qu'on SAIT, pas ce qui EST ;
  • `x-pennylane-confidence` sur tout champ non adossé à l'OpenAPI officiel ;
  • un test échoue si un champ `unverified` n'est pas inscrit dans
    docs/UNVERIFIED-FIELDS.md — l'honnêteté est une contrainte de build.

La source de vérité de ce mock est l'**OpenAPI embarqué dans chacune des 163
pages de référence** de pennylane.readme.io (relevé le 2026-09-02, fusionné en
une spec unique — cf. docs/EXTRACTION.md). Tout ce qui en vient est attesté.
Tout le reste est marqué.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def unverified(description: str) -> dict[str, Any]:
    """Marque un champ dont le nom, la forme ou les valeurs ne sont PAS attestés.

    À utiliser via `json_schema_extra`. Tout champ ainsi marqué DOIT figurer
    dans `docs/UNVERIFIED-FIELDS.md` — `tests/test_contract_is_current.py` le
    vérifie.
    """
    return {"x-pennylane-confidence": "unverified", "x-pennylane-note": description}


def invented(description: str) -> dict[str, Any]:
    """Marque un champ ou un comportement qui n'existe PAS chez Pennylane."""
    return {"x-pennylane-confidence": "invented", "x-pennylane-note": description}


class Permissif(BaseModel):
    """Base commune : les champs inconnus passent au lieu d'être rejetés.

    Un modèle strict transformerait chaque évolution de l'API réelle en panne
    du mock. Les modèles décrivent ce qui est émis, pas tout ce que Pennylane
    peut exposer.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Lien(Permissif):
    """Une collection imbriquée — `{"url": "…"}`, jamais un tableau.

    C'est la différence de forme la plus structurante entre v1 et v2 : les
    lignes d'une facture ne sont pas DANS la facture, elles sont derrière un
    lien. Un connecteur écrit pour la v1 lit une liste vide et charge zéro
    ligne sans erreur.
    """

    url: str


class Reference(Permissif):
    """Un pointeur vers une autre ressource — `{"id": 42, "url": "…"}`.

    Certaines références ne portent QUE `id` (`ledger_entry`,
    `billing_subscription`, `category_group`) ; d'autres portent aussi `url`.
    Le fournisseur n'est pas uniforme là-dessus, et `url` est donc optionnel.
    """

    id: int
    url: str | None = None


class Page[T](BaseModel):
    """L'enveloppe de pagination : `{items, has_more, next_cursor}`.

    `additionalProperties: false` chez le fournisseur — exactement trois clés.
    `next_cursor` est **`null`**, pas absent et pas `""`, quand il n'y a plus
    rien : un consommateur qui teste `if "next_cursor" in body` boucle à
    l'infini.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    has_more: bool = Field(description="Une page supplémentaire existe-t-elle ?")
    next_cursor: str | None = Field(
        default=None,
        description="Curseur de la page suivante ; `null` à la fin des résultats.",
    )


class ErreurPennylane(Permissif):
    """Le corps d'erreur — `{"error", "status"}`, et rien d'autre.

    ⚠️ Le guide « Error Handling & Status Codes » décrit une AUTRE forme
    (`{"error", "message", "details"}`). L'OpenAPI, lui, déclare celle-ci
    uniformément sur les 91 opérations GET. Divergence inscrite dans
    docs/UNVERIFIED-FIELDS.md ; c'est l'OpenAPI qui est suivi.

    Le **429 n'a pas de corps JSON du tout** : il rend du texte brut.
    """

    error: str
    status: int


#: Réutilisé sur chaque route (`responses=REPONSES_ERREUR`) : sans lui, le
#: contrat généré ne décrirait que le chemin heureux, et un consommateur ne
#: saurait pas quelles pannes il doit savoir traiter.
REPONSES_ERREUR: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErreurPennylane,
        "description": (
            "Paramètre invalide : `limit` hors bornes (elle n'est PAS rabotée), "
            "`cursor` illisible, `filter` mal formé, ou `start_date` et `cursor` "
            "envoyés ensemble sur un changelog."
        ),
    },
    401: {
        "model": ErreurPennylane,
        "description": (
            "Jeton absent, invalide ou expiré — les trois cas sont indistincts. "
            "N'est PAS retentable."
        ),
    },
    403: {
        "model": ErreurPennylane,
        "description": (
            "Le jeton ne porte pas le scope requis. Le message NOMME le scope "
            "manquant. N'est PAS retentable."
        ),
    },
    404: {
        "model": ErreurPennylane,
        "description": "Ressource inconnue, ou appartenant à une autre société.",
    },
    422: {
        "model": ErreurPennylane,
        "description": (
            "Règle métier violée. Sur un changelog : `start_date` au-delà de la "
            "rétention de quatre semaines."
        ),
    },
    429: {
        "description": (
            "Limite de débit atteinte — 25 requêtes / 5 s au jeton. "
            "**Le corps est du TEXTE BRUT, pas du JSON** ; en-tête `retry-after`. "
            "Les en-têtes `ratelimit-*` sont présents sur TOUTES les réponses."
        ),
        "content": {"text/plain": {"schema": {"type": "string"}}},
    },
    500: {"model": ErreurPennylane, "description": "Panne injectée."},
    503: {"model": ErreurPennylane, "description": "Panne transitoire injectée."},
}
