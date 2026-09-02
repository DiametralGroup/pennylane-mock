"""La cohérence du monde — ce qui distingue ce mock d'un stub.

L'invariant central : **la balance équilibre**. Tout le reste en découle, parce
que tout dérive des écritures. Un mock comptable qui ne tiendrait pas cet
invariant servirait une comptabilité fausse — ce qui est pire que de ne rien
servir, parce qu'un tableau de bord construit dessus a l'air juste.
"""

from __future__ import annotations

from decimal import Decimal

from conftest import BASE, H


def _somme(elements, champ) -> Decimal:
    return sum((Decimal(e[champ]) for e in elements), Decimal(0))


def _balance(client, *, auxiliaire: bool) -> list[dict]:
    aux = "true" if auxiliaire else "false"
    reponse = client.get(
        f"{BASE}/trial_balance?period_start=2020-01-01&period_end=2030-12-31"
        f"&limit=1000&is_auxiliary={aux}",
        headers=H,
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["items"]


def test_la_balance_equilibre(client):
    """`sum(debit) == sum(credit)`, À L'EURO PRÈS et sans arrondi de faveur.

    C'est possible parce que les montants sont manipulés en centimes ENTIERS
    dans le générateur : avec des flottants, un jeu de plusieurs centaines de
    lignes finit par afficher un écart de deux centimes que personne n'explique.
    """
    for auxiliaire in (True, False):
        lignes = _balance(client, auxiliaire=auxiliaire)
        assert lignes
        assert _somme(lignes, "debits") == _somme(lignes, "credits")


def test_la_balance_derive_bien_du_grand_livre(client):
    """Elle est CALCULÉE, jamais stockée : c'est ce qui rend impossible la seule
    incohérence qui compte — une balance qui ne correspond plus au grand livre."""
    from conftest import tout_paginer

    lignes = tout_paginer(client, f"{BASE}/ledger_entry_lines", limite=100)
    balance = _balance(client, auxiliaire=True)
    assert _somme(lignes, "debit") == _somme(balance, "debits")
    assert _somme(lignes, "credit") == _somme(balance, "credits")


def test_les_auxiliaires_sont_agreges_quand_on_ne_les_demande_pas(client):
    """Sommer les deux vues doublerait l'actif. Le fournisseur agrège dans la
    racine ; un consommateur qui ignore `is_auxiliary` doit voir la différence."""
    detaillee = _balance(client, auxiliaire=True)
    agregee = _balance(client, auxiliaire=False)
    assert len(detaillee) > len(agregee)
    assert any(not ligne["number"].isdigit() for ligne in detaillee)
    assert all(ligne["number"].isdigit() for ligne in agregee)
    # Les totaux, eux, sont IDENTIQUES : agréger ne perd rien.
    assert _somme(detaillee, "debits") == _somme(agregee, "debits")


def test_chaque_ecriture_est_equilibree(client, donnees):  # noqa: ARG001 — chaîne le reset
    """L'invariant tient écriture par écriture, pas seulement en cumul — un
    total juste sur des écritures fausses reste une comptabilité fausse."""
    par_ecriture: dict[int, Decimal] = {}
    for ligne in donnees["ledger_entry_lines"]:
        ident = ligne["ledger_entry"]["id"]
        solde = Decimal(ligne["debit"]) - Decimal(ligne["credit"])
        par_ecriture[ident] = par_ecriture.get(ident, Decimal(0)) + solde
    desequilibrees = {i: s for i, s in par_ecriture.items() if s != 0}
    assert not desequilibrees, f"écritures déséquilibrées : {desequilibrees}"


def test_le_solde_bancaire_est_celui_du_compte_512(client, donnees):
    """Sans ce recalage, la banque et la comptabilité se contrediraient dans le
    même jeu — et deux tableaux de bord de trésorerie donneraient deux chiffres."""
    par_id = {c["id"]: c for c in donnees["ledger_accounts"]}
    for compte in client.get(f"{BASE}/bank_accounts", headers=H).json()["items"]:
        numero = par_id[compte["ledger_account"]["id"]]["number"]
        solde = sum(
            (
                Decimal(ligne["debit"]) - Decimal(ligne["credit"])
                for ligne in donnees["ledger_entry_lines"]
                if par_id[ligne["ledger_account"]["id"]]["number"] == numero
            ),
            Decimal(0),
        )
        assert Decimal(compte["balance"]) == solde, f"{compte['name']} diverge de {numero}"


def test_le_ttc_d_une_facture_est_le_ht_plus_la_tva(client):
    from conftest import tout_paginer

    for facture in tout_paginer(client, f"{BASE}/customer_invoices", limite=100):
        ht = Decimal(facture["currency_amount_before_tax"])
        tva = Decimal(facture["tax"])
        assert Decimal(facture["amount"]) == ht + tva, facture["id"]
        # TVA française à 20 %, sur toutes les lignes du jeu.
        assert tva == (ht * 20 / 100).quantize(Decimal("0.01")), facture["id"]


def test_les_lignes_d_une_facture_totalisent_la_facture(client):
    from conftest import tout_paginer

    for facture in tout_paginer(client, f"{BASE}/customer_invoices", limite=100):
        lignes = client.get(
            f"{BASE}/customer_invoices/{facture['id']}/invoice_lines?limit=100", headers=H
        ).json()["items"]
        assert lignes, f"facture {facture['id']} sans ligne"
        assert _somme(lignes, "currency_amount_before_tax") == Decimal(
            facture["currency_amount_before_tax"]
        ), facture["id"]


def test_un_avoir_porte_des_montants_negatifs(client):
    """Un avoir est une FACTURE, pas une entité d'un autre type. Sommer `amount`
    sans regarder le signe fausse le chiffre d'affaires."""
    from conftest import tout_paginer

    factures = tout_paginer(client, f"{BASE}/customer_invoices", limite=100)
    avoirs = [f for f in factures if f["status"] == "credit_note"]
    assert avoirs, "le jeu doit porter au moins un avoir"
    for avoir in avoirs:
        assert Decimal(avoir["amount"]) < 0
        assert avoir["credited_invoice"] is not None
        assert avoir["invoice_number"].startswith("AV-")


def test_un_brouillon_n_a_ni_numero_ni_ecriture(client):
    """Un brouillon n'est pas comptabilisé : `ledger_entry` est `null` et le
    numéro de facture est vide. C'est un cas que tout connecteur rencontre."""
    from conftest import tout_paginer

    brouillons = [
        f for f in tout_paginer(client, f"{BASE}/customer_invoices", limite=100) if f["draft"]
    ]
    assert brouillons, "le jeu doit porter au moins un brouillon"
    for brouillon in brouillons:
        assert brouillon["ledger_entry"] is None
        assert brouillon["invoice_number"] == ""
        assert brouillon["status"] == "draft"


def test_une_facture_reglee_est_lettree_et_sans_reste(client):
    from conftest import tout_paginer

    reglees = [
        f for f in tout_paginer(client, f"{BASE}/customer_invoices", limite=100) if f["paid"]
    ]
    assert reglees
    for facture in reglees[:10]:
        assert facture["remaining_amount_with_tax"] == "0.00"
        lignes = client.get(
            f"{BASE}/ledger_entries/{facture['ledger_entry']['id']}/ledger_entry_lines",
            headers=H,
        ).json()["items"]
        tiers = [ligne for ligne in lignes if ligne["ledger_account"]["number"].startswith("411")]
        assert tiers, facture["id"]
        assert tiers[0]["lettered_ledger_entry_lines"]["ids"], (
            f"la créance de la facture {facture['id']} est réglée mais non lettrée"
        )


def test_les_transactions_orphelines_existent(client):
    """Un jeu où tout est rapproché ne prouve rien : c'est justement la
    transaction sans facture qui fait le travail d'un cabinet."""
    from conftest import tout_paginer

    transactions = tout_paginer(client, f"{BASE}/transactions", limite=100)
    orphelines = [t for t in transactions if t["outstanding_balance"] is not None]
    assert orphelines, "le jeu doit porter des transactions non rapprochées"
    for transaction in orphelines:
        assert transaction["attachment_required"] is True
        assert (
            client.get(
                f"{BASE}/transactions/{transaction['id']}/matched_invoices", headers=H
            ).json()["items"]
            == []
        )


def test_un_paiement_n_est_pas_une_transaction_rapprochee(client):
    """Le fournisseur consacre une page à la distinction. Les additionner
    compte l'encaissement deux fois."""
    reglee = next(
        f
        for f in client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
        if f["paid"] and not f["draft"]
    )
    paiements = client.get(f"{BASE}/customer_invoices/{reglee['id']}/payments", headers=H).json()[
        "items"
    ]
    transactions = client.get(
        f"{BASE}/customer_invoices/{reglee['id']}/matched_transactions", headers=H
    ).json()["items"]
    assert paiements, "une facture réglée porte un règlement"
    # Les deux décrivent le même encaissement sous deux formes : le règlement
    # n'a pas de compte bancaire, la transaction si.
    assert "bank_account" not in paiements[0]
    if transactions:
        assert "bank_account" in transactions[0]


def test_le_monde_est_deterministe(client):  # noqa: ARG001 — chaîne le reset
    """Deux constructions à la même graine donnent le même jeu, à l'octet près.
    C'est ce qui rend un gate d'idempotence possible côté consommateur."""
    from pennylane_mock.dataset.realiste import build_realiste_dataset

    a = build_realiste_dataset(42)
    b = build_realiste_dataset(42)
    assert a == b
    assert build_realiste_dataset(7) != a


def test_le_monde_est_celui_des_mocks_voisins(client, donnees):  # noqa: ARG001
    """La cohérence inter-mocks est une propriété du jeu de données, pas un
    hasard : mêmes raisons sociales, même ancre, même graine que
    boondmanager-mock. Les MONTANTS, eux, sont propres à ce mock — cf. le
    README et l'encadré de dataset/realiste.py."""
    noms = {c["name"] for c in donnees["customers"]}
    assert {"Lumina Retail", "Banque Hexagone", "Voltalis Énergie", "MediaQuartz"} <= noms
    assert {"Fivetech Partners", "Softalliance", "Foncière Beaumont"} <= {
        f["name"] for f in donnees["suppliers"]
    }
    assert all(
        f["invoice_number"].startswith(("FAC-2026-", "AV-2026-", ""))
        for f in donnees["customer_invoices"]
    )
    # Le prospect de BoondManager existe ici SANS aucune facture.
    prospect = next(c for c in donnees["customers"] if c["name"] == "MediaQuartz")
    assert not [f for f in donnees["customer_invoices"] if f["customer"]["id"] == prospect["id"]]
