"""The 91 GET operations of the v2 surface — all mounted, all served.

The test that matters here isn't "the route exists" but "no route was
forgotten by the factory" — it's the factory that applies the scope, the
injections and the envelope, and a hand-mounted route would escape it.
"""

from __future__ import annotations

import pytest
from conftest import BASE, H

import pennylane_mock as mock

#: All GET routes published by the contract, with the identifier to
#: substitute for path parameters.
ROUTES = sorted(
    chemin for chemin, operations in mock.openapi_contract()["paths"].items() if "get" in operations
)


def test_surface_counts_the_provider_s_ninety_one_operations():
    """91 GETs found on the official OpenAPI spec (163 reference pages
    merged, see docs/EXTRACTION.md). The count is an assertion: if the
    factory loses one, this test says so before a consumer discovers it."""
    assert len(ROUTES) == 91


#: The identifier to substitute when `1` doesn't exist in the targeted
#: collection. Individual customers come after companies, and each export
#: type has its own identifier in the dataset.
IDENT_PAR_ROUTE = {
    f"{BASE}/individual_customers/{{ident}}": 11,
    f"{BASE}/exports/general_ledgers/{{ident}}": 1,
    f"{BASE}/exports/analytical_general_ledgers/{{ident}}": 2,
    f"{BASE}/exports/fecs/{{ident}}": 3,
}


@pytest.mark.parametrize("chemin", ROUTES)
def test_every_route_returns_200(client, chemin):
    ident = IDENT_PAR_ROUTE.get(chemin, 1)
    url = BASE + chemin.removeprefix(BASE).replace("{ident}", str(ident))
    if url.endswith("/trial_balance"):
        url += "?period_start=2026-01-01&period_end=2026-12-31"
    reponse = client.get(url, headers=H)
    assert reponse.status_code == 200, f"{url} → {reponse.status_code} {reponse.text[:200]}"


@pytest.mark.parametrize("chemin", ROUTES)
def test_no_route_bypasses_authentication(client, chemin):
    """The factory guarantees the prelude; this test proves it route by
    route. A single hand-mounted route would serve data without a token."""
    ident = IDENT_PAR_ROUTE.get(chemin, 1)
    url = BASE + chemin.removeprefix(BASE).replace("{ident}", str(ident))
    reponse = client.get(url)
    assert reponse.status_code == 401, f"{url} responds without a token"


def test_an_unknown_identifier_renders_404(client):
    for url in (
        f"{BASE}/customers/999999",
        f"{BASE}/customer_invoices/999999",
        f"{BASE}/ledger_entries/999999/ledger_entry_lines",
        f"{BASE}/exports/fecs/999999",
    ):
        reponse = client.get(url, headers=H)
        assert reponse.status_code == 404, url
        assert reponse.json() == {"error": "Not Found", "status": 404}


def test_the_two_customer_variants_are_discriminated(client, donnees):
    """`/company_customers/{id}` and `/individual_customers/{id}` serve the
    same entity as `/customers/{id}`, but render 404 when the type doesn't
    match — it's the only way the provider signals it."""
    morale = next(c for c in donnees["customers"] if c["customer_type"] == "company")
    physique = next(c for c in donnees["customers"] if c["customer_type"] == "individual")

    assert client.get(f"{BASE}/company_customers/{morale['id']}", headers=H).status_code == 200
    assert client.get(f"{BASE}/company_customers/{physique['id']}", headers=H).status_code == 404
    assert client.get(f"{BASE}/individual_customers/{physique['id']}", headers=H).status_code == 200
    assert client.get(f"{BASE}/individual_customers/{morale['id']}", headers=H).status_code == 404


def test_the_two_variants_do_not_share_the_same_fields(client):
    """A consumer who reads `reg_no` without checking `customer_type` gets a
    KeyError on the eleventh line, never on the first."""
    clients = client.get(f"{BASE}/customers?limit=100", headers=H).json()["items"]
    morales = [c for c in clients if c["customer_type"] == "company"]
    physiques = [c for c in clients if c["customer_type"] == "individual"]
    assert morales and physiques
    assert all(c.get("reg_no") and c.get("vat_number") for c in morales)
    assert all(c.get("first_name") and c.get("last_name") for c in physiques)
    assert all(not c.get("reg_no") for c in physiques)


def test_an_empty_sub_collection_is_served_and_well_formed(client):
    """Appendices, sections, header fields: Boréal Conseil has none. The
    route exists anyway and renders an empty page — making it 404 would
    teach the consumer exactly the opposite of what they should do."""
    for chemin in ("appendices", "invoice_line_sections", "custom_header_fields", "installments"):
        corps = client.get(f"{BASE}/customer_invoices/1/{chemin}", headers=H).json()
        assert corps == {"items": [], "has_more": False, "next_cursor": None}


def test_invoice_lines_are_reached_through_their_link(client):
    facture = client.get(f"{BASE}/customer_invoices/1", headers=H).json()
    lien = facture["invoice_lines"]["url"]
    chemin = lien.replace("https://app.pennylane.com", "")
    lignes = client.get(chemin, headers=H).json()["items"]
    assert lignes
    assert all(ligne["id"] // 100 == facture["id"] for ligne in lignes)


def test_pa_registrations_requires_no_scope(client):
    """Along with `/me`, it's the only endpoint on the surface with no
    declared scope."""
    from conftest import ADMIN

    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": []})
    assert client.get(f"{BASE}/pa_registrations", headers=H).status_code == 200
