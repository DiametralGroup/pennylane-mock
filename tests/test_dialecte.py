"""Le dialecte Pennylane — ce qui casse un consommateur s'il n'est pas exact."""

from __future__ import annotations

import pytest
from conftest import ADMIN, BASE, H


def test_health_est_ouvert(client):
    """La sonde n'est PAS authentifiée : le healthcheck de l'image l'interroge,
    et `depends_on: service_healthy` en dépend côté consommateur."""
    reponse = client.get("/health")
    assert reponse.status_code == 200
    assert reponse.json() == {"status": "ok", "service": "pennylane-mock"}


def test_sans_jeton_c_est_401_a_l_enveloppe_pennylane(client):
    reponse = client.get(f"{BASE}/customer_invoices")
    assert reponse.status_code == 401
    assert reponse.json() == {"error": "The access token is invalid", "status": 401}


@pytest.mark.parametrize(
    "entete",
    [
        {},
        {"Authorization": "mock-pennylane-token"},  # sans le schéma
        {"Authorization": "Basic bW9jazptb2Nr"},  # mauvais schéma
        {"Authorization": "Bearer mauvais-jeton"},
        {"Authorization": "Bearer "},
    ],
)
def test_toutes_les_facons_d_avoir_un_mauvais_jeton_donnent_le_meme_401(client, entete):
    """Les trois cas — absent, invalide, expiré — sont INDISTINCTS chez le
    fournisseur : un seul message, aucun indice sur lequel des trois."""
    reponse = client.get(f"{BASE}/customers", headers=entete)
    assert reponse.status_code == 401
    assert reponse.json()["error"] == "The access token is invalid"


def test_bearer_est_insensible_a_la_casse(client):
    """Les bibliothèques HTTP écrivent aussi bien `Bearer` que `bearer` ;
    refuser la seconde serait une sévérité que le fournisseur n'a pas."""
    assert (
        client.get(
            f"{BASE}/customers", headers={"Authorization": "bearer mock-pennylane-token"}
        ).status_code
        == 200
    )


def test_scope_manquant_c_est_403_et_le_message_nomme_le_scope(client):
    """La seule information actionnable de toute l'API : SANS le nom du scope,
    on regénère un jeton au hasard."""
    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": ["customers:readonly"]})
    reponse = client.get(f"{BASE}/customer_invoices", headers=H)
    assert reponse.status_code == 403
    assert reponse.json() == {
        "error": 'Access to this resource requires scope "customer_invoices:readonly".',
        "status": 403,
    }
    # Le scope conservé continue de passer : c'est bien le PÉRIMÈTRE qui est
    # restreint, pas le jeton qui est cassé.
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_un_scope_all_couvre_le_readonly(client):
    """Un endpoint « requires one of x:readonly, x:all » doit accepter le second."""
    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": ["customers:all"]})
    assert client.get(f"{BASE}/customers", headers=H).status_code == 200


def test_me_ne_demande_aucun_scope(client):
    """C'est LUI qui sert à découvrir les scopes : les exiger serait circulaire."""
    client.post("/__admin/scopes", headers=ADMIN, json={"scopes": []})
    reponse = client.get(f"{BASE}/me", headers=H)
    assert reponse.status_code == 200
    assert reponse.json()["scopes"] == []
    assert reponse.json()["company"]["accounting_logic"] == "FR_PCG"


def test_route_inconnue_rend_l_enveloppe_pennylane(client):
    """Un `{"detail": "Not Found"}` apprendrait au consommateur une forme
    d'erreur qui n'existe pas chez le fournisseur."""
    reponse = client.get(f"{BASE}/nimportequoi", headers=H)
    assert reponse.status_code == 404
    assert reponse.json() == {"error": "Not Found", "status": 404}
    assert "detail" not in reponse.json()


def test_methode_d_ecriture_refusee_au_dialecte(client):
    """Le mock est en LECTURE SEULE. Un POST rend l'enveloppe Pennylane, pas le
    405 de FastAPI — un consommateur ne doit jamais voir de forme étrangère."""
    reponse = client.post(f"{BASE}/customer_invoices", headers=H, json={})
    assert reponse.status_code == 404
    assert reponse.json() == {"error": "Not Found", "status": 404}


def test_les_entetes_de_debit_sont_sur_toute_reponse(client):
    """Pas seulement sur les 429 : c'est ce qui permet à un client de se réguler
    AVANT de se faire limiter."""
    reponse = client.get(f"{BASE}/customers", headers=H)
    assert reponse.status_code == 200
    assert reponse.headers["ratelimit-limit"] == "25"
    assert int(reponse.headers["ratelimit-remaining"]) >= 0
    assert reponse.headers["ratelimit-reset"].isdigit()
    # `retry-after` n'apparaît QUE sur un 429.
    assert "retry-after" not in reponse.headers


def test_les_montants_sont_des_chaines(client):
    """`amount: "230.32"`, jamais `230.32`. Un connecteur qui reçoit un nombre
    ici et le tolère se cassera contre la vraie API."""
    facture = client.get(f"{BASE}/customer_invoices?limit=1", headers=H).json()["items"][0]
    for champ in (
        "amount",
        "currency_amount",
        "currency_amount_before_tax",
        "tax",
        "exchange_rate",
    ):
        assert isinstance(facture[champ], str), f"{champ} doit être une chaîne"
        float(facture[champ])  # et rester numériquement lisible

    ligne = client.get(f"{BASE}/ledger_entry_lines?limit=1", headers=H).json()["items"][0]
    assert isinstance(ligne["debit"], str) and isinstance(ligne["credit"], str)
    # Les deux coexistent : l'un vaut "0.00", jamais null.
    assert "0.00" in (ligne["debit"], ligne["credit"])


def test_les_collections_imbriquees_sont_des_liens(client):
    """La différence de forme la plus structurante entre v1 et v2. Un connecteur
    écrit pour la v1 lit une liste vide et charge zéro ligne SANS erreur."""
    facture = client.get(f"{BASE}/customer_invoices?limit=1", headers=H).json()["items"][0]
    for champ in ("invoice_lines", "payments", "matched_transactions", "categories"):
        assert isinstance(facture[champ], dict), f"{champ} doit être un lien, pas un tableau"
        assert facture[champ]["url"].startswith("https://app.pennylane.com/api/external/v2/")


def test_l_enveloppe_de_page_a_exactement_trois_cles(client):
    corps = client.get(f"{BASE}/customers?limit=2", headers=H).json()
    assert set(corps) == {"items", "has_more", "next_cursor"}


def test_next_cursor_est_null_a_la_fin_pas_absent(client):
    """Un consommateur qui teste `if "next_cursor" in body` boucle à l'infini."""
    corps = client.get(f"{BASE}/journals?limit=100", headers=H).json()
    assert corps["has_more"] is False
    assert "next_cursor" in corps
    assert corps["next_cursor"] is None
