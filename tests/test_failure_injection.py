"""Les modes de panne — « the point of the mock is to reproduce failure modes ».

Chaque règle est pilotée par HTTP, parce que le mock tourne en CONTENEUR chez
son consommateur : hors du processus, un test ne peut plus muter l'état en
Python.
"""

from __future__ import annotations

from conftest import ADMIN, BASE, H


def _injecter(client, **regle):
    reponse = client.post("/__admin/inject", headers=ADMIN, json=regle)
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["rule"]["id"]


def test_le_429_a_un_corps_texte_et_pas_du_json(client):
    """LE piège de ce dialecte : un client qui appelle `.json()` sur un 429
    lève, et l'exception masque la vraie cause — une limite de débit."""
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
    # Les en-têtes `ratelimit-*` sont là AUSSI sur le 429.
    assert reponse.headers["ratelimit-limit"] == "25"
    assert reponse.headers["ratelimit-remaining"] == "0"


def test_une_panne_transitoire_cesse_d_elle_meme(client):
    """Sinon on ne teste pas un retry, on teste un échec."""
    _injecter(client, kind="status", scope=f"{BASE}/*", status=503, times=1)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 503
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_une_panne_persistante_ne_cesse_pas(client):
    """Elle doit faire échouer le run du consommateur avec un code non nul :
    « partial data that looks complete is worse than no data »."""
    _injecter(client, kind="status", scope=f"{BASE}/customers", status=500)
    for _ in range(4):
        assert client.get(f"{BASE}/customers", headers=H).status_code == 500
    # Le périmètre est respecté : les autres ressources répondent.
    assert client.get(f"{BASE}/suppliers", headers=H).status_code == 200


def test_le_scope_glob_vise_une_ressource_precise(client):
    _injecter(client, kind="status", scope=f"{BASE}/customer_invoices*", status=500)
    assert client.get(f"{BASE}/customer_invoices", headers=H).status_code == 500
    assert client.get(f"{BASE}/customer_invoices/1/invoice_lines", headers=H).status_code == 500
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_auth_reject_preempte_l_authentification(client):
    """Les pannes sont dispatchées AVANT le contrôle du jeton : c'est ce qui
    permet de simuler un jeton révoqué côté fournisseur sans toucher au nôtre."""
    _injecter(client, kind="auth_reject", scope=f"{BASE}/*")
    reponse = client.get(f"{BASE}/customers", headers=H)
    assert reponse.status_code == 401
    assert reponse.json()["error"] == "The access token is invalid"


def test_scope_reject_nomme_le_scope_manquant(client):
    """La panne la plus fréquente en intégration réelle : un jeton régénéré
    sans une case cochée."""
    _injecter(
        client,
        kind="scope_reject",
        scope=f"{BASE}/transactions",
        scope_manquant="transactions:readonly",
    )
    reponse = client.get(f"{BASE}/transactions", headers=H)
    assert reponse.status_code == 403
    attendu = 'Access to this resource requires scope "transactions:readonly".'
    assert reponse.json()["error"] == attendu


def test_cursor_reject_simule_un_curseur_expire(client):
    """« Cursors are temporary and should not be stored for long-term use. »
    Un consommateur qui persiste son curseur entre deux runs doit rencontrer ce
    400 en test, pas en production."""
    _injecter(client, kind="cursor_reject", scope=f"{BASE}/customer_invoices", times=1)
    reponse = client.get(f"{BASE}/customer_invoices", headers=H)
    assert reponse.status_code == 400
    assert reponse.json() == {"error": "Invalid cursor", "status": 400}


def test_une_regle_se_retire_et_se_vide(client):
    ident = _injecter(client, kind="status", scope="*", status=500)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 500
    client.delete(f"/__admin/inject/{ident}", headers=ADMIN)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200

    _injecter(client, kind="status", scope="*", status=500)
    client.post("/__admin/inject/clear", headers=ADMIN)
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_le_plan_de_controle_est_protege(client):
    """Sans jeton d'administration, il ne rend rien — même monté."""
    assert client.get("/__admin/state").status_code == 403
    assert client.post("/__admin/reset", json={}).status_code == 403
    assert client.post("/__admin/inject", json={"kind": "status"}).status_code == 403


def test_le_plan_de_controle_expose_les_parametres_recus(client):
    """La clé de voûte des tests aval : PROUVER que le consommateur a envoyé
    son curseur. Sans cette preuve, un pipeline qui l'aurait oublié passerait
    tous ses tests — il rechargerait simplement la première page à chaque fois."""
    client.get(f"{BASE}/customer_invoices?limit=3&sort=id", headers=H)
    corps = client.get(
        f"{BASE}/customer_invoices?limit=3&sort=id&cursor=eyJhZnRlciI6MH0", headers=H
    )
    assert corps.status_code == 200
    etat = client.get("/__admin/state", headers=ADMIN).json()
    vus = etat["last_query_params_by_path"][f"{BASE}/customer_invoices"]
    assert vus["cursor"] == "eyJhZnRlciI6MH0"
    assert etat["request_counts_by_path"][f"{BASE}/customer_invoices"] == 2


def test_le_reset_reconstruit_le_monde_et_vide_les_regles(client):
    _injecter(client, kind="status", scope="*", status=500)
    reponse = client.post("/__admin/reset", headers=ADMIN, json={"seed": 7})
    assert reponse.status_code == 200
    assert reponse.json()["seed"] == 7
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200
    assert client.get("/__admin/state", headers=ADMIN).json()["injections"] == []
