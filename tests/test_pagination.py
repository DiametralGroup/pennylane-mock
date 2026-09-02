"""Le curseur — le cinquième dialecte de pagination de l'écosystème insights360.

BoondManager pagine par `page`/`maxResults`, Graph par `@odata.nextLink`,
LinkedIn par `start`/`count`, GA4 par `limit`/`offset`. Un connecteur qui
aurait « une » boucle générique se casse ici, et c'est le but.
"""

from __future__ import annotations

import pytest
from conftest import BASE, H, tout_paginer


def test_le_parcours_complet_ne_perd_ni_ne_double_aucune_ligne(client):
    tout = tout_paginer(client, f"{BASE}/customer_invoices", limite=7)
    identifiants = [element["id"] for element in tout]
    assert len(identifiants) == len(set(identifiants)), "doublons entre deux pages"
    reference = client.get(f"{BASE}/customer_invoices?limit=100", headers=H).json()
    assert reference["has_more"] is False
    assert sorted(identifiants) == sorted(e["id"] for e in reference["items"])


def test_la_derniere_page_annonce_has_more_faux_et_next_cursor_nul(client):
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
def test_limit_hors_bornes_rend_400_et_n_est_pas_rabote(client, valeur):
    """Un plafond silencieux fait croire à un pipeline qu'il a demandé 5000
    lignes et tout reçu, alors qu'il en a lu 100. C'est le défaut le plus
    coûteux d'une pagination, parce qu'il ne se voit nulle part."""
    reponse = client.get(f"{BASE}/customers?limit={valeur}", headers=H)
    assert reponse.status_code == 400
    assert reponse.json()["status"] == 400
    assert "1 and 100" in reponse.json()["error"]


def test_le_plafond_des_changelogs_est_plus_haut(client):
    """1000 sur les changelogs, 100 sur les listes ordinaires — l'OpenAPI le
    déclare bien ainsi, endpoint par endpoint."""
    assert client.get(f"{BASE}/changelogs/customers?limit=1000", headers=H).status_code == 200
    assert client.get(f"{BASE}/changelogs/customers?limit=1001", headers=H).status_code == 400
    assert client.get(f"{BASE}/customers?limit=1000", headers=H).status_code == 400


def test_la_limite_par_defaut_est_vingt(client):
    corps = client.get(f"{BASE}/customer_invoices", headers=H).json()
    assert len(corps["items"]) == 20


@pytest.mark.parametrize("curseur", ["%%%", "pas-du-base64!", "eyJ0cnVuY2F0", "bnVsbA"])
def test_un_curseur_illisible_rend_400(client, curseur):
    reponse = client.get(f"{BASE}/customers?cursor={curseur}", headers=H)
    assert reponse.status_code == 400
    assert reponse.json()["status"] == 400


def test_le_tri_par_defaut_est_decroissant(client):
    """`-id` par défaut : c'est l'inverse de l'intuition, et c'est ce que
    l'OpenAPI déclare. Un consommateur qui suppose l'ordre croissant pose son
    point de reprise sur le plus RÉCENT et ne revoit plus rien."""
    par_defaut = client.get(f"{BASE}/customer_invoices?limit=5", headers=H).json()["items"]
    explicite = client.get(f"{BASE}/customer_invoices?limit=5&sort=id", headers=H).json()["items"]
    decroissant = [e["id"] for e in par_defaut]
    croissant = [e["id"] for e in explicite]
    assert decroissant == sorted(decroissant, reverse=True)
    assert croissant == sorted(croissant)
    assert decroissant != croissant


def test_le_tri_sur_un_autre_champ(client):
    items = client.get(f"{BASE}/customer_invoices?limit=10&sort=date", headers=H).json()["items"]
    dates = [e["date"] for e in items]
    assert dates == sorted(dates)


def test_le_curseur_n_encode_pas_les_filtres(client):
    """LE piège du dialecte, documenté noir sur blanc par le fournisseur :
    « Omitting the filters on page 2+ will return unfiltered results from the
    cursor position. » Pas de 400, pas d'avertissement — des lignes en trop,
    silencieusement. Un pipeline qui oublie de rejouer son `filter` charge des
    lignes qu'il croyait avoir exclues."""
    # Un filtre dont les lignes retenues sont ESPACÉES : c'est la seule façon
    # de rendre le piège visible — avec un filtre qui sélectionne un bloc
    # contigu, la page 2 non filtrée tomberait par hasard sur des lignes
    # conformes, et le test passerait sans rien prouver.
    retenus = [1, 5, 9, 13]
    filtre = '[{"field":"id","operator":"in","value":[1,5,9,13]}]'
    page1 = client.get(
        f"{BASE}/customer_invoices?limit=2&sort=id&filter={filtre}", headers=H
    ).json()
    assert [e["id"] for e in page1["items"]] == [1, 5]
    assert page1["has_more"]

    # Page 2 SANS rejouer le filtre : le fournisseur ne proteste pas.
    sans = client.get(
        f"{BASE}/customer_invoices?limit=2&sort=id&cursor={page1['next_cursor']}", headers=H
    )
    assert sans.status_code == 200
    assert any(e["id"] not in retenus for e in sans.json()["items"]), (
        "le mock doit REPRODUIRE le piège : sans le filtre, la page 2 rend des "
        "lignes non filtrées, sans erreur"
    )

    # Page 2 AVEC le filtre rejoué : le comportement correct.
    avec = client.get(
        f"{BASE}/customer_invoices?limit=2&sort=id&filter={filtre}&cursor={page1['next_cursor']}",
        headers=H,
    ).json()
    assert [e["id"] for e in avec["items"]] == [9, 13]


def test_les_filtres_se_cumulent_en_et(client):
    filtre = (
        '[{"field":"paid","operator":"eq","value":true},'
        '{"field":"date","operator":"gteq","value":"2026-04-01"}]'
    )
    items = client.get(f"{BASE}/customer_invoices?limit=100&filter={filtre}", headers=H).json()[
        "items"
    ]
    assert items
    assert all(e["paid"] and e["date"] >= "2026-04-01" for e in items)


def test_l_operateur_in_prend_un_tableau(client):
    """C'est le pattern que le fournisseur recommande pour recharger les
    ressources d'un lot de changements : `id in [...]`."""
    filtre = '[{"field":"id","operator":"in","value":[1,2,3]}]'
    items = client.get(f"{BASE}/customer_invoices?filter={filtre}", headers=H).json()["items"]
    assert sorted(e["id"] for e in items) == [1, 2, 3]


def test_start_with_est_insensible_a_la_casse(client):
    filtre = '[{"field":"number","operator":"start_with","value":"411"}]'
    items = client.get(f"{BASE}/ledger_accounts?limit=100&filter={filtre}", headers=H).json()[
        "items"
    ]
    assert items and all(e["number"].startswith("411") for e in items)


def test_un_operateur_inconnu_rend_400(client):
    """La liste des neuf est courte et stable : un opérateur inventé côté
    consommateur est un vrai bug, et il doit se voir."""
    filtre = '[{"field":"id","operator":"like","value":1}]'
    reponse = client.get(f"{BASE}/customer_invoices?filter={filtre}", headers=H)
    assert reponse.status_code == 400
    assert "like" in reponse.json()["error"]


@pytest.mark.parametrize("brut", ["pas-du-json", '{"field":"id"}', '[{"field":"id"}]'])
def test_un_filtre_mal_forme_rend_400(client, brut):
    assert client.get(f"{BASE}/customers?filter={brut}", headers=H).status_code == 400
