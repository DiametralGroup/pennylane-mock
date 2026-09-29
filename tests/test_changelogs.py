"""The changelogs — Pennylane's native incremental extraction.

Four dialect rules, each capable of breaking a connector in production
without it showing up in a test if the mock doesn't reproduce them.
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
def test_all_ten_changelogs_are_served(client, famille):
    """Seven are documented in the guide; `quotes` and the two `*_categories`
    only appear in the reference. A consumer who stuck to the guide would
    miss three families of changes."""
    reponse = client.get(f"{BASE}/changelogs/{famille}?limit=1000", headers=H)
    assert reponse.status_code == 200, reponse.text
    assert set(reponse.json()) == {"items", "has_more", "next_cursor"}


def test_an_event_carries_id_and_operation_never_the_state(client):
    """A SECOND call is required to get the resource. That's the pattern the
    provider recommends, and the one a connector must exercise."""
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=5", headers=H).json()[
        "items"
    ]
    assert evenements
    for evenement in evenements:
        assert set(evenement) == {"id", "operation", "processed_at", "created_at", "updated_at"}
        assert evenement["operation"] in {"insert", "update", "delete"}


def test_order_is_chronological_ascending(client):
    """A consumer who assumes the opposite sets their resume point on the
    FIRST event of the page and loses everything again on every pass."""
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()[
        "items"
    ]
    horodatages = [e["processed_at"] for e in evenements]
    assert horodatages == sorted(horodatages)


def test_four_week_retention_actually_purges(client):
    """ "Changes are retained for 4 weeks." An older event no longer exists at
    all — it's not merely unreachable via `start_date`. A mock that served
    the full history would teach the consumer that a full resync via the
    changelog is possible: it isn't, and that's what breaks a pipeline that's
    been stopped for five weeks."""
    from datetime import timedelta

    from pennylane_mock.evolution import EPOQUE

    limite = EPOQUE - timedelta(days=28)
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()[
        "items"
    ]
    assert evenements
    assert all(e["processed_at"] >= limite.strftime("%Y-%m-%dT%H:%M:%S.%fZ") for e in evenements)


def test_a_start_date_outside_retention_renders_422(client):
    """422, not a truncated list: that's the difference between "nothing
    moved" and "my window is too wide"."""
    reponse = client.get(f"{BASE}/changelogs/customers?start_date=2020-01-01T00:00:00Z", headers=H)
    assert reponse.status_code == 422
    assert "retention" in reponse.json()["error"]


def test_start_date_and_cursor_together_render_400(client):
    """Pagination CONTINUES a window, it doesn't open a new one. Without this
    rejection, a consumer who resends their `start_date` on every page
    replays the first one forever and believes they've read everything."""
    reponse = client.get(
        f"{BASE}/changelogs/customers?start_date=2026-07-01T00:00:00Z&cursor=abc", headers=H
    )
    assert reponse.status_code == 400
    assert "together" in reponse.json()["error"]


@pytest.mark.parametrize("brut", ["hier", "2026-13-45", "15/07/2026"])
def test_a_malformed_start_date_renders_400(client, brut):
    reponse = client.get(f"{BASE}/changelogs/customers?start_date={brut}", headers=H)
    assert reponse.status_code == 400
    assert "RFC3339" in reponse.json()["error"]


def test_start_date_does_narrow_the_window(client):
    tout = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()["items"]
    milieu = tout[len(tout) // 2]["processed_at"]
    depuis = client.get(
        f"{BASE}/changelogs/customer_invoices?limit=1000&start_date={milieu}", headers=H
    ).json()["items"]
    assert 0 < len(depuis) < len(tout)
    assert all(e["processed_at"] >= milieu for e in depuis)


def test_a_mutation_produces_exactly_one_event(client):
    """The consumer-side incrementality test's tool: after this call, exactly
    ONE line must be reloaded."""
    avant = client.get(f"{BASE}/changelogs/customer_invoices?limit=1000", headers=H).json()["items"]
    borne = avant[-1]["processed_at"]

    client.post(
        "/__admin/mutate",
        headers=ADMIN,
        json={"collection": "customer_invoices", "id": 3, "fields": {"label": "mutated"}},
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
    assert client.get(f"{BASE}/customer_invoices/3", headers=H).json()["label"] == "mutated"


def test_batch_reload_pattern_does_reload_the_resources(client):
    """The full pattern recommended by the provider: read the changes,
    collect the identifiers, then ONE filtered call `id in [...]`."""
    evenements = client.get(f"{BASE}/changelogs/customer_invoices?limit=10", headers=H).json()[
        "items"
    ]
    identifiants = sorted({e["id"] for e in evenements})
    filtre = f'[{{"field":"id","operator":"in","value":{identifiants}}}]'
    items = client.get(f"{BASE}/customer_invoices?limit=100&filter={filtre}", headers=H).json()[
        "items"
    ]
    assert sorted(e["id"] for e in items) == identifiants
