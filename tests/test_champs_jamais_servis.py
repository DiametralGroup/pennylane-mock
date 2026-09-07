"""Ce que le fournisseur ne sert JAMAIS, et le code de taux qui n'est pas un nombre.

┌─ CES TESTS SONT ÉCRITS À L'ENVERS DES AUTRES ───────────────────────────────┐
│ Ils n'affirment pas qu'une valeur EST là. Ils affirment qu'elle N'EST PAS —  │
│ parce que le défaut qu'ils ferment est un mock plus GÉNÉREUX et plus         │
│ RÉGULIER que le fournisseur, et qu'un mock trop généreux ne se voit dans     │
│ aucun test qui vérifie une présence.                                         │
│                                                                              │
│ Relevé sur un locataire réel le 2026-09-07 :                                 │
│   • `analytical_code` nul sur 159 catégories sur 159 ;                       │
│   • aucun `product` sur les lignes de 1 898 factures client ;                │
│   • aucun `ledger_account` sur celles de 4 559 factures fournisseur ;        │
│   • `vat_rate` d'un compte : `any` (2 633), `FR_200` (166), `exempt` (141),  │
│     `extracom` (51), `crossborder` (40), `FR_100`, `FR_55`, `FR_15_385`.     │
│                                                                              │
│ Le coût de l'ancien comportement était réel : un consommateur qui infère son │
│ schéma depuis les données ne matérialisait PAS ces colonnes en production —  │
│ échec en « column does not exist » — et castait `vat_rate` en numérique,     │
│ ce qui tombait sur le premier `any`.                                         │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import pytest
from conftest import ADMIN, BASE, H

import pennylane_mock as mock

#: Les valeurs de `vat_rate` qui ne sont PAS des codes pays : elles sont ce qui
#: fait tomber un `cast(... as numeric)`, et le mock doit en servir.
NON_NUMERIQUES = {"any", "exempt", "extracom", "crossborder"}


def _tous(client, chemin: str) -> list[dict]:
    """Toutes les pages d'une collection, quelle que soit sa pagination.

    ⚠️ L'enveloppe N'EST PAS UNIFORME, et c'est délibéré côté mock : certaines
    collections paginent par `page`, d'autres par `next_cursor`. Un helper qui
    ne connaîtrait que `page` boucle indéfiniment sur les secondes — le curseur
    étant ignoré, chaque tour reçoit la même première page avec `has_more`
    toujours vrai. C'est arrivé en écrivant ce fichier, sur `/ledger_entries`.
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


# ── `vat_rate` est un code ───────────────────────────────────────────────────


def test_le_taux_de_tva_d_un_compte_n_est_jamais_un_nombre(client):
    """Aucun `vat_rate` ne doit se laisser lire comme un décimal.

    C'est la formulation qui compte : « ressemble à un nombre » plutôt que
    « vaut 0.0 ». Elle ferme aussi les variantes (« 20 », « 0,0 ») que
    personne n'a encore écrites.
    """
    comptes = _tous(client, "/ledger_accounts")
    assert comptes, "le plan comptable est vide — le test ne prouverait rien"
    for compte in comptes:
        with pytest.raises(ValueError):
            float(compte["vat_rate"].replace(",", "."))


def test_le_plan_comptable_sert_les_deux_familles_de_code(client):
    """Des codes pays ET des codes non numériques, sur le même plan.

    Ne servir que `any` rendrait le mock sévère mais faux dans l'autre sens :
    un consommateur ne verrait jamais un `FR_200`, qui est bien la forme des
    comptes soumis à un taux.
    """
    codes = {c["vat_rate"] for c in _tous(client, "/ledger_accounts")}
    assert codes & NON_NUMERIQUES, f"aucun code non numérique servi : {sorted(codes)}"
    assert {c for c in codes if c.startswith("FR_")}, f"aucun code pays servi : {sorted(codes)}"


def test_any_est_la_valeur_la_plus_repandue(client):
    """Comme chez le fournisseur : 2 633 comptes sur ~3 100.

    Un `any` marginal se ferait contourner par un consommateur qui filtrerait
    « les quelques comptes bizarres ». Il est majoritaire, donc incontournable.
    """
    codes = [c["vat_rate"] for c in _tous(client, "/ledger_accounts")]
    assert codes.count("any") > len(codes) / 2


