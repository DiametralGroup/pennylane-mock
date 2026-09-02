"""Le contrat committé ne doit pas dériver de l'application, et l'honnêteté est
une contrainte de build.

Deux propriétés, et elles se tiennent :

  1. `contracts/pennylane.openapi.yaml` EST ce que l'application sert. C'est ce
     fichier que le consommateur copie chez lui, épinglé à une version de
     l'image ; s'il ment, il ment pour tout le monde en aval.
  2. Tout champ marqué `unverified` est inscrit au registre. Sans ce test, le
     marqueur deviendrait décoratif — on le poserait sans jamais écrire ce
     qu'il faudrait faire pour lever le doute.
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


def test_le_contrat_committe_est_a_jour():
    genere = yaml.safe_load(
        yaml.safe_dump(mock.contrat_openapi(), sort_keys=False, allow_unicode=True)
    )
    committe = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    assert committe == genere, (
        "Le contrat committé a dérivé de l'application. Lancer `make contract`, "
        "RELIRE le diff — une forme de réponse qui change est un changement de "
        "contrat pour les consommateurs, qui en gardent une copie épinglée — "
        "puis committer."
    )


def test_le_contrat_ne_publie_pas_les_affordances_du_mock():
    """`/__admin` et `/health` n'existent pas chez Pennylane. Les publier ferait
    passer pour de l'API fournisseur ce qui n'en est pas — et `/__admin` n'est
    monté que conditionnellement, donc le contrat dépendrait de l'environnement
    de génération, ce qui le rendrait ininterprétable."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    assert all(chemin.startswith("/api/external/v2/") for chemin in contrat["paths"])
    brut = CONTRAT.read_text(encoding="utf-8")
    assert "__admin" not in brut
    assert "/health" not in brut


def test_le_contrat_annonce_le_serveur_du_fournisseur():
    """Un client généré depuis ce contrat doit pointer vers Pennylane, pas vers
    localhost : le mock se substitue par la configuration, pas par le contrat."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    assert contrat["servers"] == [{"url": "https://app.pennylane.com"}]


def test_le_contrat_couvre_les_quatre_vingt_onze_operations():
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    operations = [op for chemin in contrat["paths"].values() for op in chemin]
    assert len(operations) == 91
    assert set(operations) == {"get"}, "la surface est en LECTURE seule"


def test_le_contrat_documente_la_pagination():
    """Sans `cursor` dans le contrat, un consommateur ne saurait pas qu'il doit
    paginer — et un générateur de client ne produirait pas le paramètre."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    liste = contrat["paths"]["/api/external/v2/customer_invoices"]["get"]
    noms = [parametre["name"] for parametre in liste["parameters"]]
    assert noms == ["cursor", "limit", "sort", "filter"]
    assert noms.count("cursor") == 1, "un paramètre publié deux fois casse les générateurs"

    reponse = liste["responses"]["200"]["content"]["application/json"]["schema"]
    reference = reponse["$ref"].rsplit("/", 1)[-1]
    schema = contrat["components"]["schemas"][reference]
    assert set(schema["properties"]) == {"items", "has_more", "next_cursor"}


def test_le_contrat_documente_les_modes_de_panne():
    """Un contrat qui ne décrirait que le chemin heureux ne dirait pas au
    consommateur quelles pannes il doit savoir traiter."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    reponses = contrat["paths"]["/api/external/v2/customers"]["get"]["responses"]
    assert {"400", "401", "403", "404", "422", "429", "500", "503"} <= set(reponses)
    # Le 429 est en TEXTE BRUT, et le contrat doit le dire.
    assert "text/plain" in reponses["429"]["content"]


def test_tout_champ_non_verifie_est_inscrit_au_registre():
    """L'honnêteté est une contrainte de build : un marqueur qu'on peut poser
    sans rien écrire devient décoratif en trois commits."""
    assert REGISTRE.exists(), f"{REGISTRE} est obligatoire"
    registre = REGISTRE.read_text(encoding="utf-8")

    brut = json.dumps(mock.contrat_openapi(), ensure_ascii=False)
    marques = re.findall(r'"x-pennylane-confidence":\s*"(unverified|invented)"', brut)
    assert marques, (
        "aucun champ marqué : soit tout est attesté (et il faut le prouver), "
        "soit le marquage a été perdu"
    )

    # Chaque ressource porteuse d'un champ marqué doit apparaître au registre.
    schemas = mock.contrat_openapi().get("components", {}).get("schemas", {})
    for nom, schema in schemas.items():
        for champ, definition in (schema.get("properties") or {}).items():
            if "x-pennylane-confidence" not in json.dumps(definition, ensure_ascii=False):
                continue
            assert f"`{champ}`" in registre, (
                f"le champ `{champ}` de {nom} est marqué non vérifié mais "
                f"n'est pas inscrit dans docs/UNVERIFIED-FIELDS.md — "
                "y ajouter une ligne, avec ce qu'il faudrait faire pour lever le doute"
            )


def test_le_registre_porte_son_front_matter():
    """Le front-matter dit d'où vient la vérité et ce qui doit déclencher une
    relecture. Sans lui, le fichier vieillit sans qu'on sache de quand il date."""
    texte = REGISTRE.read_text(encoding="utf-8")
    assert texte.startswith("---\n")
    entete = yaml.safe_load(texte.split("---", 2)[1])
    assert entete["type"] == "reference"
    assert entete["sources_of_truth"] and entete["review_triggers"]
    assert entete["update_policy"] and entete["last_verified"]
