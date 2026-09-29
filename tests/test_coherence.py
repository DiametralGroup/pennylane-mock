"""World coherence — what sets this mock apart from a stub.

The central invariant: **the trial balance balances**. Everything else
follows from it, because everything derives from the ledger entries. An
accounting mock that didn't hold this invariant would serve a false set of
books — which is worse than serving nothing, because a dashboard built on it
looks correct.
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


def test_trial_balance_balances(client):
    """`sum(debit) == sum(credit)`, TO THE CENT and with no favorable rounding.

    This is possible because amounts are handled in WHOLE CENTS in the
    generator: with floats, a dataset of several hundred lines ends up
    showing a two-cent gap that nobody can explain.
    """
    for auxiliaire in (True, False):
        lignes = _balance(client, auxiliaire=auxiliaire)
        assert lignes
        assert _somme(lignes, "debits") == _somme(lignes, "credits")


def test_trial_balance_is_derived_from_the_general_ledger(client):
    """It is COMPUTED, never stored: that's what makes impossible the only
    inconsistency that matters — a trial balance that no longer matches the
    general ledger."""
    from conftest import tout_paginer

    lignes = tout_paginer(client, f"{BASE}/ledger_entry_lines", limite=100)
    balance = _balance(client, auxiliaire=True)
    assert _somme(lignes, "debit") == _somme(balance, "debits")
    assert _somme(lignes, "credit") == _somme(balance, "credits")


def test_auxiliary_accounts_are_aggregated_when_not_requested(client):
    """Summing both views would double the assets. The provider aggregates
    at the root; a consumer who ignores `is_auxiliary` must see the
    difference."""
    detaillee = _balance(client, auxiliaire=True)
    agregee = _balance(client, auxiliaire=False)
    assert len(detaillee) > len(agregee)
    assert any(not ligne["number"].isdigit() for ligne in detaillee)
    assert all(ligne["number"].isdigit() for ligne in agregee)
    # The totals themselves are IDENTICAL: aggregating loses nothing.
    assert _somme(detaillee, "debits") == _somme(agregee, "debits")


def test_every_entry_is_balanced(client, donnees):  # noqa: ARG001 — chains the reset
    """The invariant holds entry by entry, not only in aggregate — a correct
    total on incorrect entries is still a false set of books."""
    par_ecriture: dict[int, Decimal] = {}
    for ligne in donnees["ledger_entry_lines"]:
        ident = ligne["ledger_entry"]["id"]
        solde = Decimal(ligne["debit"]) - Decimal(ligne["credit"])
        par_ecriture[ident] = par_ecriture.get(ident, Decimal(0)) + solde
    desequilibrees = {i: s for i, s in par_ecriture.items() if s != 0}
    assert not desequilibrees, f"unbalanced entries: {desequilibrees}"


def test_bank_balance_is_that_of_account_512(client, donnees):
    """Without this reconciliation, the bank and the books would contradict
    each other in the same dataset — and two cash dashboards would give two
    different figures."""
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
        assert Decimal(compte["balance"]) == solde, f"{compte['name']} diverges from {numero}"


def test_invoice_total_is_pre_tax_amount_plus_tax(client):
    from conftest import tout_paginer

    for facture in tout_paginer(client, f"{BASE}/customer_invoices", limite=100):
        ht = Decimal(facture["currency_amount_before_tax"])
        tva = Decimal(facture["tax"])
        assert Decimal(facture["amount"]) == ht + tva, facture["id"]
        # French VAT at 20%, on every line of the dataset.
        assert tva == (ht * 20 / 100).quantize(Decimal("0.01")), facture["id"]


def test_invoice_lines_add_up_to_the_invoice(client):
    from conftest import tout_paginer

    for facture in tout_paginer(client, f"{BASE}/customer_invoices", limite=100):
        lignes = client.get(
            f"{BASE}/customer_invoices/{facture['id']}/invoice_lines?limit=100", headers=H
        ).json()["items"]
        assert lignes, f"invoice {facture['id']} has no line"
        assert _somme(lignes, "currency_amount_before_tax") == Decimal(
            facture["currency_amount_before_tax"]
        ), facture["id"]


def test_a_credit_note_carries_negative_amounts(client):
    """A credit note is an INVOICE, not an entity of another type. Summing
    `amount` without looking at the sign misstates revenue."""
    from conftest import tout_paginer

    factures = tout_paginer(client, f"{BASE}/customer_invoices", limite=100)
    avoirs = [f for f in factures if f["status"] == "credit_note"]
    assert avoirs, "the dataset must carry at least one credit note"
    for avoir in avoirs:
        assert Decimal(avoir["amount"]) < 0
        assert avoir["credited_invoice"] is not None
        assert avoir["invoice_number"].startswith("AV-")


def test_a_draft_has_neither_number_nor_ledger_entry(client):
    """A draft isn't booked: `ledger_entry` is `null` and the invoice number
    is empty. This is a case every connector runs into."""
    from conftest import tout_paginer

    brouillons = [
        f for f in tout_paginer(client, f"{BASE}/customer_invoices", limite=100) if f["draft"]
    ]
    assert brouillons, "the dataset must carry at least one draft"
    for brouillon in brouillons:
        assert brouillon["ledger_entry"] is None
        assert brouillon["invoice_number"] == ""
        assert brouillon["status"] == "draft"


def test_a_paid_invoice_is_lettered_and_has_no_remainder(client):
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
            f"invoice {facture['id']}'s receivable is paid but not lettered"
        )


def test_orphan_transactions_exist(client):
    """A dataset where everything is reconciled proves nothing: it's exactly
    the transaction with no invoice that does an accounting firm's work."""
    from conftest import tout_paginer

    transactions = tout_paginer(client, f"{BASE}/transactions", limite=100)
    orphelines = [t for t in transactions if t["outstanding_balance"] is not None]
    assert orphelines, "the dataset must carry unreconciled transactions"
    for transaction in orphelines:
        assert transaction["attachment_required"] is True
        assert (
            client.get(
                f"{BASE}/transactions/{transaction['id']}/matched_invoices", headers=H
            ).json()["items"]
            == []
        )


