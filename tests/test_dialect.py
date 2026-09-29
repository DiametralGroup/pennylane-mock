"""The Pennylane dialect — what breaks a consumer if it isn't exact."""

from __future__ import annotations

import pytest
from conftest import ADMIN, BASE, H


def test_health_is_open(client):
    """The probe is NOT authenticated: the image's healthcheck hits it, and
    `depends_on: service_healthy` depends on that on the consumer side."""
    reponse = client.get("/health")
    assert reponse.status_code == 200
    assert reponse.json() == {"status": "ok", "service": "pennylane-mock"}


def test_no_token_is_401_in_the_pennylane_envelope(client):
    reponse = client.get(f"{BASE}/customer_invoices")
    assert reponse.status_code == 401
    assert reponse.json() == {"error": "The access token is invalid", "status": 401}


@pytest.mark.parametrize(
    "entete",
    [
        {},
        {"Authorization": "mock-pennylane-token"},  # no scheme
        {"Authorization": "Basic bW9jazptb2Nr"},  # wrong scheme
        {"Authorization": "Bearer mauvais-jeton"},
        {"Authorization": "Bearer "},
    ],
)
def test_every_way_of_having_a_bad_token_gives_the_same_401(client, entete):
    """The three cases — missing, invalid, expired — are INDISTINGUISHABLE at
    the provider: a single message, no clue as to which of the three."""
    reponse = client.get(f"{BASE}/customers", headers=entete)
    assert reponse.status_code == 401
    assert reponse.json()["error"] == "The access token is invalid"


def test_bearer_is_case_insensitive(client):
    """HTTP libraries write `Bearer` just as often as `bearer`; rejecting the
    latter would be a strictness the provider doesn't have."""
    assert (
        client.get(
            f"{BASE}/customers", headers={"Authorization": "bearer mock-pennylane-token"}
        ).status_code
        == 200
    )


def test_missing_scope_is_403_and_the_message_names_the_scope(client):
    """The only actionable piece of information in the whole API: WITHOUT the
    scope's name, one just regenerates a token at random."""
    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": ["customers:readonly"]})
    reponse = client.get(f"{BASE}/customer_invoices", headers=H)
    assert reponse.status_code == 403
    assert reponse.json() == {
        "error": 'Access to this resource requires scope "customer_invoices:readonly".',
        "status": 403,
    }
    # The retained scope still passes: it's really the SCOPE that's
    # restricted, not the token that's broken.
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_an_all_scope_covers_readonly(client):
    """An endpoint that "requires one of x:readonly, x:all" must accept the
    latter."""
    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": ["customers:all"]})
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_me_requires_no_scope(client):
    """It's the very endpoint used to discover scopes: requiring one would be
    circular."""
    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": []})
    reponse = client.get(f"{BASE}/me", headers=H)
    assert reponse.status_code == 200
    assert reponse.json()["scopes"] == []
    assert reponse.json()["company"]["accounting_logic"] == "FR_PCG"


def test_unknown_route_renders_the_pennylane_envelope(client):
    """A `{"detail": "Not Found"}` would teach the consumer an error shape
    that doesn't exist at the provider."""
    reponse = client.get(f"{BASE}/nimportequoi", headers=H)
    assert reponse.status_code == 404
    assert reponse.json() == {"error": "Not Found", "status": 404}
    assert "detail" not in reponse.json()


def test_write_method_is_refused_in_the_dialect(client):
    """The mock is READ ONLY. A POST renders the Pennylane envelope, not
    FastAPI's 405 — a consumer must never see a foreign shape."""
    reponse = client.post(f"{BASE}/customer_invoices", headers=H, json={})
    assert reponse.status_code == 404
    assert reponse.json() == {"error": "Not Found", "status": 404}


def test_rate_limit_headers_are_on_every_response(client):
    """Not only on 429s: that's what lets a client throttle itself BEFORE
    getting rate limited."""
    reponse = client.get(f"{BASE}/customers", headers=H)
    assert reponse.status_code == 200
    assert reponse.headers["ratelimit-limit"] == "25"
    assert int(reponse.headers["ratelimit-remaining"]) >= 0
    assert reponse.headers["ratelimit-reset"].isdigit()
    # `retry-after` only appears on a 429.
    assert "retry-after" not in reponse.headers


def test_amounts_are_strings(client):
    """`amount: "230.32"`, never `230.32`. A connector that receives a number
    here and tolerates it will break against the real API."""
    facture = client.get(f"{BASE}/customer_invoices?limit=1", headers=H).json()["items"][0]
    for champ in (
        "amount",
        "currency_amount",
        "currency_amount_before_tax",
        "tax",
        "exchange_rate",
    ):
        assert isinstance(facture[champ], str), f"{champ} must be a string"
        float(facture[champ])  # and remain numerically readable

    ligne = client.get(f"{BASE}/ledger_entry_lines?limit=1", headers=H).json()["items"][0]
    assert isinstance(ligne["debit"], str) and isinstance(ligne["credit"], str)
    # Both coexist: one holds "0.00", never null.
    assert "0.00" in (ligne["debit"], ligne["credit"])


def test_nested_collections_are_links(client):
    """The most structuring shape difference between v1 and v2. A connector
    written for v1 reads an empty list and loads zero lines WITHOUT an
    error."""
    facture = client.get(f"{BASE}/customer_invoices?limit=1", headers=H).json()["items"][0]
    for champ in ("invoice_lines", "payments", "matched_transactions", "categories"):
        assert isinstance(facture[champ], dict), f"{champ} must be a link, not an array"
        assert facture[champ]["url"].startswith("https://app.pennylane.com/api/external/v2/")


def test_page_envelope_has_exactly_three_keys(client):
    corps = client.get(f"{BASE}/customers?limit=2", headers=H).json()
    assert set(corps) == {"items", "has_more", "next_cursor"}


def test_next_cursor_is_null_at_the_end_not_absent(client):
    """A consumer who tests `if "next_cursor" in body` loops forever."""
    corps = client.get(f"{BASE}/journals?limit=100", headers=H).json()
    assert corps["has_more"] is False
    assert "next_cursor" in corps
    assert corps["next_cursor"] is None
