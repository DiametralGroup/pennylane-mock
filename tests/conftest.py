"""Harnais de tests.

Deux réglages d'environnement, posés AVANT l'import du paquet (la configuration
est lue à l'import) :

  • le plan de contrôle `/__admin` est monté — le montage est conditionnel ;
  • l'intervalle d'évolution passe à 3600 s : aucun événement ne se déclenche au
    fil de l'horloge murale pendant la suite, même sur une CI lente. Les tests
    d'évolution font défiler le temps EXPLICITEMENT via `/__admin/clock` ou
    `/__admin/evolve` — c'est ce qui les rend déterministes.
"""

from __future__ import annotations

import os

os.environ.setdefault("PENNYLANE_MOCK_ADMIN_ENABLED", "true")
os.environ.setdefault("PENNYLANE_MOCK_EVOLUTION_INTERVAL", "3600")

import pytest
from fastapi.testclient import TestClient

import pennylane_mock as mock

BASE = "/api/external/v2"

#: Le trousseau d'une requête bien formée. Un seul en-tête — c'est le régime
#: d'authentification le plus simple des cinq mocks de l'écosystème.
H = {"Authorization": "Bearer mock-pennylane-token"}
ADMIN = {"X-Mock-Admin-Token": "mock-admin-token"}


@pytest.fixture()
def client():
    """Un client sur un état REMIS À NEUF — avant ET après, pour qu'un test ne
    lègue ni règle d'injection, ni événement d'évolution, ni scope amputé au
    suivant."""
    c = TestClient(mock.app)
    mock.settings.reload()
    mock.state.reset()
    yield c
    mock.settings.reload()
    mock.state.reset()


@pytest.fixture()
def donnees(client):  # noqa: ARG001 — la fixture chaîne le reset
    """Le jeu de données, pour les tests qui l'inspectent directement."""
    return mock.state.dataset


def tout_paginer(client, chemin: str, limite: int = 7) -> list[dict]:
    """Le parcours de pagination complet — l'outil des tests de bout en bout.

    Il REJOUE le filtre à chaque page, comme la documentation l'exige, et
    s'arrête sur `has_more` faux et non sur une page courte : c'est la règle du
    dialecte, et un helper qui prendrait le raccourci masquerait justement le
    défaut qu'on cherche.
    """
    separateur = "&" if "?" in chemin else "?"
    elements: list[dict] = []
    curseur = None
    for _ in range(200):
        url = f"{chemin}{separateur}limit={limite}" + (f"&cursor={curseur}" if curseur else "")
        reponse = client.get(url, headers=H)
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        elements.extend(corps["items"])
        if not corps["has_more"]:
            return elements
        curseur = corps["next_cursor"]
    raise AssertionError(f"pagination non terminée après 200 pages sur {chemin}")
