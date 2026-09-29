"""The evolution of the world — what makes incremental extraction testable.

No test sleeps: time advances EXPLICITLY via `/__admin/clock` or
`/__admin/evolve`. A suite that `sleep(60)`s to see an event is a suite that
eventually gets disabled.
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


def test_the_world_does_not_move_on_its_own_during_the_suite(client):
    """The interval is pushed to 3600 s by the conftest: no event fires on
    wall-clock time, even on a slow CI."""
    avant = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
    apres = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
    assert avant == apres
    assert _totaux(client)["evolution"]["rank"] == 0


@pytest.mark.parametrize("pas", [1, 6, 13, 30])
def test_trial_balance_still_balances_after_evolution(client, pas):
    """The accounting invariant holds ALSO while the world is alive. A mock
    left running for an hour must not end up serving false books."""
    client.post("/__admin/evolve", headers=ADMIN, json={"steps": pas})
    assert _ecart_balance(client) == 0


def test_evolution_is_deterministic(client):
    """Event k draws its randomness from `Random(f"{seed}:{k}")`: two mocks
    advanced by the same number of steps produce the same world. Without
    this, an incrementality test wouldn't be replayable."""
    client.post("/__admin/evolve", headers=ADMIN, json={"steps": 8})
    premier = client.get(f"{BASE}/customer_invoices?limit=100&sort=id", headers=H).json()

    client.post("/__admin/reset", headers=ADMIN, json={})
    client.post("/__admin/evolve", headers=ADMIN, json={"steps": 8})
    second = client.get(f"{BASE}/customer_invoices?limit=100&sort=id", headers=H).json()
    assert premier == second


def test_a_new_invoice_appears_and_is_logged(client):
    avant = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()
    borne = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()
    borne_at = borne["items"][-1]["processed_at"]

    # The cycle places a creation in third position.
    client.post("/__admin/evolve", headers=ADMIN, json={"steps": 3})
    apres = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()
    assert len(apres["items"]) > len(avant["items"])

    nouveaux = client.get(
        f"{BASE}/changelogs/customer_invoices?limit=1000&start_date={borne_at}", headers=H
    ).json()["items"]
    inserts = [e for e in nouveaux if e["operation"] == "insert" and e["processed_at"] > borne_at]
    assert inserts, "a creation must produce an `insert` event"


def test_a_payment_settles_the_invoice_and_creates_a_transaction(client):
    impayees_avant = [
        f
        for f in client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
        if not f["paid"] and not f["draft"] and f["status"] != "credit_note"
    ]
    transactions_avant = client.get(f"{BASE}/transactions?limit=100", headers=H).json()["items"]

    client.post("/__admin/evolve", headers=ADMIN, json={"steps": 2})

    impayees_apres = [
        f
        for f in client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()["items"]
        if not f["paid"] and not f["draft"] and f["status"] != "credit_note"
    ]
    transactions_apres = client.get(f"{BASE}/transactions?limit=100", headers=H).json()["items"]
    assert len(impayees_apres) == len(impayees_avant) - 1
    assert len(transactions_apres) == len(transactions_avant) + 1


def test_evolution_timestamps_are_strictly_later_than_the_base_dataset(client, donnees):
    """A cursor placed on the base dataset must render zero lines, and the
    FIRST evolution event is the first change it sees. Without this strict
    posteriority, an incrementality test would pass by accident."""
    from conftest import tout_paginer

    plafond = max(f["updated_at"] for f in donnees["customer_invoices"])
    client.post("/__admin/evolve", headers=ADMIN, json={"steps": 4})
    nouveaux = [
        f
        for f in tout_paginer(client, f"{BASE}/customer_invoices", limite=100)
        if f["updated_at"] > plafond
    ]
    assert nouveaux


def test_the_virtual_clock_triggers_evolution(client):
    """`/__admin/clock` advances time without sleeping. With a 3600 s
    interval, advancing two hours must produce exactly two events."""
    assert _totaux(client)["evolution"]["rank"] == 0
    client.post("/__admin/clock", headers=ADMIN, json={"advance_seconds": 7200})
    assert _totaux(client)["evolution"]["rank"] == 2


def test_the_evolution_log_is_observable(client):
    client.post("/__admin/evolve", headers=ADMIN, json={"steps": 6})
    journal = _totaux(client)["evolution"]["log"]
    assert [e["kind"] for e in journal] == [
        "customer_invoice_update",
        "customer_payment",
        "new_customer_invoice",
        "customer_update",
        "supplier_invoice",
        "orphan_transaction",
    ]


def test_a_reset_rewinds_the_timeline(client):
    client.post("/__admin/evolve", headers=ADMIN, json={"steps": 5})
    assert _totaux(client)["evolution"]["rank"] == 5
    client.post("/__admin/reset", headers=ADMIN, json={})
    assert _totaux(client)["evolution"]["rank"] == 0
