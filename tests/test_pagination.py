"""The cursor — the fifth pagination dialect in the insights360 ecosystem.

BoondManager paginates by `page`/`maxResults`, Graph by `@odata.nextLink`,
LinkedIn by `start`/`count`, GA4 by `limit`/`offset`. A connector with "one"
generic loop breaks here, and that's the point.
"""

from __future__ import annotations

import pytest
from conftest import BASE, H, tout_paginer


def test_full_walk_neither_loses_nor_duplicates_any_line(client):
    tout = tout_paginer(client, f"{BASE}/customer_invoices", limite=7)
    identifiants = [element["id"] for element in tout]
    assert len(identifiants) == len(set(identifiants)), "duplicates between two pages"
    reference = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()
    assert reference["has_more"] is False
    assert sorted(identifiants) == sorted(e["id"] for e in reference["items"])


def test_last_page_announces_has_more_false_and_next_cursor_null(client):
    curseur, corps = None, None
    for _ in range(100):
        url = f"{BASE}/journals?limit=2" + (f"&cursor={curseur}" if curseur else "")
        corps = client.get(url, headers=H).json()
        if not corps["has_more"]:
            break
        curseur = corps["next_cursor"]
    assert corps is not None and corps["has_more"] is False
    assert corps["next_cursor"] is None


@pytest.mark.parametrize("valeur", ["0", "-1", "101", "9999", "abc", "2.5"])
def test_limit_out_of_bounds_renders_400_and_is_not_clamped(client, valeur):
    """A silent cap makes a pipeline believe it asked for 5000 lines and got
    them all, when it actually read 100. This is pagination's costliest
    defect, because it's invisible anywhere."""
    reponse = client.get(f"{BASE}/customers?limit={valeur}", headers=H)
    assert reponse.status_code == 400
    assert reponse.json()["status"] == 400
    assert "1 and 100" in reponse.json()["error"]


def test_changelog_limit_ceiling_is_higher(client):
    """1000 on changelogs, 100 on ordinary lists — the OpenAPI spec declares
    it this way, endpoint by endpoint."""
    assert client.get(f"{BASE}/changelogs/customers?limit=1000", headers=H).status_code == 200
    assert client.get(f"{BASE}/changelogs/customers?limit=1001", headers=H).status_code == 400
    assert client.get(f"{BASE}/customers?limit=1000", headers=H).status_code == 400


def test_default_limit_is_twenty(client):
    corps = client.get(f"{BASE}/customer_invoices", headers=H).json()
    assert len(corps["items"]) == 20


@pytest.mark.parametrize("curseur", ["%%%", "pas-du-base64!", "eyJ0cnVuY2F0", "bnVsbA"])
def test_an_unreadable_cursor_renders_400(client, curseur):
    reponse = client.get(f"{BASE}/customers?cursor={curseur}", headers=H)
    assert reponse.status_code == 400
    assert reponse.json()["status"] == 400


def test_default_sort_is_descending(client):
    """`-id` by default: it's the opposite of intuition, and it's what the
    OpenAPI spec declares. A consumer who assumes ascending order sets their
    resume point on the most RECENT record and never sees anything again."""
    par_defaut = client.get(f"{BASE}/customer_invoices?limit=5", headers=H).json()["items"]
    explicite = client.get(f"{BASE}/customer_invoices?limit=5&sort=id", headers=H).json()["items"]
    decroissant = [e["id"] for e in par_defaut]
    croissant = [e["id"] for e in explicite]
    assert decroissant == sorted(decroissant, reverse=True)
    assert croissant == sorted(croissant)
    assert decroissant != croissant


def test_sort_on_another_field(client):
    items = client.get(f"{BASE}/customer_invoices?limit=10&sort=date", headers=H).json()["items"]
    dates = [e["date"] for e in items]
    assert dates == sorted(dates)


def test_cursor_does_not_encode_filters(client):
    """THE dialect's trap, documented in black and white by the provider:
    "Omitting the filters on page 2+ will return unfiltered results from the
    cursor position." No 400, no warning — extra lines, silently. A pipeline
    that forgets to replay its `filter` loads lines it thought it had
    excluded."""
    # A filter whose retained lines are SPREAD OUT: it's the only way to make
    # the trap visible — with a filter that selects a contiguous block, the
    # unfiltered page 2 would randomly land on compliant lines, and the test
    # would pass without proving anything.
    retenus = [1, 5, 9, 13]
    filtre = '[{"field":"id","operator":"in","value":[1,5,9,13]}]'
    page1 = client.get(
        f"{BASE}/customer_invoices?limit=2&sort=id&filter={filtre}", headers=H
    ).json()
    assert [e["id"] for e in page1["items"]] == [1, 5]
    assert page1["has_more"]

    # Page 2 WITHOUT replaying the filter: the provider doesn't complain.
    sans = client.get(
        f"{BASE}/customer_invoices?limit=2&sort=id&cursor={page1['next_cursor']}", headers=H
    )
    assert sans.status_code == 200
    assert any(e["id"] not in retenus for e in sans.json()["items"]), (
        "the mock must REPRODUCE the trap: without the filter, page 2 renders "
        "unfiltered lines, with no error"
    )

    # Page 2 WITH the filter replayed: the correct behavior.
    avec = client.get(
        f"{BASE}/customer_invoices?limit=2&sort=id&filter={filtre}&cursor={page1['next_cursor']}",
        headers=H,
    ).json()
    assert [e["id"] for e in avec["items"]] == [9, 13]


