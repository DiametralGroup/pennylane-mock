"""L'évolution du monde — ce qui rend l'extraction incrémentale éprouvable.

Aucun test ne dort : le temps défile EXPLICITEMENT via `/__admin/clock` ou
`/__admin/evolve`. Une suite qui `sleep(60)` pour voir un événement est une
suite qu'on finit par désactiver.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import ADMIN, BASE, H


def _totaux(client) -> dict:
    return client.get("/__admin/state", headers=ADMIN).json()


def _ecart_balance(client) -> Decimal:
    lignes = client.get(
        f"{BASE}/trial_balance?period_start=2020-01-01&period_end=2030-12-31"
        "&limit=1000&is_auxiliary=true",
        headers=H,
    ).json()["items"]
    return sum((Decimal(x["debits"]) - Decimal(x["credits"]) for x in lignes), Decimal(0))


def test_le_monde_ne_bouge_pas_tout_seul_pendant_la_suite(client):
    """L'intervalle est poussé à 3600 s par le conftest : aucun événement ne se
    déclenche au fil de l'horloge murale, même sur une CI lente."""
    avant = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
    apres = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
    assert avant == apres
    assert _totaux(client)["evolution"]["rang"] == 0


@pytest.mark.parametrize("pas", [1, 6, 13, 30])
def test_la_balance_equilibre_encore_apres_evolution(client, pas):
    """L'invariant comptable tient AUSSI pendant que le monde vit. Un mock
    qu'on a laissé tourner une heure ne doit pas finir par servir une
    comptabilité fausse."""
    client.post("/__admin/evolve", headers=ADMIN, json={"pas": pas})
    assert _ecart_balance(client) == 0


def test_l_evolution_est_deterministe(client):
    """L'événement k tire son aléa de `Random(f"{seed}:{k}")` : deux mocks
    avancés du même nombre de pas produisent le même monde. Sans cela, un test
    d'incrémentalité ne serait pas rejouable."""
    client.post("/__admin/evolve", headers=ADMIN, json={"pas": 8})
    premier = client.get(f"{BASE}/customer_invoices?limit=100&sort=id", headers=H).json()

    client.post("/__admin/reset", headers=ADMIN, json={})
    client.post("/__admin/evolve", headers=ADMIN, json={"pas": 8})
    second = client.get(f"{BASE}/customer_invoices?limit=100&sort=id", headers=H).json()
    assert premier == second


def test_une_nouvelle_facture_apparait_et_est_journalisee(client):
    avant = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()
    borne = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()
    borne_at = borne["items"][-1]["processed_at"]

    # Le cycle place une création en troisième position.
    client.post("/__admin/evolve", headers=ADMIN, json={"pas": 3})
    apres = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()
    assert len(apres["items"]) > len(avant["items"])

    nouveaux = client.get(
        f"{BASE}/changelogs/customer_invoices?limit=1000&start_date={borne_at}", headers=H
    ).json()["items"]
    inserts = [e for e in nouveaux if e["operation"] == "insert" and e["processed_at"] > borne_at]
    assert inserts, "une création doit produire un événement `insert`"


def test_un_reglement_solde_la_facture_et_cree_une_transaction(client):
    impayees_avant = [
        f
        for f in client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
        if not f["paid"] and not f["draft"] and f["status"] != "credit_note"
    ]
    transactions_avant = client.get(f"{BASE}/transactions?limit=100", headers=H).json()["items"]

    client.post("/__admin/evolve", headers=ADMIN, json={"pas": 2})

    impayees_apres = [
        f
        for f in client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
        if not f["paid"] and not f["draft"] and f["status"] != "credit_note"
    ]
    transactions_apres = client.get(f"{BASE}/transactions?limit=100", headers=H).json()["items"]
    assert len(impayees_apres) == len(impayees_avant) - 1
    assert len(transactions_apres) == len(transactions_avant) + 1


def test_les_horodatages_d_evolution_sont_strictement_posterieurs_au_jeu_de_base(client, donnees):
    """Un curseur posé sur le jeu de base doit rendre zéro ligne, et le PREMIER
    événement d'évolution est le premier changement qu'il verra. Sans cette
    stricte postériorité, un test d'incrémentalité passerait par accident."""
    from conftest import tout_paginer

    plafond = max(f["updated_at"] for f in donnees["customer_invoices"])
    client.post("/__admin/evolve", headers=ADMIN, json={"pas": 4})
    nouveaux = [
        f
        for f in tout_paginer(client, f"{BASE}/customer_invoices", limite=100)
        if f["updated_at"] > plafond
    ]
    assert nouveaux


def test_l_horloge_virtuelle_declenche_l_evolution(client):
    """`/__admin/clock` fait défiler le temps sans dormir. Avec un intervalle à
    3600 s, avancer de deux heures doit produire exactement deux événements."""
    assert _totaux(client)["evolution"]["rang"] == 0
    client.post("/__admin/clock", headers=ADMIN, json={"advance_seconds": 7200})
    assert _totaux(client)["evolution"]["rang"] == 2


def test_le_journal_d_evolution_est_observable(client):
    client.post("/__admin/evolve", headers=ADMIN, json={"pas": 6})
    journal = _totaux(client)["evolution"]["journal"]
    assert [e["genre"] for e in journal] == [
        "maj_facture_client",
        "reglement_client",
        "nouvelle_facture_client",
        "maj_client",
        "facture_fournisseur",
        "transaction_orpheline",
    ]


def test_un_reset_rearme_la_chronologie(client):
    client.post("/__admin/evolve", headers=ADMIN, json={"pas": 5})
    assert _totaux(client)["evolution"]["rang"] == 5
    client.post("/__admin/reset", headers=ADMIN, json={})
    assert _totaux(client)["evolution"]["rang"] == 0
