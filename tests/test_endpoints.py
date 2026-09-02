"""Les 91 opérations GET de la surface v2 — toutes montées, toutes servies.

Le test qui compte ici n'est pas « la route existe » mais « aucune route n'a
été oubliée par la fabrique » : c'est elle qui applique le scope, les
injections et l'enveloppe, et une route montée à la main y échapperait.
"""

from __future__ import annotations

import pytest
from conftest import BASE, H

import pennylane_mock as mock

#: Toutes les routes GET publiées par le contrat, avec l'identifiant à
#: substituer aux paramètres de chemin.
ROUTES = sorted(
    chemin for chemin, operations in mock.contrat_openapi()["paths"].items() if "get" in operations
)


def test_la_surface_compte_les_quatre_vingt_onze_operations_du_fournisseur():
    """91 GET relevées sur l'OpenAPI officiel (163 pages de référence fusionnées,
    cf. docs/EXTRACTION.md). Le compte est une assertion : si la fabrique en
    perd une, ce test le dit avant qu'un consommateur ne le découvre."""
    assert len(ROUTES) == 91


#: L'identifiant à substituer quand `1` n'existe pas dans la collection visée.
#: Les clients particuliers commencent après les personnes morales, et chaque
#: type d'export a son propre identifiant dans le jeu.
IDENT_PAR_ROUTE = {
    f"{BASE}/individual_customers/{{ident}}": 11,
    f"{BASE}/exports/general_ledgers/{{ident}}": 1,
    f"{BASE}/exports/analytical_general_ledgers/{{ident}}": 2,
    f"{BASE}/exports/fecs/{{ident}}": 3,
}


@pytest.mark.parametrize("chemin", ROUTES)
def test_chaque_route_repond_200(client, chemin):
    ident = IDENT_PAR_ROUTE.get(chemin, 1)
    url = BASE + chemin.removeprefix(BASE).replace("{ident}", str(ident))
    if url.endswith("/trial_balance"):
        url += "?period_start=2026-01-01&period_end=2026-12-31"
    reponse = client.get(url, headers=H)
    assert reponse.status_code == 200, f"{url} → {reponse.status_code} {reponse.text[:200]}"


@pytest.mark.parametrize("chemin", ROUTES)
def test_aucune_route_ne_passe_a_cote_de_l_authentification(client, chemin):
    """La fabrique garantit le prélude ; ce test le prouve route par route.
    Une seule route montée à la main servirait des données sans jeton."""
    ident = IDENT_PAR_ROUTE.get(chemin, 1)
    url = BASE + chemin.removeprefix(BASE).replace("{ident}", str(ident))
    reponse = client.get(url)
    assert reponse.status_code == 401, f"{url} répond sans jeton"


def test_un_identifiant_inconnu_rend_404(client):
    for url in (
        f"{BASE}/customers/999999",
        f"{BASE}/customer_invoices/999999",
        f"{BASE}/ledger_entries/999999/ledger_entry_lines",
        f"{BASE}/exports/fecs/999999",
    ):
        reponse = client.get(url, headers=H)
        assert reponse.status_code == 404, url
        assert reponse.json() == {"error": "Not Found", "status": 404}


def test_les_deux_variantes_de_client_sont_discriminees(client, donnees):
    """`/company_customers/{id}` et `/individual_customers/{id}` servent la même
    entité que `/customers/{id}`, mais rendent 404 quand le type ne correspond
    pas — c'est la seule façon dont le fournisseur le dit."""
    morale = next(c for c in donnees["customers"] if c["customer_type"] == "company")
    physique = next(c for c in donnees["customers"] if c["customer_type"] == "individual")

    assert client.get(f"{BASE}/company_customers/{morale['id']}", headers=H).status_code == 200
    assert client.get(f"{BASE}/company_customers/{physique['id']}", headers=H).status_code == 404
    assert client.get(f"{BASE}/individual_customers/{physique['id']}", headers=H).status_code == 200
    assert client.get(f"{BASE}/individual_customers/{morale['id']}", headers=H).status_code == 404


def test_les_deux_variantes_n_ont_pas_les_memes_champs(client):
    """Un consommateur qui lit `reg_no` sans regarder `customer_type` reçoit un
    KeyError sur la onzième ligne, jamais sur la première."""
    clients = client.get(f"{BASE}/customers?limit=100", headers=H).json()["items"]
    morales = [c for c in clients if c["customer_type"] == "company"]
    physiques = [c for c in clients if c["customer_type"] == "individual"]
    assert morales and physiques
    assert all(c.get("reg_no") and c.get("vat_number") for c in morales)
    assert all(c.get("first_name") and c.get("last_name") for c in physiques)
    assert all(not c.get("reg_no") for c in physiques)


def test_une_sous_collection_vide_est_servie_et_bien_formee(client):
    """Annexes, sections, champs d'en-tête : Boréal Conseil n'en a aucun. La
    route existe quand même et rend une page vide — la faire répondre 404
    apprendrait au consommateur exactement le contraire de ce qu'il doit faire."""
    for chemin in ("appendices", "invoice_line_sections", "custom_header_fields", "installments"):
        corps = client.get(f"{BASE}/customer_invoices/1/{chemin}", headers=H).json()
        assert corps == {"items": [], "has_more": False, "next_cursor": None}


def test_les_lignes_d_une_facture_passent_par_leur_lien(client):
    facture = client.get(f"{BASE}/customer_invoices/1", headers=H).json()
    lien = facture["invoice_lines"]["url"]
    chemin = lien.replace("https://app.pennylane.com", "")
    lignes = client.get(chemin, headers=H).json()["items"]
    assert lignes
    assert all(ligne["id"] // 100 == facture["id"] for ligne in lignes)


def test_pa_registrations_n_exige_aucun_scope(client):
    """Avec `/me`, c'est le seul endpoint de la surface sans scope déclaré."""
    from conftest import ADMIN

    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": []})
    assert client.get(f"{BASE}/pa_registrations", headers=H).status_code == 200