# ── Les champs facultatifs jamais renseignés ─────────────────────────────────


def test_aucune_categorie_ne_porte_de_code_analytique(client):
    categories = _tous(client, "/categories")
    assert categories, "aucune catégorie — le test ne prouverait rien"
    assert all(c["analytical_code"] is None for c in categories)


def test_la_cle_du_code_analytique_est_PRESENTE_mais_nulle(client):
    """`null`, pas absent.

    L'OpenAPI déclare le champ et le fournisseur le rend. Un mock qui
    OMETTRAIT la clé serait divergent dans l'autre sens et cacherait au
    consommateur qu'elle existe.
    """
    categories = _tous(client, "/categories")
    assert all("analytical_code" in c for c in categories)


def test_aucune_ventilation_ne_porte_de_code_analytique(client):
    """La ventilation copie sa catégorie : elle doit être gommée avec elle.

    Sans ce test, le mock se contredirait — catégorie sans code, ventilation
    qui la vise AVEC — et un consommateur qui lit la seconde resterait vert.
    """
    vues = 0
    for ecriture in _tous(client, "/ledger_entries"):
        for ventilation in ecriture.get("categories") or []:
            vues += 1
            assert ventilation["analytical_code"] is None
    assert vues, "aucune écriture ventilée — le test ne prouverait rien"


def test_aucune_ligne_de_facture_client_ne_porte_de_produit(client):
    factures = _tous(client, "/customer_invoices")[:10]
    lignes = [
        ligne
        for facture in factures
        for ligne in _tous(client, f"/customer_invoices/{facture['id']}/invoice_lines")
    ]
    assert lignes, "aucune ligne de vente — le test ne prouverait rien"
    assert all(ligne["product"] is None for ligne in lignes)


def test_aucune_ligne_de_facture_fournisseur_ne_porte_de_compte(client):
    factures = _tous(client, "/supplier_invoices")[:10]
    lignes = [
        ligne
        for facture in factures
        for ligne in _tous(client, f"/supplier_invoices/{facture['id']}/invoice_lines")
    ]
    assert lignes, "aucune ligne d'achat — le test ne prouverait rien"
    assert all(ligne["ledger_account"] is None for ligne in lignes)


# ── Le levier, et le fait qu'il soit bien un levier ──────────────────────────


def test_le_drapeau_restaure_la_forme_riche(client, monkeypatch):
    """`PENNYLANE_MOCK_OPTIONAL_FIELDS=1` rend les trois champs.

    La forme riche reste LÉGITIME — un autre locataire peut renseigner ces
    champs. Ce qui change, c'est le défaut : la forme sévère.
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


def test_l_evolution_suit_le_meme_regime(client):
    """Une facture CRÉÉE en cours de route ne doit pas rouvrir la divergence.

    Le gommage est fait sur le jeu construit ; l'évolution, elle, fabrique de
    nouvelles lignes après coup. Sans le même garde-fou là-bas, la première
    facture émise par l'horloge reservirait un `product` — et la divergence
    reviendrait par la porte de derrière, sur les seules lignes RÉCENTES,
    c'est-à-dire précisément celles qu'une extraction incrémentale lit.

    `nouvelle_facture_client` est le troisième événement du cycle : trois pas
    suffisent, et le test ÉCHOUE si aucune facture n'est apparue — se contenter
    d'un `skip` en rendrait la portée nulle le jour où le cycle changerait.
    """
    avant = {f["id"] for f in _tous(client, "/customer_invoices")}
    r = client.post("/__admin/evolve", headers=ADMIN, json={"pas": 3})
    assert r.status_code == 200, r.text

    nouvelles = {f["id"] for f in _tous(client, "/customer_invoices")} - avant
    assert nouvelles, "l'évolution n'a émis aucune facture — le test ne prouverait rien"
    for ident in nouvelles:
        lignes = _tous(client, f"/customer_invoices/{ident}/invoice_lines")
        assert lignes, f"facture {ident} sans ligne"
        assert all(ligne["product"] is None for ligne in lignes)
