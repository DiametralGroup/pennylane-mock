#!/usr/bin/env python3
"""Confronte le mock à une VRAIE instance Pennylane. Lecture seule, jamais d'écriture.

Le mock est adossé à l'OpenAPI officiel, ce qui est une bonne source — mais une
source déclarative. Ce script vérifie ce que l'instance FAIT, pas ce que la
documentation dit qu'elle fait. C'est là que se trouvent les écarts qui coûtent
cher : un montant sérialisé en nombre là où le schéma promet une chaîne, une
clé absente de la réponse réelle, une forme d'erreur différente.

    PENNYLANE_TOKEN=xxx uv run python scripts/compare_real.py

┌─ CE QUE CE SCRIPT NE FAIT PAS ──────────────────────────────────────────────┐
│ Aucune requête autre que GET. Aucun POST, aucun PUT, aucun DELETE. Il tourne │
│ contre la comptabilité RÉELLE d'une entreprise : une écriture y serait une   │
│ écriture comptable, et un bac à sable n'est pas garanti.                     │
│                                                                              │
│ Il ne recopie AUCUNE donnée dans son rapport : ni identifiant, ni raison     │
│ sociale, ni montant. Seulement des NOMS DE CHAMPS et des TYPES.              │
└──────────────────────────────────────────────────────────────────────────────┘

Tout écart est un écart du MOCK. C'est le fournisseur qui a raison.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

BASE_REELLE = "https://app.pennylane.com/api/external/v2"

#: Les listes confrontées. Uniquement des collections : un détail exigerait un
#: identifiant réel, donc de lire de la donnée pour la redemander.
RESSOURCES = (
    "journals",
    "ledger_accounts",
    "ledger_entries",
    "ledger_entry_lines",
    "fiscal_years",
    "categories",
    "category_groups",
    "customers",
    "suppliers",
    "products",
    "customer_invoices",
    "supplier_invoices",
    "quotes",
    "bank_accounts",
    "bank_establishments",
    "transactions",
)

CHANGELOGS = ("customer_invoices", "customers", "transactions")


def _get(url: str, jeton: str) -> tuple[int, dict[str, str], Any]:
    requete = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {jeton}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            return reponse.status, dict(reponse.headers), json.loads(reponse.read())
    except urllib.error.HTTPError as erreur:
        corps = erreur.read()
        try:
            return erreur.code, dict(erreur.headers), json.loads(corps)
        except ValueError:
            return erreur.code, dict(erreur.headers), corps.decode("utf-8", "replace")


def _forme(valeur: Any) -> Any:
    """La FORME d'une valeur, jamais son contenu.

    C'est ce qui rend le rapport publiable : il ne porte que des noms de champs
    et des types. `"230.32"` devient `str`, et c'est exactement l'information
    qui compte — le piège des montants du dialecte v2 se voit ici.
    """
    if isinstance(valeur, dict):
        return {cle: _forme(v) for cle, v in sorted(valeur.items())}
    if isinstance(valeur, list):
        return [_forme(valeur[0])] if valeur else []
    if valeur is None:
        return "null"
    return type(valeur).__name__


def _cles(corps: Any) -> Any:
    """Les clés d'un corps d'erreur — ou son type s'il n'est pas du JSON.

    Un 429 réel rend du TEXTE : le rapport doit pouvoir le dire sans lever.
    """
    return sorted(corps) if isinstance(corps, dict) else type(corps).__name__


def _comparer(nom: str, reel: Any, mock: Any) -> list[str]:
    ecarts: list[str] = []
    if isinstance(reel, dict) and isinstance(mock, dict):
        for cle in sorted(set(reel) - set(mock)):
            ecarts.append(f"{nom}.{cle} : présent en RÉEL, absent du mock ({reel[cle]})")
        for cle in sorted(set(mock) - set(reel)):
            ecarts.append(f"{nom}.{cle} : servi par le mock, absent du RÉEL")
        for cle in sorted(set(reel) & set(mock)):
            ecarts += _comparer(f"{nom}.{cle}", reel[cle], mock[cle])
    elif isinstance(reel, list) and isinstance(mock, list):
        if reel and mock:
            ecarts += _comparer(f"{nom}[]", reel[0], mock[0])
    elif reel != mock:
        ecarts.append(f"{nom} : RÉEL={reel}, mock={mock}")
    return ecarts


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--base", default=BASE_REELLE)
    analyseur.add_argument("--out", type=Path, default=Path("docs/comparisons"))
    options = analyseur.parse_args()

    jeton = os.environ.get("PENNYLANE_TOKEN")
    if not jeton:
        print("PENNYLANE_TOKEN est requis (jeton de compagnie, lecture seule).", file=sys.stderr)
        return 2

    from fastapi.testclient import TestClient

    import pennylane_mock as mock_module

    client_mock = TestClient(mock_module.app)
    entetes_mock = {"Authorization": "Bearer mock-pennylane-token"}

    lignes: list[str] = [
        f"# Comparaison mock ↔ instance réelle — {date.today().isoformat()}",
        "",
        "Formes uniquement : noms de champs et types. Aucune donnée recopiée.",
        "",
    ]
    total = 0

    for ressource in RESSOURCES:
        statut, _, reel = _get(f"{options.base}/{ressource}?limit=1", jeton)
        if statut != 200:
            lignes += [f"## {ressource}", "", f"⚠️ RÉEL a rendu {statut} — non comparé.", ""]
            continue
        corps_mock = client_mock.get(
            f"/api/external/v2/{ressource}?limit=1", headers=entetes_mock
        ).json()
        ecarts = _comparer(ressource, _forme(reel), _forme(corps_mock))
        total += len(ecarts)
        lignes += [f"## {ressource}", ""]
        lignes += [f"- {e}" for e in ecarts] if ecarts else ["Aucun écart de forme."]
        lignes.append("")

    for famille in CHANGELOGS:
        statut, _, reel = _get(f"{options.base}/changelogs/{famille}?limit=1", jeton)
        if statut != 200:
            lignes += [f"## changelogs/{famille}", "", f"⚠️ RÉEL a rendu {statut}.", ""]
            continue
        corps_mock = client_mock.get(
            f"/api/external/v2/changelogs/{famille}?limit=1", headers=entetes_mock
        ).json()
        ecarts = _comparer(f"changelogs/{famille}", _forme(reel), _forme(corps_mock))
        total += len(ecarts)
        lignes += [f"## changelogs/{famille}", ""]
        lignes += [f"- {e}" for e in ecarts] if ecarts else ["Aucun écart de forme."]
        lignes.append("")

    # Le dialecte d'ERREUR — le doute le plus structurant du registre
    # (docs/UNVERIFIED-FIELDS.md) : le guide et l'OpenAPI se contredisent.
    lignes += ["## Dialecte d'erreur", ""]
    statut, _, corps = _get(f"{options.base}/customers", "jeton-manifestement-invalide")
    lignes.append(f"- 401 réel : `{statut}` → clés `{_cles(corps)}`")
    statut, entetes, corps = _get(f"{options.base}/nimportequoi", jeton)
    lignes.append(f"- 404 réel : `{statut}` → clés `{_cles(corps)}`")
    lignes.append(
        "- en-têtes de débit sur une réponse saine : "
        f"`{sorted(k for k in entetes if k.lower().startswith('ratelimit'))}`"
    )
    lignes.append("")
    lignes.append(f"**Total : {total} écart(s) de forme.**")

    options.out.mkdir(parents=True, exist_ok=True)
    rapport = options.out / f"{date.today().isoformat()}.md"
    rapport.write_text("\n".join(lignes), encoding="utf-8")
    print(f"→ {rapport} ({total} écart(s))")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