def test_a_payment_is_not_a_reconciled_transaction(client):
    """The provider devotes a whole page to this distinction. Adding both up
    counts the collection twice."""
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
    assert paiements, "a paid invoice carries a payment"
    # Both describe the same collection under two shapes: the payment has no
    # bank account, the transaction does.
    assert "bank_account" not in paiements[0]
    if transactions:
        assert "bank_account" in transactions[0]


def test_the_world_is_deterministic(client):  # noqa: ARG001 — chains the reset
    """Two builds from the same seed give the same dataset, byte for byte.
    This is what makes an idempotency gate possible on the consumer side."""
    from pennylane_mock.dataset.realiste import build_realiste_dataset

    a = build_realiste_dataset(42)
    b = build_realiste_dataset(42)
    assert a == b
    assert build_realiste_dataset(7) != a


def test_the_world_matches_the_sibling_mocks(client, donnees):  # noqa: ARG001
    """Cross-mock coherence is a property of the dataset, not chance: same
    company names, same anchor, same seed as boondmanager-mock. The AMOUNTS
    themselves are specific to this mock — see the README and the box in
    dataset/realiste.py."""
    noms = {c["name"] for c in donnees["customers"]}
    assert {"Lumina Retail", "Banque Hexagone", "Voltalis Énergie", "MediaQuartz"} <= noms
    assert {"Fivetech Partners", "Softalliance", "Foncière Beaumont"} <= {
        f["name"] for f in donnees["suppliers"]
    }
    assert all(
        f["invoice_number"].startswith(("FAC-2026-", "AV-2026-", ""))
        for f in donnees["customer_invoices"]
    )
    # BoondManager's prospect exists here WITHOUT any invoice.
    prospect = next(c for c in donnees["customers"] if c["name"] == "MediaQuartz")
    assert not [f for f in donnees["customer_invoices"] if f["customer"]["id"] == prospect["id"]]
