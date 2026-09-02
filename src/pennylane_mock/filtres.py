"""Le paramètre `filter` et le paramètre `sort` — le dialecte de sélection.

`filter` est un **tableau JSON passé en query string**, chaque objet portant
exactement `{field, operator, value}` (guide « Filter API Data », 2025-10-23) :

    ?filter=[{"field":"date","operator":"gteq","value":"2024-01-01"}]

Neuf opérateurs, pas un de plus : `eq`, `not_eq`, `lt`, `lteq`, `gt`, `gteq`,
`in`, `not_in`, `start_with`. Les filtres d'un même tableau se cumulent en ET.

`sort` est un nom de champ, préfixé de `-` pour l'ordre décroissant. Le défaut
déclaré par l'OpenAPI est **`-id`** sur presque toutes les listes — un
consommateur qui pagine sans expliciter son tri hérite donc du décroissant, ce
qui est l'inverse de l'intuition et vaut d'être rencontré en test.

┌─ CE QUI N'EST PAS VALIDÉ, ET POURQUOI ──────────────────────────────────────┐
│ L'OpenAPI déclare, endpoint par endpoint, QUELS champs sont filtrables et    │
│ avec quels opérateurs. Le mock accepte tout champ présent dans l'élément :   │
│ refuser un champ non déclaré demanderait de recopier 40 listes blanches      │
│ dont la doc reconnaît elle-même qu'elles bougent, et ferait échouer le mock  │
│ là où le fournisseur, lui, aurait entre-temps ajouté le champ.               │
│                                                                              │
│ En revanche un OPÉRATEUR inconnu rend 400 : la liste des neuf est courte,    │
│ stable, et un opérateur inventé côté consommateur est un vrai bug.           │
│                                                                              │
│ Ce choix est inscrit dans docs/UNVERIFIED-FIELDS.md.                         │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
from typing import Any

OPERATEURS = frozenset({"eq", "not_eq", "lt", "lteq", "gt", "gteq", "in", "not_in", "start_with"})


class FiltreInvalide(ValueError):
    """`filter` mal formé → 400."""


class TriInvalide(ValueError):
    """`sort` mal formé → 400."""


def _comparable(valeur: Any) -> Any:
    """Rend une valeur comparable de façon stable.

    Les montants du dialecte v2 sont des CHAÎNES (`"1234.56"`) : comparer
    `"900.00" < "1000.00"` lexicographiquement rendrait faux. On tente donc le
    nombre d'abord, et on retombe sur la chaîne — ce qui laisse les dates ISO
    se comparer correctement, puisqu'elles sont ordonnées lexicographiquement.
    """
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, str):
        try:
            return float(valeur)
        except ValueError:
            return valeur
    return valeur


def _teste(gauche: Any, operateur: str, droite: Any) -> bool:  # noqa: PLR0911
    if operateur == "in":
        return gauche in droite if isinstance(droite, list) else False
    if operateur == "not_in":
        return gauche not in droite if isinstance(droite, list) else True
    if operateur == "start_with":
        return isinstance(gauche, str) and gauche.lower().startswith(str(droite).lower())
    if operateur == "eq":
        return bool(gauche == droite)
    if operateur == "not_eq":
        return bool(gauche != droite)

    if gauche is None or droite is None:
        # Une comparaison d'ordre sur un champ nul n'a pas de sens : la ligne
        # ne matche pas, plutôt que de faire tomber la requête en TypeError.
        return False
    g, d = _comparable(gauche), _comparable(droite)
    if type(g) is not type(d):
        g, d = str(gauche), str(droite)
    if operateur == "lt":
        return bool(g < d)
    if operateur == "lteq":
        return bool(g <= d)
    if operateur == "gt":
        return bool(g > d)
    return bool(g >= d)


def analyser_filtre(brut: str | None) -> list[dict[str, Any]]:
    if not brut:
        return []
    try:
        charge = json.loads(brut)
    except ValueError as exc:
        raise FiltreInvalide("filter must be a JSON array of objects") from exc
    if not isinstance(charge, list):
        raise FiltreInvalide("filter must be a JSON array of objects")
    for entree in charge:
        if not isinstance(entree, dict) or {"field", "operator", "value"} - entree.keys():
            raise FiltreInvalide("each filter must have field, operator and value")
        if entree["operator"] not in OPERATEURS:
            raise FiltreInvalide(
                f"unknown operator {entree['operator']!r} "
                f"(available: {', '.join(sorted(OPERATEURS))})"
            )
    return list(charge)


def appliquer_filtre(
    elements: list[dict[str, Any]], filtres: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Les filtres d'un même tableau se cumulent en ET."""
    resultat = elements
    for f in filtres:
        champ, operateur, valeur = f["field"], f["operator"], f["value"]
        resultat = [e for e in resultat if _teste(e.get(champ), operateur, valeur)]
    return resultat


def appliquer_tri(
    elements: list[dict[str, Any]], brut: str | None, *, defaut: str = "-id"
) -> list[dict[str, Any]]:
    """`id` croissant, `-id` décroissant. Défaut `-id`, comme l'OpenAPI le déclare."""
    expression = (brut or defaut).strip()
    if not expression:
        expression = defaut
    decroissant = expression.startswith("-")
    champ = expression[1:] if decroissant else expression
    if not champ:
        raise TriInvalide("sort must be a field name, optionally prefixed with '-'")

    def cle(element: dict[str, Any]) -> tuple[int, int, float, str]:
        """Une clé de tri HOMOGÈNE, quoi que porte le champ.

        Un même champ peut porter des valeurs de natures différentes : les
        numéros de compte de la balance valent `"411000"` pour les généraux et
        `"411LUMIN"` pour les auxiliaires. Comparer les deux fait tomber
        `sorted` en TypeError — et il tombe sur la balance détaillée, pas sur
        la balance agrégée, donc pas dans le premier test qu'on écrit.

        D'où une clé à quatre composantes : nul en dernier, puis les nombres
        avant les chaînes, puis la valeur dans son propre espace.
        """
        valeur = element.get(champ)
        if valeur is None:
            return (1, 0, 0.0, "")
        compare = _comparable(valeur)
        if isinstance(compare, (int, float)) and not isinstance(compare, bool):
            return (0, 0, float(compare), "")
        return (0, 1, 0.0, str(compare))

    return sorted(elements, key=cle, reverse=decroissant)
