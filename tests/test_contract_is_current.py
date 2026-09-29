"""The committed contract must not drift from the application, and honesty is
a build constraint.

Two properties, and they hold together:

  1. `contracts/pennylane.openapi.yaml` IS what the application serves. This
     is the file a consumer copies into their own repo, pinned to an image
     version; if it lies, it lies for everyone downstream.
  2. Every field marked `unverified` is registered. Without this test, the
     marker would become decorative — it would get applied without ever
     writing down what it would take to remove the doubt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

import pennylane_mock as mock

RACINE = Path(__file__).resolve().parents[1]
CONTRAT = RACINE / "contracts" / "pennylane.openapi.yaml"
REGISTRE = RACINE / "docs" / "UNVERIFIED-FIELDS.md"


def test_committed_contract_is_current():
    genere = yaml.safe_load(
        yaml.safe_dump(mock.openapi_contract(), sort_keys=False, allow_unicode=True)
    )
    committe = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    assert committe == genere, (
        "The committed contract has drifted from the application. Run "
        "`make contract`, REVIEW the diff — a response shape that changes is "
        "a contract change for consumers, who keep a pinned copy of it — "
        "then commit."
    )


def test_contract_does_not_publish_the_mock_s_affordances():
    """`/__admin` and `/health` don't exist at Pennylane. Publishing them
    would pass off as provider API what isn't — and `/__admin` is only
    mounted conditionally, so the contract would depend on the environment
    it was generated in, which would make it uninterpretable."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    assert all(chemin.startswith("/api/external/v2/") for chemin in contrat["paths"])
    brut = CONTRAT.read_text(encoding="utf-8")
    assert "__admin" not in brut
    assert "/health" not in brut


def test_contract_advertises_the_provider_s_server():
    """A client generated from this contract must point at Pennylane, not at
    localhost: the mock substitutes itself via configuration, not via the
    contract."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    assert contrat["servers"] == [{"url": "https://app.pennylane.com"}]


def test_contract_covers_the_ninety_one_operations():
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    operations = [op for chemin in contrat["paths"].values() for op in chemin]
    assert len(operations) == 91
    assert set(operations) == {"get"}, "the surface is READ-only"


def test_contract_documents_pagination():
    """Without `cursor` in the contract, a consumer wouldn't know they must
    paginate — and a client generator wouldn't produce the parameter."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    liste = contrat["paths"]["/api/external/v2/customer_invoices"]["get"]
    noms = [parametre["name"] for parametre in liste["parameters"]]
    assert noms == ["cursor", "limit", "sort", "filter"]
    assert noms.count("cursor") == 1, "a parameter published twice breaks generators"

    reponse = liste["responses"]["200"]["content"]["application/json"]["schema"]
    reference = reponse["$ref"].rsplit("/", 1)[-1]
    schema = contrat["components"]["schemas"][reference]
    # The three cursor keys, always — that's the safe path, present on all
    # sixteen collections. The four offset keys are declared alongside
    # because four collections render them (see test_pagination.py); the
    # contract must document them, otherwise a client generator rejects them.
    assert {"items", "has_more", "next_cursor"} <= set(schema["properties"])


def test_contract_documents_the_failure_modes():
    """A contract that only described the happy path wouldn't tell the
    consumer which failures they need to handle."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    reponses = contrat["paths"]["/api/external/v2/customers"]["get"]["responses"]
    assert {"400", "401", "403", "404", "422", "429", "500", "503"} <= set(reponses)
    # The 429 is PLAIN TEXT, and the contract must say so.
    assert "text/plain" in reponses["429"]["content"]


def test_every_unverified_field_is_registered():
    """Honesty is a build constraint: a marker that can be applied without
    writing anything down becomes decorative within three commits."""
    assert REGISTRE.exists(), f"{REGISTRE} is required"
    registre = REGISTRE.read_text(encoding="utf-8")

    brut = json.dumps(mock.openapi_contract(), ensure_ascii=False)
    marques = re.findall(r'"x-pennylane-confidence":\s*"(unverified|invented)"', brut)
    assert marques, (
        "no field marked: either everything is attested (and it must be "
        "proven), or the marking was lost"
    )

    # Every resource carrying a marked field must appear in the registry.
    schemas = mock.openapi_contract().get("components", {}).get("schemas", {})
    for nom, schema in schemas.items():
        for champ, definition in (schema.get("properties") or {}).items():
            if "x-pennylane-confidence" not in json.dumps(definition, ensure_ascii=False):
                continue
            assert f"`{champ}`" in registre, (
                f"field `{champ}` of {nom} is marked unverified but is not "
                "registered in docs/UNVERIFIED-FIELDS.md — "
                "add a line there, with what it would take to remove the doubt"
            )


def test_registry_carries_its_front_matter():
    """The front matter says where the truth comes from and what should
    trigger a re-review. Without it, the file ages without anyone knowing how
    stale it is."""
    texte = REGISTRE.read_text(encoding="utf-8")
    assert texte.startswith("---\n")
    entete = yaml.safe_load(texte.split("---", 2)[1])
    assert entete["type"] == "reference"
    assert entete["sources_of_truth"] and entete["review_triggers"]
    assert entete["update_policy"] and entete["last_verified"]