def test_filters_stack_with_and(client):
    filtre = (
        '[{"field":"paid","operator":"eq","value":true},'
        '{"field":"date","operator":"gteq","value":"2026-04-01"}]'
    )
    items = client.get(f"{BASE}/customer_invoices?limit=100&filter={filtre}", headers=H).json()[
        "items"
    ]
    assert items
    assert all(e["paid"] and e["date"] >= "2026-04-01" for e in items)


def test_the_in_operator_takes_an_array(client):
    """This is the pattern the provider recommends for reloading the
    resources of a batch of changes: `id in [...]`."""
    filtre = '[{"field":"id","operator":"in","value":[1,2,3]}]'
    items = client.get(f"{BASE}/customer_invoices?filter={filtre}", headers=H).json()["items"]
    assert sorted(e["id"] for e in items) == [1, 2, 3]


def test_start_with_is_case_insensitive(client):
    filtre = '[{"field":"number","operator":"start_with","value":"411"}]'
    items = client.get(f"{BASE}/ledger_accounts?limit=100&filter={filtre}", headers=H).json()[
        "items"
    ]
    assert items and all(e["number"].startswith("411") for e in items)


def test_an_unknown_operator_renders_400(client):
    """The list of nine is short and stable: an operator invented on the
    consumer side is a real bug, and it must show up."""
    filtre = '[{"field":"id","operator":"like","value":1}]'
    reponse = client.get(f"{BASE}/customer_invoices?filter={filtre}", headers=H)
    assert reponse.status_code == 400
    assert "like" in reponse.json()["error"]


@pytest.mark.parametrize("brut", ["pas-du-json", '{"field":"id"}', '[{"field":"id"}]'])
def test_a_malformed_filter_renders_400(client, brut):
    assert client.get(f"{BASE}/customers?filter={brut}", headers=H).status_code == 400


# ── The envelope is not uniform, and the OpenAPI spec doesn't say so ────────

#: The four collections whose envelope ALSO carries offset pagination,
#: observed against a real instance on 2026-09-04 via
#: `scripts/compare_real.py`.
AVEC_OFFSET = ("journals", "ledger_accounts", "ledger_entries", "fiscal_years")

#: A sample of the ones that do NOT carry it — including `ledger_entry_lines`,
#: from the same accounting family as three of the four above. This
#: proximity is exactly what rules out guessing a rule.
SANS_OFFSET = ("ledger_entry_lines", "customers", "suppliers", "transactions", "products")

CLES_OFFSET = {"current_page", "per_page", "total_items", "total_pages"}
CLES_CURSEUR = {"items", "has_more", "next_cursor"}


@pytest.mark.parametrize("collection", AVEC_OFFSET)
def test_these_four_collections_also_render_offset_but_EMPTY(client, collection):
    """The mock used to claim "exactly three keys" on the strength of the
    OpenAPI spec.

    Checked against a real instance, that's wrong twice over: these four add
    `current_page`, `per_page`, `total_items` and `total_pages` — and all
    four are `null`. Present and empty.

    This is the trap to reproduce: a consumer who tests their PRESENCE to
    choose their pagination mode finds them, switches to offset, and reads
    `null` everywhere — with no error. COMPUTING them, as the first version
    of this fix did, would be more useful and therefore more wrong: a mock
    that renders a total where the provider renders `null` validates code
    that breaks in production.
    """
    corps = client.get(f"{BASE}/{collection}?limit=2", headers=H).json()
    assert set(corps) >= CLES_CURSEUR, "the cursor remains the safe path, everywhere"
    assert set(corps) >= CLES_OFFSET, f"{collection} must carry the offset keys"
    assert all(corps[cle] is None for cle in CLES_OFFSET), (
        f"{collection}: the offset keys must be NULL — the provider doesn't "
        "fill them under cursor pagination."
    )


@pytest.mark.parametrize("collection", SANS_OFFSET)
def test_the_others_do_NOT_render_it(client, collection):
    """The asymmetry is the fact to reproduce, not a detail to smooth over.

    `ledger_entry_lines` is from the same accounting family as
    `ledger_entries` and has no offset. So there is no rule to guess — only
    an observation. Serving the offset everywhere would be just as wrong as
    nowhere, and would invent a third dialect that exists nowhere.
    """
    corps = client.get(f"{BASE}/{collection}?limit=2", headers=H).json()
    assert set(corps) == CLES_CURSEUR, f"{collection} must render only the cursor"


def test_offset_stays_null_even_when_paging_forward(client):
    """Inert means inert: nothing gets filled in on the next page.

    This test used to say the opposite as long as the mock computed the
    values. It's worth keeping in its reverted form: it's the trace of the
    mistake, and the guarantee that it won't be redone by finding the
    `null`s "useless".
    """
    premiere = client.get(f"{BASE}/ledger_entries?limit=1", headers=H).json()
    if not premiere["has_more"]:
        pytest.skip("dataset too short for a second page")
    suivante = client.get(
        f"{BASE}/ledger_entries?limit=1&cursor={premiere['next_cursor']}", headers=H
    ).json()
    assert all(suivante[cle] is None for cle in CLES_OFFSET)
    assert suivante["next_cursor"] != premiere["next_cursor"], (
        "the cursor, on the other hand, advances"
    )
