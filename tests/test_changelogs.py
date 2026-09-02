"""Les changelogs — l'extraction incrémentale native de Pennylane.

Quatre règles de dialecte, toutes capables de casser un connecteur en
production sans se voir en test si le mock ne les reproduit pas.
"""

from __future__ import annotations

import pytest
from conftest import ADMIN, BASE, H

FAMILLES = (
    "customer_invoices",
    "supplier_invoices",
    "customers",
    "suppliers",
    "products",
    "transactions",
    "quotes",
    "ledger_entry_lines",
    "ledger_entries_categories",
    "ledger_entry_lines_categories",
)


@pytest.mark.parametrize("famille", FAMILLES)
def test_les_dix_journaux_sont_servis(client, famille):
    """Sept sont documentés dans le guide ; `quotes` et les deux `*_categories`
    n'apparaissent que dans la référence. Un consommateur qui s'en tiendrait au
    guide manquerait trois familles de changements."""
    reponse = client.get(f"{BASE}/changelogs/{famille}?limit=1000", headers=H)
    assert reponse.status_code == 200, reponse.text
    assert set(reponse.json()) == {"items", "has_more", "next_cursor"}


def test_un_evenement_porte_l_id_et_l_operation_jamais_l_etat(client):
    """Il faut un SECOND appel pour obtenir la ressource. C'est le pattern que
    le fournisseur recommande, et celui qu'un connecteur doit exercer."""
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=5", headers=H).json()[
        "items"
    ]
    assert evenements
    for evenement in evenements:
        assert set(evenement) == {"id", "operation", "processed_at", "created_at", "updated_at"}
        assert evenement["operation"] in {"insert", "update", "delete"}


def test_l_ordre_est_chronologique_croissant(client):
    """Un consommateur qui suppose l'inverse pose son point de reprise sur le
    PREMIER événement de la page et reperd tout à chaque passage."""
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()[
        "items"
    ]
    horodatages = [e["processed_at"] for e in evenements]
    assert horodatages == sorted(horodatages)


def test_la_retention_de_quatre_semaines_purge_reellement(client):
    """« Changes are retained for 4 weeks. » Un événement plus ancien n'existe
    PLUS — il n'est pas seulement inaccessible par `start_date`. Un mock qui
    servirait tout l'historique apprendrait au consommateur qu'une
    resynchronisation complète par le changelog est possible : elle ne l'est
    pas, et c'est ce qui casse un pipeline arrêté cinq semaines."""
    from datetime import timedelta

    from pennylane_mock.evolution import EPOQUE

    limite = EPOQUE - timedelta(days=28)
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()[
        "items"
    ]
    assert evenements
    assert all(e["processed_at"] >= limite.strftime("%Y-%m-%dT%H:%M:%S.%fZ") for e in evenements)


def test_une_start_date_hors_retention_rend_422(client):
    """422, pas une liste tronquée : c'est la différence entre « rien n'a bougé »
    et « ma fenêtre est trop large »."""
    reponse = client.get(f"{BASE}/changelogs/customers?start_date=2020-01-01T00:00:00Z", headers=H)
    assert reponse.status_code == 422
    assert "retention" in reponse.json()["error"]


def test_start_date_et_cursor_ensemble_rendent_400(client):
    """La pagination CONTINUE une fenêtre, elle n'en ouvre pas une nouvelle.
    Sans ce refus, un consommateur qui renvoie sa `start_date` à chaque page
    rejoue la première indéfiniment et croit avoir tout lu."""
    reponse = client.get(
        f"{BASE}/changelogs/customers?start_date=2026-07-01T00:00:00Z&cursor=abc", headers=H
    )
    assert reponse.status_code == 400
    assert "together" in reponse.json()["error"]


@pytest.mark.parametrize("brut", ["hier", "2026-13-45", "15/07/2026"])
def test_une_start_date_mal_formee_rend_400(client, brut):
    reponse = client.get(f"{BASE}/changelogs/customers?start_date={brut}", headers=H)
    assert reponse.status_code == 400
    assert "RFC3339" in reponse.json()["error"]


def test_start_date_reduit_bien_la_fenetre(client):
    tout = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()["items"]
    milieu = tout[len(tout) // 2]["processed_at"]
    depuis = client.get(
        f"{BASE}/changelogs/customer_invoices?limit=1000&start_date={milieu}", headers=H
    ).json()["items"]
    assert 0 < len(depuis) < len(tout)
    assert all(e["processed_at"] >= milieu for e in depuis)


def test_une_mutation_produit_exactement_un_evenement(client):
    """L'outil du test d'incrémentalité côté consommateur : après cet appel,
    exactement UNE ligne doit être rechargée."""
    avant = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()["items"]
    borne = avant[-1]["processed_at"]

    client.post(
        "/__admin/mutate",
        headers=ADMIN,
        json={"collection": "customer_invoices", "id": 3, "champs": {"label": "muté"}},
    )
    nouveaux = client.get(
        f"{BASE}/changelogs/customer_invoices?limit=1000&start_date={borne}", headers=H
    ).json()["items"]
    ajoutes = [e for e in nouveaux if e["processed_at"] > borne]
    assert len(ajoutes) == 1
    assert ajoutes[0] == {
        "id": 3,
        "operation": "update",
        "processed_at": ajoutes[0]["processed_at"],
        "created_at": ajoutes[0]["created_at"],
        "updated_at": ajoutes[0]["processed_at"],
    }
    assert client.get(f"{BASE}/customer_invoices/3", headers=H).json()["label"] == "muté"


def test_le_parcours_par_lots_recharge_bien_les_ressources(client):
    """Le pattern complet recommandé par le fournisseur : lire les changements,
    collecter les identifiants, puis UN appel filtré `id in [...]`."""
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=10", headers=H).json()[
        "items"
    ]
    identifiants = sorted({e["id"] for e in evenements})
    filtre = f'[{{"field":"id","operator":"in","value":{identifiants}}}]'
    items = client.get(f"{BASE}/customer_invoices?limit=100&filter={filtre}", headers=H).json()[
        "items"
    ]
    assert sorted(e["id"] for e in items) == identifiants
