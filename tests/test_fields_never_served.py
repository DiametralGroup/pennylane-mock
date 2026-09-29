"""What the provider NEVER serves, and the rate code that isn't a number.

┌─ THESE TESTS ARE WRITTEN BACKWARDS FROM THE OTHERS ─────────────────────────┐
│ They don't assert that a value IS there. They assert that it is NOT —       │
│ because the defect they close is a mock more GENEROUS and more REGULAR     │
│ than the provider, and an overly generous mock never shows up in any test   │
│ that checks for presence.                                                   │
│                                                                              │
│ Observed on a real tenant on 2026-09-07:                                    │
│   * `analytical_code` null on 159 categories out of 159;                    │
│   * no `product` on the lines of 1,898 customer invoices;                   │
│   * no `ledger_account` on the lines of 4,559 supplier invoices;            │
│   * a single account's `vat_rate`: `any` (2,633), `FR_200` (166),           │
│     `exempt` (141), `extracom` (51), `crossborder` (40), `FR_100`,          │
│     `FR_55`, `FR_15_385`.                                                   │
│                                                                              │
│ The cost of the old behavior was real: a consumer that infers its schema    │
│ from the data did NOT materialize these columns in production — it failed  │
│ with "column does not exist" — and cast `vat_rate` to numeric, which broke  │
│ on the first `any`.                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import pytest
from conftest import ADMIN, BASE, H

import pennylane_mock as mock

#: The `vat_rate` values that are NOT country codes: they're what breaks a
#: `cast(... as numeric)`, and the mock must serve them.
NON_NUMERIQUES = {"any", "exempt", "extracom", "crossborder"}


def _tous(client, chemin: str) -> list[dict]:
    """All the pages of a collection, whatever its pagination style.

    Warning: the envelope is NOT UNIFORM, and this is deliberate on the mock's
    side: some collections paginate by `page`, others by `next_cursor`. A
    helper that only knew `page` would loop forever on the latter — the
    cursor being ignored, every round receives the same first page with
    `has_more` always true. This happened while writing this file, on
    `/ledger_entries`.
    """
    elements: list[dict] = []
    params: dict[str, object] = {"limit": 100}
    page = 1
    while True:
        r = client.get(f"{BASE}{chemin}", headers=H, params=params)
        assert r.status_code == 200, r.text
        corps = r.json()
        elements += corps["items"]
        if not corps.get("has_more"):
            return elements
        if curseur := corps.get("next_cursor"):
            params = {"limit": 100, "cursor": curseur}
        else:
            page += 1
            params = {"limit": 100, "page": page}


# ── `vat_rate` is a code ─────────────────────────────────────────────────────


def test_vat_rate_of_an_account_is_never_a_number(client):
    """No `vat_rate` should read back as a decimal.

    The phrasing is what matters: "looks like a number" rather than "equals
    0.0". It also closes the variants ("20", "0,0") that nobody has written
    yet.
    """
    comptes = _tous(client, "/ledger_accounts")
    assert comptes, "chart of accounts is empty — the test would prove nothing"
    for compte in comptes:
        with pytest.raises(ValueError):
            float(compte["vat_rate"].replace(",", "."))


def test_chart_of_accounts_serves_both_code_families(client):
    """Both country codes AND non-numeric codes, on the same chart.

    Serving only `any` would make the mock strict but wrong in the other
    direction: a consumer would never see an `FR_200`, which is indeed the
    shape of accounts subject to a rate.
    """
    codes = {c["vat_rate"] for c in _tous(client, "/ledger_accounts")}
    assert codes & NON_NUMERIQUES, f"no non-numeric code served: {sorted(codes)}"
    assert {c for c in codes if c.startswith("FR_")}, f"no country code served: {sorted(codes)}"


def test_any_is_the_most_common_value(client):
    """As with the provider: 2,633 accounts out of ~3,100.

    A marginal `any` would get worked around by a consumer filtering out
    "the few weird accounts". It is the majority, hence unavoidable.
    """
    codes = [c["vat_rate"] for c in _tous(client, "/ledger_accounts")]
    assert codes.count("any") > len(codes) / 2


# ── Optional fields that are never filled in ─────────────────────────────────


def test_no_category_carries_an_analytical_code(client):
    categories = _tous(client, "/categories")
    assert categories, "no categories — the test would prove nothing"
    assert all(c["analytical_code"] is None for c in categories)


def test_analytical_code_key_is_present_but_null(client):
    """`null`, not absent.

    The OpenAPI spec declares the field and the provider renders it. A mock
    that OMITTED the key would diverge in the other direction and would hide
    from the consumer that it exists.
    """
    categories = _tous(client, "/categories")
    assert all("analytical_code" in c for c in categories)


def test_no_breakdown_carries_an_analytical_code(client):
    """A breakdown copies its category: it must be blanked out along with it.

    Without this test, the mock would contradict itself — category without a
    code, breakdown targeting it WITH one — and a consumer reading the latter
    would stay green.
    """
    vues = 0
    for ecriture in _tous(client, "/ledger_entries"):
        for ventilation in ecriture.get("categories") or []:
            vues += 1
            assert ventilation["analytical_code"] is None
    assert vues, "no breakdown entry seen — the test would prove nothing"


def test_no_customer_invoice_line_carries_a_product(client):
    factures = _tous(client, "/customer_invoices")[:10]
    lignes = [
        ligne
        for facture in factures
        for ligne in _tous(client, f"/customer_invoices/{facture['id']}/invoice_lines")
    ]
    assert lignes, "no sales line — the test would prove nothing"
    assert all(ligne["product"] is None for ligne in lignes)


def test_no_supplier_invoice_line_carries_a_ledger_account(client):
    factures = _tous(client, "/supplier_invoices")[:10]
    lignes = [
        ligne
        for facture in factures
        for ligne in _tous(client, f"/supplier_invoices/{facture['id']}/invoice_lines")
    ]
    assert lignes, "no purchase line — the test would prove nothing"
    assert all(ligne["ledger_account"] is None for ligne in lignes)


# ── The switch, and the fact that it really is a switch ──────────────────────


def test_flag_restores_the_rich_shape(client, monkeypatch):
    """`PENNYLANE_MOCK_OPTIONAL_FIELDS=1` renders all three fields.

    The rich shape remains LEGITIMATE — another tenant may fill in these
    fields. What changes is the default: the strict shape.
    """
    monkeypatch.setenv("PENNYLANE_MOCK_OPTIONAL_FIELDS", "1")
    mock.state.reset()

    assert any(c["analytical_code"] for c in _tous(client, "/categories"))

    factures = _tous(client, "/customer_invoices")[:10]
    lignes = [
        ligne
        for facture in factures
        for ligne in _tous(client, f"/customer_invoices/{facture['id']}/invoice_lines")
    ]
    assert any(ligne["product"] for ligne in lignes)


def test_evolution_follows_the_same_regime(client):
    """An invoice CREATED along the way must not reopen the divergence.

    The blanking is done on the dataset as built; evolution, on the other
    hand, manufactures new lines afterward. Without the same safeguard there,
    the first invoice issued by the clock would serve a `product` again — and
    the divergence would come back through the back door, on only the RECENT
    lines, i.e. precisely the ones an incremental extraction reads.

    `new_customer_invoice` is the third event of the cycle: three steps are
    enough, and the test FAILS if no invoice has appeared — settling for a
    `skip` would make its coverage void the day the cycle changes.
    """
    avant = {f["id"] for f in _tous(client, "/customer_invoices")}
    r = client.post("/__admin/evolve", headers=ADMIN, json={"steps": 3})
    assert r.status_code == 200, r.text

    nouvelles = {f["id"] for f in _tous(client, "/customer_invoices")} - avant
    assert nouvelles, "evolution emitted no invoice — the test would prove nothing"
    for ident in nouvelles:
        lignes = _tous(client, f"/customer_invoices/{ident}/invoice_lines")
        assert lignes, f"invoice {ident} has no line"
        assert all(ligne["product"] is None for ligne in lignes)
