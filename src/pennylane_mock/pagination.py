"""Pagination par curseur — le cinquième dialecte de pagination de l'écosystème.

    {"items": [...], "has_more": true, "next_cursor": "eyJhZnRlciI6IDEwMH0"}

À comparer aux quatre autres mocks : `page`/`maxResults` chez BoondManager,
`@odata.nextLink` chez Graph, `start`/`count` Rest.li chez LinkedIn,
`limit`/`offset` chez GA4. Un connecteur qui aurait « une » boucle de
pagination générique se casse ici, et c'est le but.

┌─ TROIS PIÈGES REPRODUITS EXPRÈS ────────────────────────────────────────────┐
│ 1. LE CURSEUR N'ENCODE PAS LES FILTRES. La documentation est explicite :     │
│    « Omitting the filters on page 2+ will return unfiltered results from     │
│    the cursor position. » Donc PAS de 400, PAS d'avertissement — des lignes  │
│    en trop, silencieusement. Un pipeline qui oublie de rejouer son `filter`  │
│    charge des lignes qu'il croyait avoir exclues. Reproduit tel quel.        │
│                                                                              │
│ 2. `limit` HORS BORNES REND 400, il n'est pas raboté. 1..100 sur les listes  │
│    ordinaires, 1..1000 sur les changelogs — deux plafonds, déclarés ainsi    │
│    endpoint par endpoint dans l'OpenAPI officiel.                            │
│                                                                              │
│ 3. `next_cursor` est `null` — pas absent, pas "" — quand `has_more` est      │
│    faux. `additionalProperties: false` : les trois clés, toujours, et rien   │
│    d'autre.                                                                  │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ CE QUE LE CURSEUR EST VRAIMENT ────────────────────────────────────────────┐
│ La documentation du fournisseur donne TROIS encodages incompatibles :        │
│   • le guide de pagination         : `eyJpZCI6MTAwfQ==`  → {"id":100}        │
│   • l'exemple de /bank_accounts    : `dXBkYXRlZF9hdDoxNjc0MTIzNDU2`          │
│                                                    → updated_at:1674123456   │
│   • l'exemple des changelogs       : `MjAyNS0wMS0wOVQwODoyNDozOC44MTI0NTha`  │
│                                                    → 2025-01-09T08:24:38…Z   │
│                                                                              │
│ Trois formes, un seul point commun : c'est du base64. La conclusion qui      │
│ s'impose est celle que la doc énonce elle-même — « the cursor is an opaque   │
│ string » — et un consommateur qui le décode s'adosse à un détail             │
│ d'implémentation qui a déjà changé trois fois.                               │
│                                                                              │
│ Le mock émet donc du base64url de JSON, la forme du guide de pagination,     │
│ et l'inscrit dans docs/UNVERIFIED-FIELDS.md. Il ne SIGNE pas le curseur :    │
│ ce serait inventer une sévérité que le fournisseur n'a pas.                  │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from .settings import settings


class CurseurInvalide(ValueError):
    """Curseur illisible → 400, comme chez le fournisseur."""


class LimiteInvalide(ValueError):
    """`limit` hors bornes → 400. Le message porte les bornes réelles."""


def encoder_curseur(charge: dict[str, Any]) -> str:
    """base64url SANS padding — le `=` final est un caractère à échapper en
    query string, et les exemples du fournisseur en portent tantôt, tantôt pas."""
    brut = json.dumps(charge, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(brut).rstrip(b"=").decode()


def decoder_curseur(curseur: str) -> dict[str, Any]:
    try:
        brut = base64.urlsafe_b64decode(curseur + "=" * (-len(curseur) % 4))
        charge = json.loads(brut)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CurseurInvalide("Invalid cursor") from exc
    if not isinstance(charge, dict):
        raise CurseurInvalide("Invalid cursor")
    return charge


def limite_demandee(brut: str | None, *, maximum: int | None = None) -> int:
    """Valide `limit` et rend la valeur effective.

    Le fournisseur REFUSE une valeur hors bornes au lieu de la raboter. On
    reproduit ce refus, y compris pour une valeur non entière — un `limit=abc`
    silencieusement ramené à 20 rendrait un test vert sur un client cassé.
    """
    plafond = settings.limite_max if maximum is None else maximum
    if brut is None or brut == "":
        return settings.limite_defaut
    try:
        valeur = int(brut)
    except ValueError as exc:
        raise LimiteInvalide(f"limit must be an integer between 1 and {plafond}") from exc
    if valeur < 1 or valeur > plafond:
        raise LimiteInvalide(f"limit must be between 1 and {plafond}")
    return valeur


def paginer(
    elements: list[dict[str, Any]],
    *,
    curseur: str | None,
    limite: int,
    cle: str = "id",
) -> dict[str, Any]:
    """Découpe une liste DÉJÀ triée et filtrée en une page du dialecte.

    Le curseur porte le rang du dernier élément servi (`after`) ET la valeur de
    sa clé (`key`). Le rang seul suffirait pour paginer, mais il ferait dériver
    la page si le jeu de données bouge entre deux appels ; la clé permet de
    retrouver la position exacte, et de retomber sur le rang quand l'élément a
    disparu (supprimé entre-temps). C'est le comportement d'un curseur réel :
    stable sur des données qui vivent.
    """
    depart = 0
    if curseur:
        charge = decoder_curseur(curseur)
        valeur = charge.get("key")
        depart = int(charge.get("after", 0))
        if valeur is not None:
            positions = [i for i, e in enumerate(elements) if e.get(cle) == valeur]
            if positions:
                depart = positions[0] + 1

    page = elements[depart : depart + limite]
    reste = depart + len(page) < len(elements)
    suivant: str | None = None
    if reste and page:
        suivant = encoder_curseur({"after": depart + len(page), "key": page[-1].get(cle)})
    return {"items": page, "has_more": reste, "next_cursor": suivant}
