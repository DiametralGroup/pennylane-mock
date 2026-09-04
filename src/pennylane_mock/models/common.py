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

    `next_cursor` est **`null`**, pas absent et pas `""`, quand il n'y a plus
    rien : un consommateur qui teste `if "next_cursor" in body` boucle à
    l'infini.

    ┌─ L'ENVELOPPE N'EST PAS UNIFORME, ET L'OPENAPI NE LE DIT PAS ───────────┐
    │ Ce modèle portait `additionalProperties: false` — « exactement trois    │
    │ clés » — sur la foi de l'OpenAPI officiel. Confronté à une instance     │
    │ réelle le 2026-09-04 (`scripts/compare_real.py`), c'est faux : QUATRE   │
    │ collections sur seize ajoutent une pagination par OFFSET à côté du      │
    │ curseur — `current_page`, `per_page`, `total_items`, `total_pages`.     │
    │                                                                         │
    │ Ce sont `journals`, `ledger_accounts`, `ledger_entries` et              │
    │ `fiscal_years`. Pas `ledger_entry_lines`, pourtant de la même famille : │
    │ il n'y a donc aucune règle à deviner, seulement une observation à       │
    │ reproduire.                                                             │
    │                                                                         │
    │ Le mock affirmait donc une régularité que le fournisseur n'a pas — et   │
    │ un mock plus régulier que la réalité est le même défaut qu'un mock plus │
    │ permissif : il valide du code qui casse ailleurs. Un consommateur qui   │
    │ aurait voulu afficher un total, compter les pages ou court-circuiter le │
    │ curseur aurait trouvé le champ en production et pas ici.                │
    │                                                                         │
    │ Et elles valent `null` : présentes, vides. Un consommateur qui teste    │
    │ leur PRÉSENCE pour choisir son mode de pagination les trouve, bascule    │
    │ sur l'offset, et lit `null` partout — sans une erreur.                   │
    │                                                                          │
    │ D'où `extra="allow"` : les quatre clés sont rendues là où elles ont été  │
    │ OBSERVÉES, et nulle part ailleurs. Cf. docs/UNVERIFIED-FIELDS.md.        │
    └─────────────────────────────────────────────────────────────────────────┘
    """

    model_config = ConfigDict(extra="allow")

    #: Pagination par OFFSET, servie par les seules collections où elle a été
    #: observée. Déclarée ici POUR LE CONTRAT : la réponse est un `JSONResponse`
    #: bâti sur le dict de `paginer`, donc ces clés sont réellement ABSENTES
    #: ailleurs, et non rendues à `null`. Rendre `"total_pages": null` sur une
    #: collection qui ne la porte pas serait un troisième dialecte, inventé.
    current_page: int | None = Field(
        default=None, description="Rang de la page — `null` sous curseur."
    )
    per_page: int | None = Field(default=None, description="Taille de page — `null` sous curseur.")
    total_items: int | None = Field(
        default=None, description="Total d'éléments — `null` sous curseur."
    )
    total_pages: int | None = Field(
        default=None, description="Total de pages — `null` sous curseur."
    )

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
