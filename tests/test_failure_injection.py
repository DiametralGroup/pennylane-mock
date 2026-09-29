"""Failure modes — "the point of the mock is to reproduce failure modes".

Each rule is driven over HTTP, because the mock runs as a CONTAINER on the
consumer's side: outside the process, a test can no longer mutate state in
Python.
"""

from __future__ import annotations

from conftest import ADMIN, BASE, H


def _injecter(client, **regle):
    reponse = client.post("/__admin/inject", headers=ADMIN, json=regle)
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["rule"]["id"]


def test_429_has_a_plain_text_body_not_json(client):
    """THE trap of this dialect: a client that calls `.json()` on a 429
    raises, and the exception masks the real cause — a rate limit."""
    _injecter(
        client,
        kind="rate_limit",
        scope=f"{BASE}/customers",
        after_requests=1,
        retry_after_seconds=2,
    )
    client.get(f"{BASE}/customers", headers=H)
    reponse = client.get(f"{BASE}/customers", headers=H)
    assert reponse.status_code == 429
    assert reponse.headers["content-type"].startswith("text/plain")
    assert reponse.text == "Rate limit exceeded. Please retry in 2 seconds."
    assert reponse.headers["retry-after"] == "2"
    # The `ratelimit-*` headers are ALSO present on the 429.
    assert reponse.headers["ratelimit-limit"] == "25"
    assert reponse.headers["ratelimit-remaining"] == "0"


def test_a_transient_failure_stops_on_its_own(client):
    """Otherwise we're not testing a retry, we're testing a failure."""
    _injecter(client, kind="status", scope=f"{BASE}/*", status=503, times=1)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 503
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_a_persistent_failure_does_not_stop(client):
    """It must fail the consumer's run with a non-zero code: "partial data
    that looks complete is worse than no data"."""
    _injecter(client, kind="status", scope=f"{BASE}/customers", status=500)
    for _ in range(4):
        assert client.get(f"{BASE}/customers", headers=H).status_code == 500
    # The scope is respected: other resources still respond.
    assert client.get(f"{BASE}/suppliers", headers=H).status_code == 200


def test_the_glob_scope_targets_one_specific_resource(client):
    _injecter(client, kind="status", scope=f"{BASE}/customer_invoices*", status=500)
    assert client.get(f"{BASE}/customer_invoices", headers=H).status_code == 500
    assert client.get(f"{BASE}/customer_invoices/1/invoice_lines", headers=H).status_code == 500
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_auth_reject_preempts_authentication(client):
    """Failures are dispatched BEFORE the token check: that's what makes it
    possible to simulate a token revoked at the provider without touching
    ours."""
    _injecter(client, kind="auth_reject", scope=f"{BASE}/*")
    reponse = client.get(f"{BASE}/customers", headers=H)
    assert reponse.status_code == 401
    assert reponse.json()["error"] == "The access token is invalid"


def test_scope_reject_names_the_missing_scope(client):
    """The most frequent failure in real-world integration: a token
    regenerated without a box checked."""
    _injecter(
        client,
        kind="scope_reject",
        scope=f"{BASE}/transactions",
        missing_scope="transactions:readonly",
    )
    reponse = client.get(f"{BASE}/transactions", headers=H)
    assert reponse.status_code == 403
    attendu = 'Access to this resource requires scope "transactions:readonly".'
    assert reponse.json()["error"] == attendu


def test_cursor_reject_simulates_an_expired_cursor(client):
    """ "Cursors are temporary and should not be stored for long-term use." A
    consumer who persists their cursor across two runs must run into this 400
    in a test, not in production."""
    _injecter(client, kind="cursor_reject", scope=f"{BASE}/customer_invoices", times=1)
    reponse = client.get(f"{BASE}/customer_invoices", headers=H)
    assert reponse.status_code == 400
    assert reponse.json() == {"error": "Invalid cursor", "status": 400}


def test_a_rule_can_be_removed_and_cleared(client):
    ident = _injecter(client, kind="status", scope="*", status=500)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 500
    client.delete(f"/__admin/inject/{ident}", headers=ADMIN)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200

    _injecter(client, kind="status", scope="*", status=500)
    client.post("/__admin/inject/clear", headers=ADMIN)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_the_control_plane_is_protected(client):
    """Without an admin token, it renders nothing — even when mounted."""
    assert client.get("/__admin/state").status_code == 403
    assert client.post("/__admin/reset", json={}).status_code == 403
    assert client.post("/__admin/inject", json={"kind": "status"}).status_code == 403


def test_the_control_plane_exposes_the_received_parameters(client):
    """The keystone of the downstream tests: PROVING that the consumer sent
    their cursor. Without this proof, a pipeline that forgot it would pass
    all its tests — it would simply reload the first page every time."""
    client.get(f"{BASE}/customer_invoices?limit=3&sort=id", headers=H)
    corps = client.get(
        f"{BASE}/customer_invoices?limit=3&sort=id&cursor=eyJhZnRlciI6MH0", headers=H
    )
    assert corps.status_code == 200
    etat = client.get("/__admin/state", headers=ADMIN).json()
    vus = etat["last_query_params_by_path"][f"{BASE}/customer_invoices"]
    assert vus["cursor"] == "eyJhZnRlciI6MH0"
    assert etat["request_counts_by_path"][f"{BASE}/customer_invoices"] == 2


def test_reset_restores_the_environment_s_scopes(client):
    """The control plane mutates a GLOBAL object: rebuilding the dataset
    alone isn't enough to restore it.

    Without this restoration, a test that removes a scope removes it for
    every test that follows — and the failure shows up tests later, on a 403
    that has nothing to do with what was actually being tested. This is
    exactly what happened to insights360's integration suite before this
    fix.
    """
    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": ["customers:readonly"]})
    assert client.get(f"{BASE}/journals", headers=H).status_code == 403

    client.post("/__admin/reset", headers=ADMIN, json={})
    assert client.get(f"{BASE}/journals", headers=H).status_code == 200
    etat = client.get("/__admin/state", headers=ADMIN).json()
    assert len(etat["scopes"]) > 1


def test_reset_rebuilds_the_world_and_clears_the_rules(client):
    _injecter(client, kind="status", scope="*", status=500)
    reponse = client.post("/__admin/reset", headers=ADMIN, json={"seed": 7})
    assert reponse.status_code == 200
    assert reponse.json()["seed"] == 7
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200
    assert client.get("/__admin/state", headers=ADMIN).json()["injections"] == []
