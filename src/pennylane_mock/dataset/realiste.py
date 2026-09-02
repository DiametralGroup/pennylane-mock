"""Le jeu de données : la comptabilité de « Boréal Conseil » dans Pennylane.

Même monde que les quatre autres mocks de l'écosystème insights360 — ESN
française de 34 personnes, trois agences, domaine `boreal-conseil.example`,
ancre au 15 juillet 2026, graine 42 — vu cette fois par le SYSTÈME COMPTABLE.
Les mêmes flux que ceux de `boondmanager-mock` y apparaissent sous leur forme
Pennylane : sociétés clientes → factures de vente → encaissements bancaires,
achats → factures fournisseurs → décaissements, et l'écriture double qui les
enregistre tous.

┌─ CE QUI EST PARTAGÉ AVEC boondmanager-mock, ET CE QUI NE L'EST PAS ─────────┐
│ PARTAGÉ (dupliqué ici, sans dépendance de paquet — aucun des cinq mocks ne  │
│ dépend d'un autre, cf. README) :                                            │
│   • les raisons sociales des dix clients et des trois fournisseurs ;        │
│   • l'ancre temporelle (15/07/2026), la graine (42), le taux de TVA (20 %) ;│
│   • le format des références de vente `FAC-2026-NNNN` / `AV-2026-NNNN`.     │
│                                                                             │
│ PAS PARTAGÉ, et il faut le dire clairement : les MONTANTS. Les reproduire   │
│ à l'euro près demanderait de rejouer ici la matrice jours x TJM des         │
│ missions BoondManager — ~1500 lignes de logique métier dupliquée, qui       │
│ divergeraient au premier changement d'un des deux dépôts. Les montants de   │
│ ce mock sont donc les SIENS, tirés de la même graine et du même ordre de    │
│ grandeur. Un test aval qui compare des CA entre les deux mocks compare      │
│ donc des ENSEMBLES DE CLIENTS et des séries de références, pas des euros.   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ L'INVARIANT CENTRAL : LA BALANCE ÉQUILIBRE ────────────────────────────────┐
│ Tout est construit à partir des ÉCRITURES. Une facture de vente n'est pas   │
│ un montant posé à côté d'une écriture plausible : l'écriture EST la source, │
│ et la facture en dérive. Les montants sont manipulés en CENTIMES ENTIERS —  │
│ jamais en flottants — puis formatés en chaîne à deux décimales, qui est le  │
│ dialecte v2. C'est ce qui garantit que `sum(debit) == sum(credit)` à        │
│ l'octet près, et `tests/test_coherence.py` le vérifie.                      │
└─────────────────────────────────────────────────────────────────────────────┘

Déterminisme : `random.Random(seed)` et une ancre temporelle FIXE. Jamais
`datetime.now()` — deux exécutions produisent le même jeu de données au même
octet, c'est ce qui rend les tests aval reproductibles.
"""

from __future__ import annotations

import calendar
import random
from datetime import date, timedelta
from typing import Any

# ── Ancre temporelle ─────────────────────────────────────────────────────────

#: Identique à celle de boondmanager-mock, linkedin-mock et ga-mock.
AUJOURDHUI = date(2026, 7, 15)
#: Plafond de tous les `updated_at` du jeu de BASE. Les événements d'évolution
#: sont STRICTEMENT postérieurs : un curseur incrémental posé ici doit rendre
#: zéro ligne tant que le monde n'a pas bougé.
DERNIERE_MAJ = date(2026, 7, 12)
#: Premier mois facturé de l'exercice courant.
DEBUT_FACTURATION = date(2026, 1, 1)

TVA = 20  # en pourcentage entier — les centimes se calculent sans flottant
TAUX_TVA = "FR_200"
DEVISE = "EUR"
PAYS = "FR"

BASE_URL = "https://app.pennylane.com/api/external/v2"


# ── Utilitaires de forme ─────────────────────────────────────────────────────


def _d(jour: date) -> str:
    return f"{jour:%Y-%m-%d}"


def _dt(jour: date, h: int = 9, mn: int = 0, s: int = 0, micro: int = 0) -> str:
    """`2026-08-30T10:08:08.146343Z` — le format du fournisseur.

    UTC avec un `Z` final et SIX chiffres de microsecondes, jamais un décalage
    `+02:00` : c'est ce que montrent tous les exemples de l'OpenAPI. Un
    consommateur qui parse en `datetime.fromisoformat` avant Python 3.11 casse
    sur le `Z` — raison de plus pour ne pas « simplifier » en `+00:00`.
    """
    return f"{jour:%Y-%m-%d}T{h:02d}:{mn:02d}:{s:02d}.{micro:06d}Z"


def _maj(rng: random.Random, apres: date) -> str:
    """Un `updated_at` plausible : postérieur à la création, jamais après l'ancre."""
    if apres >= DERNIERE_MAJ:
        jour = DERNIERE_MAJ
    else:
        jour = apres + timedelta(days=rng.randint(0, (DERNIERE_MAJ - apres).days))
    return _dt(
        jour, rng.randint(8, 18), rng.randint(0, 59), rng.randint(0, 59), rng.randrange(10**6)
    )


def _euros(centimes: int) -> str:
    """Centimes entiers → la chaîne du dialecte v2.

    Les montants de l'API v2 sont des CHAÎNES (`"230.32"`), jamais des nombres.
    Le guide d'erreurs va jusqu'à lister « amounts not sent as strings » comme
    cause typique de 400. Un connecteur qui reçoit un flottant ici et le
    tolère se cassera en production, quand la vraie API lui enverra la chaîne.
    """
    signe = "-" if centimes < 0 else ""
    a = abs(centimes)
    return f"{signe}{a // 100}.{a % 100:02d}"


def _fin_mois(annee: int, mois: int) -> date:
    return date(annee, mois, calendar.monthrange(annee, mois)[1])


def _jours_ouvres(annee: int, mois: int) -> int:
    """Jours ouvrés du mois (hors week-ends ; les fériés sont ignorés — mock)."""
    dernier = calendar.monthrange(annee, mois)[1]
    return sum(1 for j in range(1, dernier + 1) if date(annee, mois, j).weekday() < 5)


def _lien(chemin: str) -> dict[str, str]:
    """Une collection imbriquée est un LIEN, pas un tableau.

    C'est la différence de forme la plus structurante entre v1 et v2 (guide de
    migration) : `{"invoice_lines": {"url": "…"}}` et non `{"invoice_lines":
    [...]}`. Un consommateur qui itère dessus doit faire un second appel, et
    c'est précisément le comportement qu'un mock doit lui imposer.
    """
    return {"url": f"{BASE_URL}{chemin}"}


def _ref(ident: int, chemin: str) -> dict[str, Any]:
    return {"id": ident, "url": f"{BASE_URL}{chemin}/{ident}"}


# ── Catalogues — les mêmes raisons sociales que boondmanager-mock ────────────

#: (raison sociale, ville, code postal, adresse, secteur, prospect ?)
_CLIENTS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    ("Lumina Retail", "Paris", "75009", "14 rue de Châteaudun", "Retail & Distribution", False),
    ("Banque Hexagone", "Paris", "75002", "3 place de la Bourse", "Banque & Assurance", False),
    ("Voltalis Énergie", "Lyon", "69003", "88 rue Garibaldi", "Énergie & Utilities", False),
    ("Mutuelle Armor", "Nantes", "44000", "12 quai de la Fosse", "Banque & Assurance", False),
    ("TransEuropa Fret", "Lille", "59000", "45 avenue du Peuple Belge", "Transport", False),
    ("Pharmadis", "Lyon", "69007", "27 avenue Jean Jaurès", "Santé & Pharma", False),
    ("Citymob", "Bordeaux", "33000", "9 cours de l'Intendance", "Transport", False),
    ("Assurial", "Bruxelles", "1000", "60 rue Royale", "Banque & Assurance", False),
    ("Groupe Ardentes", "Nantes", "44200", "5 boulevard Vincent Gâche", "Industrie", False),
    # Prospect chez BoondManager : il existe en tant que client Pennylane
    # (une fiche a été créée) mais ne porte AUCUNE facture. C'est un cas
    # limite délibéré — un client à zéro euro doit apparaître dans les listes.
    ("MediaQuartz", "Paris", "75011", "22 rue Oberkampf", "Télécoms & Médias", True),
)

#: (raison sociale, ville, code postal, adresse, compte de charge, libellé d'achat)
_FOURNISSEURS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("Fivetech Partners", "Paris", "75008", "31 rue de Ponthieu", "604000", "Sous-traitance"),
    ("Softalliance", "Paris", "75010", "8 rue des Petites Écuries", "651600", "Licences"),
    ("Foncière Beaumont", "Paris", "75017", "40 rue de Courcelles", "613200", "Loyer"),
    ("Nordnet Télécom", "Lille", "59200", "2 avenue de la Marne", "626000", "Télécoms"),
    ("Bureau & Cie", "Nantes", "44100", "17 rue de la Convention", "606300", "Fournitures"),
)

#: Deux clients PARTICULIERS — la surface `customers` est un `oneOf` entre
#: personne morale et personne physique, et un connecteur qui ne traite que la
#: première casse sur la seconde. Ils achètent de la formation (compte 706100).
_PARTICULIERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("Camille", "Rousset", "Paris", "75012", "8 rue Crozatier"),
    ("Yanis", "Belkacem", "Lyon", "69006", "3 rue Duquesne"),
)

#: Le plan comptable servi : (numéro, libellé, type, lettrable, taux de TVA).
#: Réduit à ce que la vie de l'entreprise met réellement en mouvement — un
#: PCG complet ferait 400 comptes dont 390 à zéro, ce qui n'éprouve rien.
_PLAN: tuple[tuple[str, str, str, bool, str], ...] = (
    ("101000", "Capital social", "equity", False, "0.0"),
    ("110000", "Report à nouveau", "equity", False, "0.0"),
    ("401000", "Fournisseurs", "supplier", True, "0.0"),
    ("411000", "Clients", "customer", True, "0.0"),
    ("445660", "TVA déductible sur autres biens et services", "tax", False, "20.0"),
    ("471000", "Compte d'attente", "suspense", True, "0.0"),
    ("445710", "TVA collectée", "tax", False, "20.0"),
    ("512000", "Banque — compte courant", "bank", True, "0.0"),
    ("512100", "Banque — compte de réserve", "bank", True, "0.0"),
    ("604000", "Achats d'études et prestations de services", "expense", False, "20.0"),
    ("606300", "Fournitures d'entretien et petit équipement", "expense", False, "20.0"),
    ("613200", "Locations immobilières", "expense", False, "20.0"),
    ("626000", "Frais postaux et de télécommunications", "expense", False, "20.0"),
    ("627000", "Services bancaires et assimilés", "expense", False, "0.0"),
    ("641100", "Salaires et appointements", "expense", False, "0.0"),
    ("645000", "Charges de sécurité sociale et de prévoyance", "expense", False, "0.0"),
    ("651600", "Droits d'auteur et de reproduction", "expense", False, "20.0"),
    ("706000", "Prestations de services", "income", False, "20.0"),
    ("706100", "Formations", "income", False, "20.0"),
)

#: (code, libellé) — les journaux d'une petite ESN.
_JOURNAUX: tuple[tuple[str, str, str], ...] = (
    ("VE", "Journal des ventes", "sale"),
    ("AC", "Journal des achats", "purchase"),
    ("BQ", "Journal de banque", "bank"),
    ("OD", "Opérations diverses", "miscellaneous"),
    ("AN", "À-nouveaux", "new_year"),
    ("SA", "Journal de paie", "payroll"),
)

#: Les axes analytiques : l'agence et le pôle, exactement les deux axes de
#: cloisonnement d'insights360. Un groupe de catégories par axe.
_AXES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Agence", (("Paris", "AG-PAR"), ("Lyon", "AG-LYO"), ("Nantes", "AG-NAN"))),
    (
        "Pôle",
        (
            ("Data & Analytics", "PO-DATA"),
            ("Cloud & Platform", "PO-CLOUD"),
            ("Cybersécurité", "PO-CYBER"),
        ),
    ),
)

#: (libellé de la prestation, TJM en centimes)
_PRESTATIONS: tuple[tuple[str, int], ...] = (
    ("Ingénierie data — consultant confirmé", 68_000),
    ("Ingénierie data — consultant senior", 78_000),
    ("Architecture cloud — senior", 82_000),
    ("Expertise MLOps", 85_000),
    ("Pilotage de programme", 92_000),
    ("Audit sécurité", 88_000),
    ("Formation Power BI (jour)", 145_000),
)

#: Établissements bancaires — deux comptes, comme chez BoondManager.
_BANQUES: tuple[tuple[str, str, str], ...] = (
    ("Banque Hexagone Entreprises", "Compte courant", "512000"),
    ("Banque Hexagone Entreprises", "Compte de réserve", "512100"),
)


def _aux(prefixe: str, nom: str) -> str:
    """Le numéro d'un compte auxiliaire : `411LUMIN`, `401FIVET`.

    Forme française classique — racine générale + cinq lettres du tiers. Le
    fournisseur ne documente PAS de règle de composition (chaque cabinet a la
    sienne) ; celle-ci est plausible, pas attestée. Cf. docs/UNVERIFIED-FIELDS.md.
    """
    lettres = "".join(c for c in nom.upper() if c.isalpha())[:5].ljust(5, "X")
    return f"{prefixe}{lettres}"


# ═════════════════════════════════════════════════════════════════════════════
#  Référentiels — journaux, plan comptable, exercices, axes analytiques
# ═════════════════════════════════════════════════════════════════════════════


def _journaux() -> list[dict[str, Any]]:
    """Six journaux. `type` n'a PAS d'énumération dans l'OpenAPI officiel : les
    valeurs ci-dessous sont plausibles (nomenclature française usuelle), pas
    attestées — cf. docs/UNVERIFIED-FIELDS.md."""
    return [
        {"id": i, "code": code, "label": libelle, "type": type_}
        for i, (code, libelle, type_) in enumerate(_JOURNAUX, start=1)
    ]


def _comptes(rng: random.Random) -> list[dict[str, Any]]:
    """Le plan général, puis un auxiliaire par client et par fournisseur.

    Les auxiliaires portent le MÊME `type` que leur racine (`customer`,
    `supplier`) : c'est ce qui permet à un consommateur de les regrouper sans
    connaître la règle de numérotation française.
    """
    comptes: list[dict[str, Any]] = []
    ident = 1
    cree = _dt(date(2024, 1, 2), 8, 0, 0, 0)
    for numero, libelle, type_, lettrable, tva in _PLAN:
        comptes.append(
            {
                "id": ident,
                "number": numero,
                "label": libelle,
                "vat_rate": tva,
                "country_alpha2": PAYS,
                "enabled": True,
                "type": type_,
                "letterable": lettrable,
                "created_at": cree,
                "updated_at": cree,
            }
        )
        ident += 1

    for nom, *_ in _CLIENTS:
        comptes.append(_compte_auxiliaire(ident, _aux("411", nom), f"Client — {nom}", "customer"))
        ident += 1
    for prenom, nom, *_ in _PARTICULIERS:
        comptes.append(
            _compte_auxiliaire(
                ident, _aux("411", f"{nom}{prenom}"), f"Client — {prenom} {nom}", "customer"
            )
        )
        ident += 1
    for nom, *_ in _FOURNISSEURS:
        comptes.append(
            _compte_auxiliaire(ident, _aux("401", nom), f"Fournisseur — {nom}", "supplier")
        )
        ident += 1
    del rng  # le plan comptable n'a aucune part d'aléa : il est décidé, pas tiré
    return comptes


def _compte_auxiliaire(ident: int, numero: str, libelle: str, type_: str) -> dict[str, Any]:
    return {
        "id": ident,
        "number": numero,
        "label": libelle,
        "vat_rate": "0.0",
        "country_alpha2": PAYS,
        "enabled": True,
        "type": type_,
        "letterable": True,
        "created_at": _dt(date(2024, 1, 2), 8, 0, 0, 0),
        "updated_at": _dt(date(2024, 1, 2), 8, 0, 0, 0),
    }


def _exercices() -> list[dict[str, Any]]:
    """Trois exercices : deux clos, celui de l'ancre ouvert.

    Un exercice CLOS et un exercice OUVERT dans le même jeu : c'est ce qui
    permet d'éprouver un consommateur qui extrait « l'exercice courant » sans
    dire lequel — il doit choisir, et le choix doit se voir.
    """
    exercices = []
    for i, annee in enumerate((2024, 2025, 2026), start=1):
        statut = "closed" if annee < 2026 else "open"
        exercices.append(
            {
                "id": i,
                "start": _d(date(annee, 1, 1)),
                "finish": _d(date(annee, 12, 31)),
                "status": statut,
                "created_at": _dt(date(annee, 1, 1), 0, 5, 0, 0),
                "updated_at": _dt(
                    date(annee, 1, 1) if statut == "open" else date(annee + 1, 4, 30)
                ),
            }
        )
    return exercices


def _axes_analytiques() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deux groupes (Agence, Pôle) et leurs catégories.

    C'est l'axe de cloisonnement d'insights360 : une catégorie Pennylane
    « Agence / Lyon » doit se raccrocher au périmètre `agence` des marts.
    """
    groupes: list[dict[str, Any]] = []
    categories: list[dict[str, Any]] = []
    cree = _dt(date(2024, 1, 2), 8, 30, 0, 0)
    ident_cat = 1
    for ident_groupe, (libelle, membres) in enumerate(_AXES, start=1):
        groupes.append(
            {
                "id": ident_groupe,
                "label": libelle,
                "categories": _lien(f"/category_groups/{ident_groupe}/categories"),
                "created_at": cree,
                "updated_at": cree,
            }
        )
        for nom, code in membres:
            categories.append(
                {
                    "id": ident_cat,
                    "label": f"{libelle} — {nom}",
                    "direction": None,
                    "created_at": cree,
                    "updated_at": cree,
                    "category_group": {"id": ident_groupe},
                    "analytical_code": code,
                }
            )
            ident_cat += 1
    return groupes, categories


def _etablissements_bancaires() -> list[dict[str, Any]]:
    noms = []
    for nom, _, _ in _BANQUES:
        if nom not in noms:
            noms.append(nom)
    cree = _dt(date(2024, 1, 2), 8, 0, 0, 0)
    return [
        {"id": i, "name": nom, "created_at": cree, "updated_at": cree}
        for i, nom in enumerate(noms, start=1)
    ]


# ═════════════════════════════════════════════════════════════════════════════
#  Tiers — clients (personnes morales ET physiques) et fournisseurs
# ═════════════════════════════════════════════════════════════════════════════


def _clients(comptes: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """Dix personnes morales, puis deux personnes physiques.

    Le `oneOf` de l'OpenAPI n'a que DEUX variantes, distinguées par
    `customer_type` — et elles n'ont pas les mêmes champs : la personne morale
    porte `name`, `reg_no`, `vat_number` ; la personne physique porte
    `first_name`/`last_name` et AUCUN des trois autres. Un consommateur qui lit
    `reg_no` sans regarder `customer_type` reçoit un KeyError sur la onzième
    ligne, jamais sur la première.
    """
    par_numero = {c["number"]: c for c in comptes}
    clients: list[dict[str, Any]] = []
    ident = 1

    for nom, ville, cp, adresse, _secteur, prospect in _CLIENTS:
        cree = date(2024, ((ident * 5) % 12) + 1, 12)
        pays = "BE" if ville == "Bruxelles" else "FR"
        siren = f"{400_000_000 + ident * 1_234_567 % 500_000_000}"
        domaine = nom.lower().replace(" ", "-").replace("é", "e").replace("è", "e")
        adr = {
            "address": adresse,
            "postal_code": cp,
            "city": ville,
            "country_alpha2": pays,
        }
        clients.append(
            {
                "id": ident,
                "customer_type": "company",
                "name": nom,
                "billing_iban": None,
                # Le délai de paiement : 30 jours par défaut, 45 fin de mois
                # pour les grands comptes. C'est ce qui fait diverger
                # `deadline` de `date + 30`, et un consommateur qui recalcule
                # l'échéance au lieu de la lire se trompe sur ceux-là.
                "payment_conditions": "45_days_end_of_month" if ident % 4 == 0 else "30_days",
                "recipient": "Service comptabilité fournisseurs",
                "phone": f"+331{rng.randint(10_000_000, 99_999_999)}",
                "reference": f"CLI-{ident:04d}",
                "notes": "Prospect — fiche créée, aucune facture émise." if prospect else None,
                "vat_number": f"{pays}{(ident * 7 + 11) % 100:02d}{siren}",
                "reg_no": siren,
                "ledger_account": {"id": par_numero[_aux("411", nom)]["id"]},
                "emails": [f"comptabilite@{domaine}.example"],
                "billing_address": dict(adr),
                "delivery_address": dict(adr),
                "external_reference": f"boond:company:{ident}",
                "billing_language": "fr_FR",
                "mandates": _lien(f"/customers/{ident}/mandates"),
                "pro_account_mandates": _lien(f"/customers/{ident}/pro_account_mandates"),
                "contacts": _lien(f"/customers/{ident}/contacts"),
                "created_at": _dt(cree, 10, 15, 0, 0),
                "updated_at": _maj(rng, cree),
            }
        )
        ident += 1

    for prenom, nom, ville, cp, adresse in _PARTICULIERS:
        cree = date(2026, 2 + ident % 3, 8)
        adr = {"address": adresse, "postal_code": cp, "city": ville, "country_alpha2": "FR"}
        clients.append(
            {
                "id": ident,
                "customer_type": "individual",
                "name": f"{prenom} {nom}",
                "first_name": prenom,
                "last_name": nom,
                "billing_iban": None,
                "payment_conditions": "upon_receipt",
                "recipient": f"{prenom} {nom}",
                "phone": f"+336{rng.randint(10_000_000, 99_999_999)}",
                "reference": f"CLI-{ident:04d}",
                "notes": None,
                "ledger_account": {"id": par_numero[_aux("411", f"{nom}{prenom}")]["id"]},
                "emails": [f"{prenom.lower()}.{nom.lower()}@example.org"],
                "billing_address": dict(adr),
                "delivery_address": dict(adr),
                "external_reference": f"formation:{ident}",
                "billing_language": "fr_FR",
                "mandates": _lien(f"/customers/{ident}/mandates"),
                "pro_account_mandates": _lien(f"/customers/{ident}/pro_account_mandates"),
                "contacts": _lien(f"/customers/{ident}/contacts"),
                "created_at": _dt(cree, 14, 5, 0, 0),
                "updated_at": _maj(rng, cree),
            }
        )
        ident += 1
    return clients


def _contacts(clients: list[dict[str, Any]], rng: random.Random) -> dict[int, list[dict[str, Any]]]:
    """Les contacts d'un client, servis sur `/customers/{id}/contacts`.

    ⚠️ MINIMISATION : ces enregistrements portent de l'identité. Le mock les
    sert parce que le fournisseur les sert — mais le connecteur d'insights360
    ne doit en extraire qu'une liste blanche de champs. Cf. la note du README.
    """
    fonctions = ("Directeur administratif et financier", "Comptable", "Responsable achats")
    prenoms = ("Sofia", "Hugo", "Amara", "Louis", "Priya", "Arthur", "Emma", "Mateo")
    noms = ("Marchand", "Léger", "Delaunay", "Baptiste", "Nguyen", "Aubert")
    par_client: dict[int, list[dict[str, Any]]] = {}
    ident = 1
    for client in clients:
        if client["customer_type"] != "company":
            continue
        domaine = client["emails"][0].split("@", 1)[1]
        contacts = []
        for _ in range(2 if client["id"] % 2 == 0 else 1):
            prenom = prenoms[(ident * 3) % len(prenoms)]
            nom = noms[(ident * 5) % len(noms)]
            contacts.append(
                {
                    "id": ident,
                    "first_name": prenom,
                    "last_name": nom,
                    "email": f"{prenom.lower()}.{nom.lower()}@{domaine}",
                    "phone": f"+331{rng.randint(10_000_000, 99_999_999)}",
                    "job_title": fonctions[ident % len(fonctions)],
                    "customer": _ref(client["id"], "/customers"),
                    "created_at": client["created_at"],
                    "updated_at": client["updated_at"],
                }
            )
            ident += 1
        par_client[client["id"]] = contacts
    return par_client


def _fournisseurs(comptes: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    par_numero = {c["number"]: c for c in comptes}
    fournisseurs = []
    for ident, (nom, ville, cp, adresse, _compte, _libelle) in enumerate(_FOURNISSEURS, start=1):
        cree = date(2024, 1 + ident, 5)
        siren = f"{500_000_000 + ident * 7_654_321 % 400_000_000}"
        domaine = nom.lower().replace(" ", "-").replace("è", "e").replace("&", "et")
        fournisseurs.append(
            {
                "id": ident,
                "name": nom,
                "establishment_no": f"{siren}000{ident:02d}",
                "reg_no": siren,
                "vat_number": f"FR{(ident * 13 + 3) % 100:02d}{siren}",
                "ledger_account": {"id": par_numero[_aux("401", nom)]["id"]},
                "emails": [f"facturation@{domaine}.example"],
                "iban": f"FR76{30_000 + ident:05d}0000{rng.randint(10**10, 10**11 - 1)}{ident:02d}",
                "postal_address": {
                    "address": adresse,
                    "postal_code": cp,
                    "city": ville,
                    "country_alpha2": "FR",
                },
                "supplier_payment_method": "automatic_transfer"
                if ident <= 3
                else "manual_transfer",
                "supplier_due_date_delay": 30,
                "supplier_due_date_rule": "days_or_end_of_month" if ident == 1 else "days",
                "external_reference": f"boond:company:{10 + ident}",
                "created_at": _dt(cree, 9, 30, 0, 0),
                "updated_at": _maj(rng, cree),
            }
        )
    return fournisseurs


def _produits(rng: random.Random, comptes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Le catalogue de prestations — le TJM par profil.

    `price_before_tax` et `price` sont des chaînes, comme tous les montants.
    Le second EST le TTC : il ne se recalcule pas, il se lit.
    """
    vente = next(c for c in comptes if c["number"] == "706000")
    formation = next(c for c in comptes if c["number"] == "706100")
    produits = []
    cree = date(2025, 11, 3)
    for ident, (libelle, tjm) in enumerate(_PRESTATIONS, start=1):
        est_formation = "Formation" in libelle
        produits.append(
            {
                "id": ident,
                "label": libelle,
                "description": f"{libelle} — facturation à la journée.",
                "external_reference": f"presta-{ident:02d}",
                "price_before_tax": _euros(tjm),
                "vat_rate": TAUX_TVA,
                "price": _euros(tjm + tjm * TVA // 100),
                "unit": "jour",
                "currency": DEVISE,
                "reference": f"PRE-{ident:03d}",
                "ledger_account": {"id": (formation if est_formation else vente)["id"]},
                "archived_at": None,
                "created_at": _dt(cree, 16, 0, 0, 0),
                "updated_at": _maj(rng, cree),
            }
        )
    return produits


def _comptes_bancaires(
    comptes: list[dict[str, Any]],
    etablissements: list[dict[str, Any]],
    journaux: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    par_numero = {c["number"]: c for c in comptes}
    banque = next(j for j in journaux if j["code"] == "BQ")
    cree = _dt(date(2024, 1, 2), 8, 0, 0, 0)
    resultat = []
    for ident, (etablissement, libelle, numero) in enumerate(_BANQUES, start=1):
        etab = next(e for e in etablissements if e["name"] == etablissement)
        resultat.append(
            {
                "id": ident,
                "name": libelle,
                "currency": DEVISE,
                # Le solde est RECALCULÉ à la fin de la construction, une fois
                # toutes les écritures posées : il doit être le solde du compte
                # 512 correspondant, sinon la banque et la comptabilité se
                # contredisent dans le même jeu de données.
                "balance": "0.00",
                "bank_establishment": {"id": etab["id"]},
                "journal": _ref(banque["id"], "/journals"),
                "ledger_account": _ref(par_numero[numero]["id"], "/ledger_accounts"),
                "created_at": cree,
                "updated_at": cree,
            }
        )
    return resultat


# ═════════════════════════════════════════════════════════════════════════════
#  Le grand livre — la SOURCE, dont tout le reste dérive
# ═════════════════════════════════════════════════════════════════════════════


class Grand:
    """Accumulateur d'écritures. Une écriture non équilibrée est un bug, pas
    une donnée : `passer()` le vérifie et lève.

    Les montants circulent en CENTIMES ENTIERS. C'est la seule façon d'avoir
    `sum(debit) == sum(credit)` exactement — avec des flottants, la balance
    d'un jeu de 3000 lignes finit par afficher un écart de deux centimes que
    personne n'arrive à expliquer.
    """

    def __init__(self, journaux: list[dict[str, Any]], comptes: list[dict[str, Any]]) -> None:
        self.journal_par_code = {j["code"]: j for j in journaux}
        self.compte_par_numero = {c["number"]: c for c in comptes}
        self.ecritures: list[dict[str, Any]] = []
        self.lignes: list[dict[str, Any]] = []
        self._id_ecriture = 1
        self._id_ligne = 1

    def compte(self, numero: str) -> dict[str, Any]:
        return self.compte_par_numero[numero]

    def passer(
        self,
        *,
        journal: str,
        jour: date,
        libelle: str,
        mouvements: list[tuple[str, int, str]],
        numero_piece: str | None = None,
        numero_facture: str | None = None,
        echeance: date | None = None,
        statut: str = "complete",
        categories: list[dict[str, Any]] | None = None,
        maj: str | None = None,
    ) -> dict[str, Any]:
        """Passe une écriture. `mouvements` : (compte, centimes signés, libellé).

        Un montant POSITIF est un DÉBIT, un montant NÉGATIF un CRÉDIT. Une
        seule convention de signe dans tout le module : deux conventions, et
        la moitié des écritures finit à l'envers sans que rien ne le dise.
        """
        total = sum(montant for _, montant, _ in mouvements)
        if total != 0:
            raise AssertionError(
                f"écriture déséquilibrée ({libelle}) : {total} centimes d'écart. "
                "C'est un bug du générateur, pas une donnée à servir."
            )
        j = self.journal_par_code[journal]
        ident = self._id_ecriture
        self._id_ecriture += 1
        cree = _dt(jour, 6, 0, 0, ident % 10**6)
        ecriture = {
            "id": ident,
            "created_at": cree,
            "updated_at": maj or cree,
            "label": libelle,
            "piece_number": numero_piece,
            "date": _d(jour),
            "due_date": _d(echeance) if echeance else None,
            "invoice_number": numero_facture,
            "journal_id": j["id"],
            "journal": _ref(j["id"], "/journals"),
            "status": statut,
            "categories": categories or [],
            "ledger_attachment_filename": None,
            "attachment": None,
        }
        self.ecritures.append(ecriture)

        for numero, montant, libelle_ligne in mouvements:
            compte = self.compte(numero)
            ligne_id = self._id_ligne
            self._id_ligne += 1
            self.lignes.append(
                {
                    "id": ligne_id,
                    "debit": _euros(max(montant, 0)),
                    "credit": _euros(max(-montant, 0)),
                    "label": libelle_ligne,
                    "categories": categories or [],
                    "ledger_account": {
                        "id": compte["id"],
                        "number": compte["number"],
                        "url": f"{BASE_URL}/ledger_accounts/{compte['id']}",
                    },
                    "journal": _ref(j["id"], "/journals"),
                    "date": _d(jour),
                    "ledger_entry": {"id": ident},
                    "lettered_ledger_entry_lines": {
                        "ids": [],
                        "url": f"{BASE_URL}/ledger_entry_lines/{ligne_id}"
                        "/lettered_ledger_entry_lines",
                    },
                    "created_at": cree,
                    "updated_at": ecriture["updated_at"],
                }
            )
        return ecriture

    def lettrer(self, ecriture_a: int, ecriture_b: int, numero: str) -> None:
        """Lettre les lignes de tiers de deux écritures — facture ↔ règlement.

        Le lettrage est ce qui distingue une créance soldée d'une créance
        ouverte, donc ce qui fait le `remaining_amount` d'une facture. Un mock
        qui l'omettrait servirait un `outstanding_balance` toujours nul.
        """
        del numero  # le fournisseur n'expose pas le code de lettrage en v2
        cotes = [
            ligne
            for ligne in self.lignes
            if ligne["ledger_entry"]["id"] in (ecriture_a, ecriture_b)
            and ligne["ledger_account"]["number"][:3] in ("411", "401")
        ]
        identifiants = [ligne["id"] for ligne in cotes]
        for ligne in cotes:
            ligne["lettered_ledger_entry_lines"]["ids"] = [
                i for i in identifiants if i != ligne["id"]
            ]


def _categorie(categories: list[dict[str, Any]], libelle_partiel: str) -> list[dict[str, Any]]:
    """La ventilation analytique d'une écriture : une catégorie à poids 1.

    `weight` est une CHAÎNE (`"1.0"`), comme tous les nombres du dialecte, et
    la somme des poids d'une ligne vaut 1 — c'est ce qui permet de ventiler un
    même montant sur deux axes sans le compter deux fois.
    """
    trouvee = next(c for c in categories if libelle_partiel in c["label"])
    return [
        {
            "id": trouvee["id"],
            "label": trouvee["label"],
            "weight": "1.0",
            "category_group": dict(trouvee["category_group"]),
            "analytical_code": trouvee["analytical_code"],
            "created_at": trouvee["created_at"],
            "updated_at": trouvee["updated_at"],
        }
    ]


# ═════════════════════════════════════════════════════════════════════════════
#  Les ventes — factures clients, lignes, règlements
# ═════════════════════════════════════════════════════════════════════════════

#: Agence de rattachement de chaque client, par identifiant. Reprend celle de
#: boondmanager-mock (Lyon → agence 2, Nantes → 3, le reste → Paris).
_AGENCE_DU_CLIENT = {
    1: "Paris",
    2: "Paris",
    3: "Lyon",
    4: "Nantes",
    5: "Paris",
    6: "Lyon",
    7: "Paris",
    8: "Paris",
    9: "Nantes",
    10: "Paris",
    11: "Paris",
    12: "Lyon",
}
_POLE_DU_CLIENT = {
    1: "Data & Analytics",
    2: "Cybersécurité",
    3: "Cloud & Platform",
    4: "Data & Analytics",
    5: "Cloud & Platform",
    6: "Data & Analytics",
    7: "Cloud & Platform",
    8: "Cybersécurité",
    9: "Data & Analytics",
    10: "Data & Analytics",
    11: "Data & Analytics",
    12: "Data & Analytics",
}


def _statut_facture(mois: int, brouillon: bool, regle: bool) -> str:
    if brouillon:
        return "draft"
    if regle:
        return "paid"
    # Une facture de juin, émise début juillet, échéance à 30 jours : elle n'est
    # pas encore en retard le 15 juillet. `upcoming` et non `late` — la
    # distinction porte tout un tableau de bord de recouvrement.
    del mois
    return "upcoming"


def _echeance(emission: date, conditions: str) -> date:
    """`deadline` selon les conditions de paiement du client.

    Le fournisseur SERT l'échéance ; il ne demande pas de la recalculer. Elle
    est reproduite ici pour que les deux valeurs soient cohérentes — mais un
    consommateur doit LIRE `deadline`, pas le déduire de `date`, sinon il se
    trompe sur les clients en « fin de mois ».
    """
    if conditions == "upon_receipt":
        return emission
    if conditions == "45_days_end_of_month":
        cible = emission + timedelta(days=45)
        return _fin_mois(cible.year, cible.month)
    return emission + timedelta(days=30)


def _ventes(  # noqa: PLR0915 — trois familles de ventes en un seul passage
    grand: Grand,
    clients: list[dict[str, Any]],
    produits: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Une facture par client actif et par mois échu de 2026.

    Janvier→mai : réglées. Juin : émises, non réglées. Juillet : un brouillon
    chez un client sur quatre — un brouillon n'a PAS de numéro de facture
    définitif ni d'écriture comptable, et c'est un cas que tout connecteur doit
    rencontrer (`draft: true`, `invoice_number` provisoire, `status: "draft"`).
    """
    factures: list[dict[str, Any]] = []
    lignes_par_facture: dict[int, list[dict[str, Any]]] = {}
    reglements: list[dict[str, Any]] = []  # (facture, date, écriture) — pour la banque
    ident = 1
    prestations_vente = [p for p in produits if "Formation" not in p["label"]]

    actifs = [c for c in clients if c["customer_type"] == "company" and c["notes"] is None]
    for mois in range(1, 8):
        for client in actifs:
            emission = _fin_mois(2026, mois) + timedelta(days=2)
            brouillon = emission > AUJOURDHUI
            if brouillon:
                if mois != 7 or client["id"] % 4 != 1:
                    continue
                emission = AUJOURDHUI

            # Un à trois consultants facturés, stable pour un (client, mois).
            tirage = random.Random(f"{client['id']}:{mois}:vente")
            nb = 1 + (client["id"] + mois) % 3
            jours_dispo = _jours_ouvres(2026, mois)
            lignes: list[dict[str, Any]] = []
            ht = 0
            for rang in range(nb):
                produit = prestations_vente[
                    (client["id"] * 3 + mois + rang) % len(prestations_vente)
                ]
                quantite = max(1, jours_dispo - tirage.randint(0, 4))
                pu = round(float(produit["price_before_tax"]) * 100)
                ligne_ht = pu * quantite
                ht += ligne_ht
                lignes.append(
                    {
                        "id": 0,  # attribué plus bas, une fois la facture connue
                        "label": produit["label"],
                        "unit": "jour",
                        "quantity": str(quantite),
                        "amount": _euros(ligne_ht + ligne_ht * TVA // 100),
                        "currency_amount": _euros(ligne_ht + ligne_ht * TVA // 100),
                        "description": (
                            f"{produit['label']} — {calendar.month_name[mois]} 2026, "
                            f"{quantite} jours."
                        ),
                        "product": _ref(produit["id"], "/products"),
                        "vat_rate": TAUX_TVA,
                        "currency_amount_before_tax": _euros(ligne_ht),
                        "currency_tax": _euros(ligne_ht * TVA // 100),
                        "tax": _euros(ligne_ht * TVA // 100),
                        "raw_currency_unit_price": _euros(pu),
                        "discount": {"type": "relative", "value": "0"},
                        "section_rank": 1,
                        "imputation_dates": {
                            "start_date": _d(date(2026, mois, 1)),
                            "end_date": _d(_fin_mois(2026, mois)),
                        },
                        "created_at": _dt(emission, 8, 5, 0, ident),
                        "updated_at": _dt(emission, 8, 5, 0, ident),
                    }
                )
            tva = ht * TVA // 100
            ttc = ht + tva
            echeance = _echeance(emission, client["payment_conditions"])
            regle = mois <= 5 and not brouillon
            paye_le = emission + timedelta(days=tirage.randint(18, 32)) if regle else None
            if paye_le and paye_le > AUJOURDHUI:
                paye_le, regle = None, False

            numero = f"FAC-2026-{ident:04d}"
            analytique = _categorie(categories, _AGENCE_DU_CLIENT[client["id"]])
            ecriture = None
            if not brouillon:
                # L'écriture de vente : le tiers au débit, le produit et la TVA
                # au crédit. C'est ELLE qui porte le montant ; la facture en
                # dérive, et non l'inverse.
                ecriture = grand.passer(
                    journal="VE",
                    jour=emission,
                    libelle=f"Facture {numero} — {client['name']}",
                    numero_piece=numero,
                    numero_facture=numero,
                    echeance=echeance,
                    categories=analytique,
                    mouvements=[
                        (_aux("411", client["name"]), ttc, f"{client['name']} — {numero}"),
                        ("706000", -ht, f"Prestations {calendar.month_name[mois]} 2026"),
                        ("445710", -tva, f"TVA collectée {TVA}%"),
                    ],
                )

            facture = {
                "id": ident,
                "label": f"Prestations {calendar.month_name[mois]} 2026",
                "invoice_number": "" if brouillon else numero,
                "currency": DEVISE,
                "amount": _euros(ttc),
                "currency_amount": _euros(ttc),
                "currency_amount_before_tax": _euros(ht),
                "exchange_rate": "1.0",
                "date": _d(emission),
                "deadline": _d(echeance),
                "currency_tax": _euros(tva),
                "tax": _euros(tva),
                "language": "fr_FR",
                "paid": regle,
                "status": _statut_facture(mois, brouillon, regle),
                "discount": {"type": "relative", "value": None},
                "ledger_entry": {"id": ecriture["id"]} if ecriture else None,
                "public_file_url": None if brouillon else f"https://files.example/{numero}.pdf",
                "filename": None if brouillon else f"{numero}.pdf",
                "remaining_amount_with_tax": "0.00" if regle else _euros(ttc),
                "remaining_amount_without_tax": "0.00" if regle else _euros(ht),
                "draft": brouillon,
                "special_mention": None,
                "customer": _ref(client["id"], "/customers"),
                "invoice_line_sections": _lien(f"/customer_invoices/{ident}/invoice_line_sections"),
                "invoice_lines": _lien(f"/customer_invoices/{ident}/invoice_lines"),
                "custom_header_fields": _lien(f"/customer_invoices/{ident}/custom_header_fields"),
                "categories": _lien(f"/customer_invoices/{ident}/categories"),
                "pdf_invoice_free_text": "Merci de votre confiance.",
                "pdf_invoice_subject": f"Prestations {calendar.month_name[mois]} 2026",
                "pdf_description": None,
                "billing_subscription": None,
                "credited_invoice": None,
                "customer_invoice_template": {"id": 1},
                "transaction_reference": {
                    "banking_provider": "bank",
                    "provider_field_name": "label",
                    "provider_field_value": numero,
                },
                "payments": _lien(f"/customer_invoices/{ident}/payments"),
                "matched_transactions": _lien(f"/customer_invoices/{ident}/matched_transactions"),
                "appendices": _lien(f"/customer_invoices/{ident}/appendices"),
                "quote": None,
                "external_reference": f"boond:invoice:{ident}",
                "e_invoicing": None,
                "factur_x": False,
                "schematron_validation_status": None,
                "archived_at": None,
                "created_at": _dt(emission, 8, 5, 0, ident),
                "updated_at": _maj(rng, paye_le or emission),
            }
            for rang, ligne in enumerate(lignes, start=1):
                ligne["id"] = ident * 100 + rang
            lignes_par_facture[ident] = lignes
            factures.append(facture)
            if paye_le is not None and ecriture is not None:
                reglements.append(
                    {
                        "facture": facture,
                        "jour": paye_le,
                        "ecriture_vente": ecriture["id"],
                        "client": client,
                        "ttc": ttc,
                    }
                )
            ident += 1

    # ── L'avoir ──────────────────────────────────────────────────────────────
    # Correction d'un trop-facturé sur la première facture réglée. Un avoir est
    # une facture à MONTANT NÉGATIF portant `status: "credit_note"` et
    # `credited_invoice` — pas une entité d'un autre type. Un connecteur qui
    # sommerait naïvement `amount` sans regarder le signe se trompe de CA.
    if reglements:
        origine = reglements[0]["facture"]
        ttc_avoir = -(round(float(origine["amount"]) * 100) // 10)
        ht_avoir = ttc_avoir * 100 // (100 + TVA)
        tva_avoir = ttc_avoir - ht_avoir
        jour = date.fromisoformat(origine["date"]) + timedelta(days=14)
        numero = f"AV-2026-{ident:04d}"
        client = next(c for c in clients if c["id"] == origine["customer"]["id"])
        ecriture = grand.passer(
            journal="VE",
            jour=jour,
            libelle=f"Avoir {numero} — {client['name']}",
            numero_piece=numero,
            numero_facture=numero,
            echeance=jour,
            categories=_categorie(categories, _AGENCE_DU_CLIENT[client["id"]]),
            mouvements=[
                (_aux("411", client["name"]), ttc_avoir, f"{client['name']} — {numero}"),
                ("706000", -ht_avoir, "Avoir sur prestations"),
                ("445710", -tva_avoir, f"TVA collectée {TVA}% — avoir"),
            ],
        )
        factures.append(
            {
                **origine,
                "id": ident,
                "label": "Avoir sur trop-facturé",
                "invoice_number": numero,
                "amount": _euros(ttc_avoir),
                "currency_amount": _euros(ttc_avoir),
                "currency_amount_before_tax": _euros(ht_avoir),
                "currency_tax": _euros(tva_avoir),
                "tax": _euros(tva_avoir),
                "date": _d(jour),
                "deadline": _d(jour),
                "paid": False,
                "status": "credit_note",
                "ledger_entry": {"id": ecriture["id"]},
                "remaining_amount_with_tax": _euros(ttc_avoir),
                "remaining_amount_without_tax": _euros(ht_avoir),
                "draft": False,
                "credited_invoice": _ref(origine["id"], "/customer_invoices"),
                "filename": f"{numero}.pdf",
                "public_file_url": f"https://files.example/{numero}.pdf",
                "invoice_line_sections": _lien(f"/customer_invoices/{ident}/invoice_line_sections"),
                "invoice_lines": _lien(f"/customer_invoices/{ident}/invoice_lines"),
                "custom_header_fields": _lien(f"/customer_invoices/{ident}/custom_header_fields"),
                "categories": _lien(f"/customer_invoices/{ident}/categories"),
                "payments": _lien(f"/customer_invoices/{ident}/payments"),
                "matched_transactions": _lien(f"/customer_invoices/{ident}/matched_transactions"),
                "appendices": _lien(f"/customer_invoices/{ident}/appendices"),
                "external_reference": f"boond:invoice:{ident}",
                "created_at": _dt(jour, 9, 55, 0, ident),
                "updated_at": _dt(jour, 9, 55, 0, ident),
            }
        )
        lignes_par_facture[ident] = [
            {
                **lignes_par_facture[origine["id"]][0],
                "id": ident * 100 + 1,
                "label": "Avoir sur trop-facturé",
                "quantity": "1",
                "amount": _euros(ttc_avoir),
                "currency_amount": _euros(ttc_avoir),
                "currency_amount_before_tax": _euros(ht_avoir),
                "currency_tax": _euros(tva_avoir),
                "tax": _euros(tva_avoir),
                "raw_currency_unit_price": _euros(ht_avoir),
                "created_at": _dt(jour, 9, 55, 0, ident),
                "updated_at": _dt(jour, 9, 55, 0, ident),
            }
        ]
        ident += 1

    # ── Les deux formations, facturées à des PARTICULIERS ────────────────────
    formation = next(p for p in produits if "Formation" in p["label"])
    for rang, client in enumerate(
        [c for c in clients if c["customer_type"] == "individual"], start=1
    ):
        jour = date(2026, 2 + rang * 2, 12)
        pu = round(float(formation["price_before_tax"]) * 100)
        quantite = 1 + rang
        ht = pu * quantite
        tva = ht * TVA // 100
        ttc = ht + tva
        numero = f"FAC-2026-{ident:04d}"
        ecriture = grand.passer(
            journal="VE",
            jour=jour,
            libelle=f"Facture {numero} — {client['name']}",
            numero_piece=numero,
            numero_facture=numero,
            echeance=jour,
            categories=_categorie(categories, "Paris"),
            mouvements=[
                (
                    _aux("411", f"{client['last_name']}{client['first_name']}"),
                    ttc,
                    f"{client['name']} — {numero}",
                ),
                ("706100", -ht, "Formation Power BI"),
                ("445710", -tva, f"TVA collectée {TVA}%"),
            ],
        )
        facture = {
            "id": ident,
            "label": "Formation Power BI",
            "invoice_number": numero,
            "currency": DEVISE,
            "amount": _euros(ttc),
            "currency_amount": _euros(ttc),
            "currency_amount_before_tax": _euros(ht),
            "exchange_rate": "1.0",
            "date": _d(jour),
            "deadline": _d(jour),
            "currency_tax": _euros(tva),
            "tax": _euros(tva),
            "language": "fr_FR",
            "paid": True,
            "status": "paid",
            "discount": {"type": "relative", "value": None},
            "ledger_entry": {"id": ecriture["id"]},
            "public_file_url": f"https://files.example/{numero}.pdf",
            "filename": f"{numero}.pdf",
            "remaining_amount_with_tax": "0.00",
            "remaining_amount_without_tax": "0.00",
            "draft": False,
            "special_mention": "Action de formation — article L6313-1 du code du travail.",
            "customer": _ref(client["id"], "/customers"),
            "invoice_line_sections": _lien(f"/customer_invoices/{ident}/invoice_line_sections"),
            "invoice_lines": _lien(f"/customer_invoices/{ident}/invoice_lines"),
            "custom_header_fields": _lien(f"/customer_invoices/{ident}/custom_header_fields"),
            "categories": _lien(f"/customer_invoices/{ident}/categories"),
            "pdf_invoice_free_text": "Merci de votre confiance.",
            "pdf_invoice_subject": "Formation Power BI",
            "pdf_description": None,
            "billing_subscription": None,
            "credited_invoice": None,
            "customer_invoice_template": {"id": 1},
            "transaction_reference": {
                "banking_provider": "bank",
                "provider_field_name": "label",
                "provider_field_value": numero,
            },
            "payments": _lien(f"/customer_invoices/{ident}/payments"),
            "matched_transactions": _lien(f"/customer_invoices/{ident}/matched_transactions"),
            "appendices": _lien(f"/customer_invoices/{ident}/appendices"),
            "quote": None,
            "external_reference": f"formation:invoice:{ident}",
            "e_invoicing": None,
            "factur_x": False,
            "schematron_validation_status": None,
            "archived_at": None,
            "created_at": _dt(jour, 11, 0, 0, ident),
            "updated_at": _dt(jour, 11, 0, 0, ident),
        }
        factures.append(facture)
        lignes_par_facture[ident] = [
            {
                "id": ident * 100 + 1,
                "label": formation["label"],
                "unit": "jour",
                "quantity": str(quantite),
                "amount": _euros(ttc),
                "currency_amount": _euros(ttc),
                "description": "Formation intra — Power BI, niveau avancé.",
                "product": _ref(formation["id"], "/products"),
                "vat_rate": TAUX_TVA,
                "currency_amount_before_tax": _euros(ht),
                "currency_tax": _euros(tva),
                "tax": _euros(tva),
                "raw_currency_unit_price": _euros(pu),
                "discount": {"type": "relative", "value": "0"},
                "section_rank": 1,
                "imputation_dates": {"start_date": _d(jour), "end_date": _d(jour)},
                "created_at": _dt(jour, 11, 0, 0, ident),
                "updated_at": _dt(jour, 11, 0, 0, ident),
            }
        ]
        reglements.append(
            {
                "facture": facture,
                "jour": jour + timedelta(days=3),
                "ecriture_vente": ecriture["id"],
                "client": client,
                "ttc": ttc,
            }
        )
        ident += 1

    return factures, lignes_par_facture, reglements


# ═════════════════════════════════════════════════════════════════════════════
#  Les achats — factures fournisseurs, lignes, décaissements
# ═════════════════════════════════════════════════════════════════════════════

#: (fournisseur, libellé, HT en centimes, mensuel ?, mois d'émission si ponctuel)
_ACHATS: tuple[tuple[str, str, int, bool, tuple[int, ...]], ...] = (
    ("Softalliance", "Licences plateforme data", 89_000, True, ()),
    ("Softalliance", "Abonnement observabilité", 34_000, True, ()),
    ("Foncière Beaumont", "Loyer agence Paris", 830_000, True, ()),
    ("Foncière Beaumont", "Loyer agence Lyon", 290_000, True, ()),
    ("Nordnet Télécom", "Liens et téléphonie", 62_000, True, ()),
    ("Bureau & Cie", "Postes de travail consultants", 990_000, False, (2, 6)),
    ("Fivetech Partners", "Sous-traitance — mission Voltalis", 1_460_000, False, (3,)),
    ("Fivetech Partners", "Sous-traitance — mission Pharmadis", 1_120_000, False, (5,)),
)


def _achats(
    grand: Grand,
    fournisseurs: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Les factures fournisseurs et leurs règlements.

    Trois `accounting_status` distincts dans le jeu — `complete`, `entry`,
    `validation_needed` — parce que c'est le champ sur lequel un cabinet
    filtre, et qu'un jeu où tout vaut `complete` ne prouve rien.
    """
    par_nom = {f["name"]: f for f in fournisseurs}
    compte_de = {nom: compte for nom, _, _, _, compte, _ in _FOURNISSEURS}
    factures: list[dict[str, Any]] = []
    lignes_par_facture: dict[int, list[dict[str, Any]]] = {}
    reglements: list[dict[str, Any]] = []
    ident = 1

    for nom, libelle, ht_base, mensuel, mois_ponctuels in _ACHATS:
        fournisseur = par_nom[nom]
        mois_emis = tuple(range(1, 7)) if mensuel else mois_ponctuels
        for mois in mois_emis:
            jour = date(2026, mois, 5)
            if jour > AUJOURDHUI:
                continue
            tirage = random.Random(f"{nom}:{libelle}:{mois}")
            ht = ht_base if mensuel else ht_base + tirage.randint(-20_000, 20_000)
            tva = ht * TVA // 100
            ttc = ht + tva
            numero = f"{nom[:3].upper()}-2026-{mois:02d}{ident:03d}"
            echeance = jour + timedelta(days=fournisseur["supplier_due_date_delay"])
            # Les factures de juin restent à payer ; les précédentes sont réglées.
            regle = mois <= 5
            paye_le = jour + timedelta(days=tirage.randint(20, 30)) if regle else None
            if paye_le and paye_le > AUJOURDHUI:
                paye_le, regle = None, False
            statut_compta = "complete" if regle else ("entry" if mois == 6 else "validation_needed")
            analytique = _categorie(categories, "Lyon" if "Lyon" in libelle else "Paris")

            ecriture = grand.passer(
                journal="AC",
                jour=jour,
                libelle=f"{nom} — {libelle}",
                numero_piece=numero,
                numero_facture=numero,
                echeance=echeance,
                statut=statut_compta,
                categories=analytique,
                mouvements=[
                    (compte_de[nom], ht, f"{libelle} {calendar.month_name[mois]} 2026"),
                    ("445660", tva, f"TVA déductible {TVA}%"),
                    (_aux("401", nom), -ttc, f"{nom} — {numero}"),
                ],
            )

            factures.append(
                {
                    "id": ident,
                    "label": libelle,
                    "invoice_number": numero,
                    "currency": DEVISE,
                    "amount": _euros(ttc),
                    "currency_amount": _euros(ttc),
                    "currency_amount_before_tax": _euros(ht),
                    "exchange_rate": "1.0",
                    "date": _d(jour),
                    "deadline": _d(echeance),
                    "currency_tax": _euros(tva),
                    "tax": _euros(tva),
                    "reconciled": regle,
                    "accounting_status": statut_compta,
                    "filename": f"{numero}.pdf",
                    "public_file_url": f"https://files.example/{numero}.pdf",
                    "remaining_amount_with_tax": "0.00" if regle else _euros(ttc),
                    "remaining_amount_without_tax": "0.00" if regle else _euros(ht),
                    "ledger_entry": {"id": ecriture["id"]},
                    "supplier": _ref(fournisseur["id"], "/suppliers"),
                    "invoice_lines": _lien(f"/supplier_invoices/{ident}/invoice_lines"),
                    "categories": _lien(f"/supplier_invoices/{ident}/categories"),
                    "transaction_reference": {
                        "banking_provider": "bank",
                        "provider_field_name": "label",
                        "provider_field_value": numero,
                    },
                    "payment_status": "fully_paid" if regle else "to_be_paid",
                    "paid": regle,
                    "payments": _lien(f"/supplier_invoices/{ident}/payments"),
                    "matched_transactions": _lien(
                        f"/supplier_invoices/{ident}/matched_transactions"
                    ),
                    "external_reference": f"boond:purchase:{ident}",
                    "import_source": None,
                    "e_invoicing": None,
                    "archived_at": None,
                    "created_at": _dt(jour, 7, 30, 0, ident),
                    "updated_at": _maj(rng, paye_le or jour),
                }
            )
            lignes_par_facture[ident] = [
                {
                    "id": ident * 100 + 1,
                    "label": libelle,
                    "quantity": "1",
                    "unit": "forfait",
                    "amount": _euros(ttc),
                    "currency_amount": _euros(ttc),
                    "currency_amount_before_tax": _euros(ht),
                    "currency_tax": _euros(tva),
                    "tax": _euros(tva),
                    "vat_rate": TAUX_TVA,
                    "raw_currency_unit_price": _euros(ht),
                    "description": f"{libelle} — {calendar.month_name[mois]} 2026.",
                    "ledger_account": {"id": grand.compte(compte_de[nom])["id"]},
                    "created_at": _dt(jour, 7, 30, 0, ident),
                    "updated_at": _dt(jour, 7, 30, 0, ident),
                }
            ]
            if paye_le is not None:
                reglements.append(
                    {
                        "facture": factures[-1],
                        "jour": paye_le,
                        "ecriture_achat": ecriture["id"],
                        "fournisseur": fournisseur,
                        "ttc": ttc,
                    }
                )
            ident += 1
    return factures, lignes_par_facture, reglements


# ═════════════════════════════════════════════════════════════════════════════
#  La banque — transactions, rapprochements, paie, frais
# ═════════════════════════════════════════════════════════════════════════════


def _banque(  # noqa: PLR0915, PLR0917 — la banque voit passer TOUS les flux
    grand: Grand,
    comptes_bancaires: list[dict[str, Any]],
    reglements_clients: list[dict[str, Any]],
    reglements_fournisseurs: list[dict[str, Any]],
    journaux: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[int, list[int]], dict[int, list[int]]]:
    """Les mouvements bancaires, et le rapprochement facture ↔ transaction.

    ┌─ TROIS TRANSACTIONS NON RAPPROCHÉES, DÉLIBÉRÉMENT ─────────────────────┐
    │ Un jeu où tout est rapproché ne prouve rien : c'est justement la        │
    │ transaction ORPHELINE qui fait le travail d'un cabinet, et le           │
    │ `outstanding_balance` non nul qui doit remonter dans un tableau de      │
    │ bord. Trois encaissements clients restent donc sans facture appariée.   │
    └─────────────────────────────────────────────────────────────────────────┘
    """
    banque = next(j for j in journaux if j["code"] == "BQ")
    principal, reserve = comptes_bancaires[0], comptes_bancaires[1]
    transactions: list[dict[str, Any]] = []
    apparie_facture_client: dict[int, list[int]] = {}
    apparie_facture_fournisseur: dict[int, list[int]] = {}
    ident = 1

    evenements: list[tuple[date, str, dict[str, Any]]] = []
    for r in reglements_clients:
        evenements.append((r["jour"], "client", r))
    for r in reglements_fournisseurs:
        evenements.append((r["jour"], "fournisseur", r))
    evenements.sort(key=lambda e: (e[0], e[1], e[2]["facture"]["id"]))

    #: Les trois derniers encaissements clients restent NON rapprochés.
    non_rapproches = {
        r["facture"]["id"] for r in sorted(reglements_clients, key=lambda r: r["jour"])[-3:]
    }

    for jour, genre, r in evenements:
        facture = r["facture"]
        ttc = r["ttc"]
        if genre == "client":
            tiers = r["client"]
            libelle = f"VIR SEPA {tiers['name'].upper()} {facture['invoice_number']}"
            compte_tiers = (
                _aux("411", tiers["name"])
                if tiers["customer_type"] == "company"
                else _aux("411", f"{tiers['last_name']}{tiers['first_name']}")
            )
            ecriture = grand.passer(
                journal="BQ",
                jour=jour,
                libelle=libelle,
                numero_piece=f"BQ-{ident:04d}",
                categories=_categorie(categories, _AGENCE_DU_CLIENT[tiers["id"]]),
                mouvements=[
                    ("512000", ttc, libelle),
                    (compte_tiers, -ttc, f"Règlement {facture['invoice_number']}"),
                ],
            )
            grand.lettrer(r["ecriture_vente"], ecriture["id"], facture["invoice_number"])
            rapproche = facture["id"] not in non_rapproches
            transaction = _transaction(
                ident,
                jour=jour,
                libelle=libelle,
                montant=ttc,
                compte=principal,
                journal=banque,
                tiers_client=_ref(tiers["id"], "/customers") if rapproche else None,
                tiers_fournisseur=None,
                reste=0 if rapproche else ttc,
                categories=_categorie(categories, _AGENCE_DU_CLIENT[tiers["id"]]),
                rng=rng,
            )
            if rapproche:
                apparie_facture_client.setdefault(facture["id"], []).append(ident)
        else:
            tiers = r["fournisseur"]
            libelle = f"PRLV SEPA {tiers['name'].upper()} {facture['invoice_number']}"
            ecriture = grand.passer(
                journal="BQ",
                jour=jour,
                libelle=libelle,
                numero_piece=f"BQ-{ident:04d}",
                categories=_categorie(categories, "Paris"),
                mouvements=[
                    (_aux("401", tiers["name"]), ttc, f"Règlement {facture['invoice_number']}"),
                    ("512000", -ttc, libelle),
                ],
            )
            grand.lettrer(r["ecriture_achat"], ecriture["id"], facture["invoice_number"])
            transaction = _transaction(
                ident,
                jour=jour,
                libelle=libelle,
                montant=-ttc,
                compte=principal,
                journal=banque,
                tiers_client=None,
                tiers_fournisseur=_ref(tiers["id"], "/suppliers"),
                reste=0,
                categories=_categorie(categories, "Paris"),
                rng=rng,
            )
            apparie_facture_fournisseur.setdefault(facture["id"], []).append(ident)
        transactions.append(transaction)
        ident += 1

    # ── La paie, du 1er au 6e mois ───────────────────────────────────────────
    # 34 salariés. Un seul mouvement par mois : brut + charges au débit,
    # banque au crédit. La paie n'a pas de facture — c'est la seule famille
    # d'écritures du jeu qui n'a AUCUNE pièce, et un consommateur qui suppose
    # « une écriture = une facture » se casse dessus.
    for mois in range(1, 7):
        jour = _fin_mois(2026, mois) - timedelta(days=2)
        brut = 34 * 385_000 + mois * 12_000
        charges = brut * 42 // 100
        libelle = f"Paie {calendar.month_name[mois]} 2026"
        grand.passer(
            journal="SA",
            jour=jour,
            libelle=libelle,
            numero_piece=f"PAIE-2026-{mois:02d}",
            mouvements=[
                ("641100", brut, "Salaires et appointements"),
                ("645000", charges, "Charges sociales"),
                ("512000", -(brut + charges), libelle),
            ],
        )
        transactions.append(
            _transaction(
                ident,
                jour=jour,
                libelle=f"VIR MULTIPLE PAIE {mois:02d}/2026",
                montant=-(brut + charges),
                compte=principal,
                journal=banque,
                tiers_client=None,
                tiers_fournisseur=None,
                reste=0,
                categories=[],
                rng=rng,
            )
        )
        ident += 1

    # ── Les frais bancaires ──────────────────────────────────────────────────
    for mois in range(1, 8):
        jour = date(2026, mois, 3)
        if jour > AUJOURDHUI:
            continue
        frais = 4_500 + mois * 120
        grand.passer(
            journal="BQ",
            jour=jour,
            libelle=f"Frais bancaires {calendar.month_name[mois]} 2026",
            numero_piece=f"BQ-FRAIS-{mois:02d}",
            mouvements=[
                ("627000", frais, "Commissions et frais de tenue de compte"),
                ("512000", -frais, "Frais bancaires"),
            ],
        )
        transactions.append(
            _transaction(
                ident,
                jour=jour,
                libelle=f"FRAIS TENUE DE COMPTE {mois:02d}/2026",
                montant=-frais,
                compte=principal,
                journal=banque,
                tiers_client=None,
                tiers_fournisseur=None,
                reste=0,
                categories=[],
                rng=rng,
            )
        )
        ident += 1

    # ── Le virement interne vers le compte de réserve ────────────────────────
    # Deux comptes bancaires, donc un mouvement ENTRE eux : c'est la seule
    # écriture du jeu qui touche deux comptes 512, et elle vaut d'exister
    # parce qu'un rapprochement naïf la compte deux fois en trésorerie.
    jour = date(2026, 4, 15)
    montant = 5_000_000
    grand.passer(
        journal="BQ",
        jour=jour,
        libelle="Virement interne vers le compte de réserve",
        numero_piece="BQ-INT-001",
        mouvements=[("512100", montant, "Alimentation réserve"), ("512000", -montant, "Réserve")],
    )
    for compte, signe in ((principal, -1), (reserve, +1)):
        transactions.append(
            _transaction(
                ident,
                jour=jour,
                libelle="VIR INTERNE RESERVE",
                montant=signe * montant,
                compte=compte,
                journal=banque,
                tiers_client=None,
                tiers_fournisseur=None,
                reste=0,
                categories=[],
                rng=rng,
            )
        )
        ident += 1

    return transactions, apparie_facture_client, apparie_facture_fournisseur


def _transaction(
    ident: int,
    *,
    jour: date,
    libelle: str,
    montant: int,
    compte: dict[str, Any],
    journal: dict[str, Any],
    tiers_client: dict[str, Any] | None,
    tiers_fournisseur: dict[str, Any] | None,
    reste: int,
    categories: list[dict[str, Any]],
    rng: random.Random,
) -> dict[str, Any]:
    """Une ligne de relevé bancaire.

    `amount` est SIGNÉ : négatif au débit du compte. `outstanding_balance` est
    le reste à rapprocher — `null` quand il n'y a rien à rapprocher, `"0.0"`
    quand tout l'est. La nuance entre les deux existe chez le fournisseur, et
    un consommateur qui les confond compte des transactions orphelines qui
    n'en sont pas.
    """
    horodatage = _dt(jour, rng.randint(3, 7), rng.randint(0, 59), 0, rng.randrange(10**6))
    return {
        "id": ident,
        "label": libelle,
        "attachment_required": reste != 0,
        "date": _d(jour),
        "outstanding_balance": _euros(reste) if reste else None,
        "created_at": horodatage,
        "updated_at": horodatage,
        "archived_at": None,
        "currency": DEVISE,
        "currency_amount": _euros(montant),
        "amount": _euros(montant),
        "currency_fee": None,
        "fee": None,
        "journal": _ref(journal["id"], "/journals"),
        "bank_account": _ref(compte["id"], "/bank_accounts"),
        "pro_account_expense": None,
        "customer": tiers_client,
        "supplier": tiers_fournisseur,
        "categories": categories,
        "matched_invoices": _lien(f"/transactions/{ident}/matched_invoices"),
        "interbank_code": "B1D" if montant > 0 else "B2C",
    }


# ═════════════════════════════════════════════════════════════════════════════
#  La périphérie — devis, abonnements, mandats, documents, demandes d'achat
# ═════════════════════════════════════════════════════════════════════════════
#
# ┌─ FIDÉLITÉ GRADUÉE, ET C'EST UNE DÉCISION ──────────────────────────────────┐
# │ Les ressources ci-dessous existent chez le fournisseur et sont servies —   │
# │ le périmètre demandé est « toute la surface v2 en lecture ». Mais elles    │
# │ ne portent PAS le flux qu'insights360 consomme (facturation, banque,       │
# │ comptabilité), et leur jeu de données est donc volontairement mince :      │
# │ quelques éléments cohérents, aux champs déclarés par l'OpenAPI, sans la    │
# │ mécanique d'écriture double qui porte le cœur.                             │
# │                                                                            │
# │ Ce qui n'est PAS négociable même ici : la FORME. Une liste vide se pagine  │
# │ comme les autres, une ressource inconnue rend le 404 du dialecte, et les   │
# │ montants restent des chaînes. C'est la forme qui casse un connecteur, pas  │
# │ le volume.                                                                 │
# └────────────────────────────────────────────────────────────────────────────┘


def _devis(clients: list[dict[str, Any]], produits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cinq devis couvrant les cinq statuts documentés — `pending`, `accepted`,
    `denied`, `expired`, `invoiced`. Un jeu à statut unique ne teste aucun filtre."""
    statuts = ("pending", "accepted", "denied", "expired", "invoiced")
    actifs = [c for c in clients if c["customer_type"] == "company"]
    devis = []
    for ident, statut in enumerate(statuts, start=1):
        client = actifs[ident % len(actifs)]
        produit = produits[ident % len(produits)]
        jour = date(2026, 1 + ident, 9)
        ht = round(float(produit["price_before_tax"]) * 100) * (10 + ident)
        tva = ht * TVA // 100
        devis.append(
            {
                "id": ident,
                "label": f"Proposition {produit['label']}",
                "quote_number": f"DEV-2026-{ident:04d}",
                "status": statut,
                "currency": DEVISE,
                "amount": _euros(ht + tva),
                "currency_amount": _euros(ht + tva),
                "currency_amount_before_tax": _euros(ht),
                "currency_tax": _euros(tva),
                "tax": _euros(tva),
                "date": _d(jour),
                "deadline": _d(jour + timedelta(days=30)),
                "language": "fr_FR",
                "customer": _ref(client["id"], "/customers"),
                "invoice_lines": _lien(f"/quotes/{ident}/invoice_lines"),
                "invoice_line_sections": _lien(f"/quotes/{ident}/invoice_line_sections"),
                "appendices": _lien(f"/quotes/{ident}/appendices"),
                "external_reference": f"boond:opportunity:{ident}",
                "created_at": _dt(jour, 10, 0, 0, ident),
                "updated_at": _dt(jour + timedelta(days=ident), 10, 0, 0, ident),
            }
        )
    return devis


def _abonnements(
    clients: list[dict[str, Any]], produits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deux abonnements de TMA — le cas « facturation récurrente »."""
    actifs = [c for c in clients if c["customer_type"] == "company"]
    abonnements = []
    for ident in (1, 2):
        client = actifs[ident]
        produit = produits[ident]
        debut = date(2026, 1, 1)
        abonnements.append(
            {
                "id": ident,
                "label": f"TMA {client['name']}",
                "status": "in_progress",
                "start": _d(debut),
                "finish": _d(date(2026, 12, 31)),
                "recurrence": "monthly",
                "next_invoice_date": _d(date(2026, 8, 1)),
                "currency": DEVISE,
                "customer": _ref(client["id"], "/customers"),
                "invoice_lines": _lien(f"/billing_subscriptions/{ident}/invoice_lines"),
                "invoice_line_sections": _lien(
                    f"/billing_subscriptions/{ident}/invoice_line_sections"
                ),
                "product": _ref(produit["id"], "/products"),
                "created_at": _dt(debut, 9, 0, 0, ident),
                "updated_at": _dt(debut, 9, 0, 0, ident),
            }
        )
    return abonnements


def _mandats(
    clients: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Mandats SEPA, GoCardless, Pro Account — et les candidats à migration."""
    actifs = [c for c in clients if c["customer_type"] == "company"][:4]
    sepa, gocardless, pro, migrations = [], [], [], []
    for ident, client in enumerate(actifs, start=1):
        signe = date(2025, 6 + ident % 6, 10)
        commun = {
            "id": ident,
            "customer": _ref(client["id"], "/customers"),
            "iban": f"FR7630004000{ident:04d}{'0' * 8}{ident:02d}",
            "signed_at": _d(signe),
            "created_at": _dt(signe, 9, 0, 0, ident),
            "updated_at": _dt(signe, 9, 0, 0, ident),
        }
        sepa.append({**commun, "rum": f"RUM-{ident:08d}", "status": "active", "scheme": "core"})
        if ident <= 2:
            gocardless.append(
                {
                    **commun,
                    "external_reference": f"MD{ident:06d}",
                    "status": "active" if ident == 1 else "pending_customer_approval",
                }
            )
        if ident == 1:
            pro.append({**commun, "status": "enabled", "rum": f"RUM-{ident:08d}"})
        else:
            migrations.append(
                {
                    "id": ident,
                    "customer": _ref(client["id"], "/customers"),
                    "status": "available",
                    "created_at": commun["created_at"],
                    "updated_at": commun["updated_at"],
                }
            )
    return sepa, gocardless, pro, migrations


def _documents_commerciaux(clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Les trois `document_type` documentés, un exemplaire chacun."""
    types = ("proforma", "shipping_order", "purchasing_order")
    actifs = [c for c in clients if c["customer_type"] == "company"]
    documents = []
    for ident, type_ in enumerate(types, start=1):
        client = actifs[ident]
        jour = date(2026, 3 + ident, 6)
        documents.append(
            {
                "id": ident,
                "label": f"{type_.replace('_', ' ').title()} {client['name']}",
                "document_type": type_,
                "document_number": f"DOC-2026-{ident:04d}",
                "currency": DEVISE,
                "date": _d(jour),
                "customer": _ref(client["id"], "/customers"),
                "invoice_lines": _lien(f"/commercial_documents/{ident}/invoice_lines"),
                "invoice_line_sections": _lien(
                    f"/commercial_documents/{ident}/invoice_line_sections"
                ),
                "appendices": _lien(f"/commercial_documents/{ident}/appendices"),
                "created_at": _dt(jour, 10, 30, 0, ident),
                "updated_at": _dt(jour, 10, 30, 0, ident),
            }
        )
    return documents


def _demandes_achat(fournisseurs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    demandes = []
    for ident, fournisseur in enumerate(fournisseurs[:3], start=1):
        jour = date(2026, 2 + ident, 11)
        demandes.append(
            {
                "id": ident,
                "label": f"Bon de commande {fournisseur['name']}",
                "reference": f"BC-2026-{ident:04d}",
                "status": "approved" if ident < 3 else "pending",
                "currency": DEVISE,
                "amount": _euros(120_000 * ident),
                "date": _d(jour),
                "supplier": _ref(fournisseur["id"], "/suppliers"),
                "user_id": 1,
                "reviewed_by_id": 1 if ident < 3 else None,
                "created_at": _dt(jour, 14, 0, 0, ident),
                "updated_at": _dt(jour, 14, 0, 0, ident),
            }
        )
    return demandes


def _exports() -> dict[str, list[dict[str, Any]]]:
    """Trois exports pré-existants — un par type.

    Le fournisseur les CRÉE par POST puis les sert par GET : le mock, en
    lecture seule, n'en sert que le second temps. Un export porte un `status`
    et une `url` de fichier ; le fichier lui-même n'est pas servi — il n'a
    aucun intérêt pour un connecteur qui n'en lira jamais le xlsx.
    """
    jour = date(2026, 7, 1)

    def _un(ident: int, genre: str) -> dict[str, Any]:
        return {
            "id": ident,
            "status": "completed",
            "period_start": _d(date(2026, 1, 1)),
            "period_end": _d(date(2026, 6, 30)),
            "file_url": f"https://files.example/exports/{genre}-{ident}.xlsx",
            "created_at": _dt(jour, 2, 0, 0, ident),
            "updated_at": _dt(jour, 2, 5, 0, ident),
        }

    return {
        "general_ledger_exports": [_un(1, "gl")],
        "analytical_general_ledger_exports": [_un(2, "agl")],
        "fec_exports": [{**_un(3, "fec"), "file_url": "https://files.example/exports/fec-3.txt"}],
    }


# ═════════════════════════════════════════════════════════════════════════════
#  Dérivés — balance, soldes bancaires, journal des changements
# ═════════════════════════════════════════════════════════════════════════════


def balance(
    lignes: list[dict[str, Any]],
    comptes: list[dict[str, Any]],
    *,
    debut: date,
    fin: date,
    auxiliaires: bool,
) -> list[dict[str, Any]]:
    """La balance générale : un cumul débit / crédit par compte, sur la période.

    Calculée À LA DEMANDE depuis les lignes d'écriture, jamais stockée. C'est
    ce qui rend impossible la seule incohérence qui compte : une balance qui
    ne correspond plus au grand livre dont elle est censée sortir.

    `is_auxiliary` : quand il est faux, les comptes auxiliaires (411XXXXX,
    401XXXXX) sont AGRÉGÉS dans leur racine — c'est le comportement du
    fournisseur, et un consommateur qui somme les deux vues double son actif.
    """
    par_id = {c["id"]: c for c in comptes}
    cumuls: dict[str, list[int]] = {}
    libelles: dict[str, str] = {}
    for ligne in lignes:
        jour = date.fromisoformat(ligne["date"])
        if jour < debut or jour > fin:
            continue
        compte = par_id[ligne["ledger_account"]["id"]]
        numero = compte["number"]
        if not auxiliaires and len(numero) > 6:
            racine = numero[:3] + "000"
            numero, libelle = racine, "Fournisseurs" if racine == "401000" else "Clients"
        else:
            libelle = compte["label"]
        entree = cumuls.setdefault(numero, [0, 0])
        libelles[numero] = libelle
        entree[0] += round(float(ligne["debit"]) * 100)
        entree[1] += round(float(ligne["credit"]) * 100)

    return [
        {
            "number": numero,
            # `formatted_number` : le numéro complété à huit caractères, forme
            # sur laquelle les logiciels comptables français s'alignent. Le
            # fournisseur sert les DEUX, et ils ne se déduisent pas l'un de
            # l'autre pour un auxiliaire (`411LUMIN` ne se complète pas de zéros).
            "formatted_number": numero.ljust(8, "0") if numero.isdigit() else numero,
            "label": libelles[numero],
            "debits": _euros(debit),
            "credits": _euros(credit),
        }
        for numero, (debit, credit) in sorted(cumuls.items())
    ]


def _recaler_soldes_bancaires(
    comptes_bancaires: list[dict[str, Any]],
    lignes: list[dict[str, Any]],
    comptes: list[dict[str, Any]],
) -> None:
    """Le solde d'un compte bancaire EST le solde de son compte 512.

    Sans ce recalage, la banque et la comptabilité se contrediraient dans le
    même jeu de données — et un tableau de bord de trésorerie construit sur
    l'une des deux sources donnerait un chiffre différent de l'autre, sans
    qu'aucun test ne le voie.
    """
    par_id = {c["id"]: c for c in comptes}
    for compte_bancaire in comptes_bancaires:
        numero = par_id[compte_bancaire["ledger_account"]["id"]]["number"]
        solde = sum(
            round(float(ligne["debit"]) * 100) - round(float(ligne["credit"]) * 100)
            for ligne in lignes
            if par_id[ligne["ledger_account"]["id"]]["number"] == numero
        )
        compte_bancaire["balance"] = _euros(solde)


#: Les sept familles pour lesquelles le fournisseur expose un changelog. Toute
#: autre ressource n'a PAS de journal de changements — un consommateur qui en
#: attendrait un pour les écritures se trompe : il n'y a que les LIGNES.
FAMILLES_CHANGELOG: tuple[str, ...] = (
    "customer_invoices",
    "supplier_invoices",
    "customers",
    "suppliers",
    "products",
    "ledger_entry_lines",
    "transactions",
)

#: Deux familles supplémentaires servies par l'API mais absentes du guide :
#: les changements de CATÉGORIES analytiques. Elles ont leur propre forme.
FAMILLES_CHANGELOG_CATEGORIES: tuple[str, ...] = (
    "ledger_entries_categories",
    "ledger_entry_lines_categories",
)

#: Les devis ont aussi un changelog (`/changelogs/quotes`), documenté dans la
#: référence mais pas dans le guide.
FAMILLES_CHANGELOG_AUTRES: tuple[str, ...] = ("quotes",)


def _journal_des_changements(donnees: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Un événement `insert` à la création, un `update` si la ligne a bougé.

    ┌─ CE QUE LE CHANGELOG N'EST PAS ─────────────────────────────────────────┐
    │ Ce n'est pas une copie de la ressource : un événement porte l'ID, le    │
    │ type d'opération et trois horodatages, RIEN d'autre. Il faut donc un    │
    │ second appel — `filter=[{"field":"id","operator":"in","value":[…]}]` —  │
    │ pour obtenir l'état. C'est le pattern que le fournisseur recommande, et │
    │ c'est celui que le connecteur d'insights360 doit exercer.               │
    │                                                                         │
    │ Ordre CHRONOLOGIQUE croissant (le plus ancien d'abord), et rétention de │
    │ quatre semaines : une `start_date` plus ancienne rend 422.              │
    └─────────────────────────────────────────────────────────────────────────┘
    """
    familles = {
        "customer_invoices": donnees["customer_invoices"],
        "supplier_invoices": donnees["supplier_invoices"],
        "customers": donnees["customers"],
        "suppliers": donnees["suppliers"],
        "products": donnees["products"],
        "ledger_entry_lines": donnees["ledger_entry_lines"],
        "transactions": donnees["transactions"],
        "quotes": donnees["quotes"],
    }
    journal: dict[str, list[dict[str, Any]]] = {}
    for nom, elements in familles.items():
        evenements: list[dict[str, Any]] = []
        for element in elements:
            evenements.append(
                {
                    "id": element["id"],
                    "operation": "insert",
                    "processed_at": element["created_at"],
                    "created_at": element["created_at"],
                    "updated_at": element["created_at"],
                }
            )
            if element["updated_at"] != element["created_at"]:
                evenements.append(
                    {
                        "id": element["id"],
                        "operation": "update",
                        "processed_at": element["updated_at"],
                        "created_at": element["created_at"],
                        "updated_at": element["updated_at"],
                    }
                )
        evenements.sort(key=lambda e: (e["processed_at"], e["id"]))
        journal[nom] = evenements

    # Les changements de catégories analytiques : un événement par écriture et
    # par ligne effectivement ventilée.
    journal["ledger_entries_categories"] = [
        {
            "id": e["id"],
            "operation": "update",
            "processed_at": e["updated_at"],
            "created_at": e["created_at"],
            "updated_at": e["updated_at"],
        }
        for e in donnees["ledger_entries"]
        if e["categories"]
    ]
    journal["ledger_entry_lines_categories"] = [
        {
            "id": ligne["id"],
            "operation": "update",
            "processed_at": ligne["updated_at"],
            "created_at": ligne["created_at"],
            "updated_at": ligne["updated_at"],
        }
        for ligne in donnees["ledger_entry_lines"]
        if ligne["categories"]
    ]
    for cle in ("ledger_entries_categories", "ledger_entry_lines_categories"):
        journal[cle].sort(key=lambda e: (e["processed_at"], e["id"]))
    return journal


# ═════════════════════════════════════════════════════════════════════════════
#  L'assemblage
# ═════════════════════════════════════════════════════════════════════════════


def build_realiste_dataset(seed: int = 42) -> dict[str, Any]:
    """Construit le monde comptable complet. Une seule voie d'entrée.

    L'ordre est PORTEUR : les référentiels d'abord (ils portent les identifiants
    auxquels tout le reste se réfère), puis les tiers, puis les flux, puis les
    dérivés — la balance et les soldes bancaires ne peuvent pas être calculés
    avant que la dernière écriture soit passée.
    """
    rng = random.Random(seed)

    journaux = _journaux()
    comptes = _comptes(rng)
    exercices = _exercices()
    groupes, categories = _axes_analytiques()
    etablissements = _etablissements_bancaires()
    comptes_bancaires = _comptes_bancaires(comptes, etablissements, journaux)

    clients = _clients(comptes, rng)
    contacts = _contacts(clients, rng)
    fournisseurs = _fournisseurs(comptes, rng)
    produits = _produits(rng, comptes)

    grand = Grand(journaux, comptes)

    # L'à-nouveau : le bilan d'ouverture de l'exercice. Sans lui, la banque
    # part de zéro et tombe en négatif dès la première paie — un jeu de
    # données où la trésorerie est absurde n'est pas un jeu réaliste.
    grand.passer(
        journal="AN",
        jour=date(2026, 1, 1),
        libelle="À-nouveaux exercice 2026",
        numero_piece="AN-2026",
        mouvements=[
            ("512000", 25_000_000, "Solde bancaire d'ouverture"),
            ("101000", -10_000_000, "Capital social"),
            ("110000", -15_000_000, "Report à nouveau"),
        ],
    )

    factures_clients, lignes_vente, reglements_clients = _ventes(
        grand, clients, produits, categories, rng
    )
    factures_fournisseurs, lignes_achat, reglements_fournisseurs = _achats(
        grand, fournisseurs, categories, rng
    )
    transactions, apparie_client, apparie_fournisseur = _banque(
        grand,
        comptes_bancaires,
        reglements_clients,
        reglements_fournisseurs,
        journaux,
        categories,
        rng,
    )

    _recaler_soldes_bancaires(comptes_bancaires, grand.lignes, comptes)

    devis = _devis(clients, produits)
    abonnements = _abonnements(clients, produits)
    sepa, gocardless, pro_mandats, migrations = _mandats(clients)
    documents = _documents_commerciaux(clients)
    demandes = _demandes_achat(fournisseurs)
    exports = _exports()

    donnees: dict[str, Any] = {
        # ── Référentiels ────────────────────────────────────────────────────
        "journals": journaux,
        "ledger_accounts": comptes,
        "fiscal_years": exercices,
        "category_groups": groupes,
        "categories": categories,
        "bank_establishments": etablissements,
        "bank_accounts": comptes_bancaires,
        # ── Tiers ───────────────────────────────────────────────────────────
        "customers": clients,
        "customer_contacts": contacts,
        "suppliers": fournisseurs,
        "products": produits,
        # ── Flux ────────────────────────────────────────────────────────────
        "customer_invoices": factures_clients,
        "customer_invoice_lines": lignes_vente,
        "supplier_invoices": factures_fournisseurs,
        "supplier_invoice_lines": lignes_achat,
        "transactions": transactions,
        # ── Comptabilité ────────────────────────────────────────────────────
        "ledger_entries": grand.ecritures,
        "ledger_entry_lines": grand.lignes,
        # ── Périphérie ──────────────────────────────────────────────────────
        "quotes": devis,
        "billing_subscriptions": abonnements,
        "sepa_mandates": sepa,
        "gocardless_mandates": gocardless,
        "pro_account_mandates": pro_mandats,
        "pro_account_mandate_migrations": migrations,
        "commercial_documents": documents,
        "purchase_requests": demandes,
        "customer_invoice_templates": [
            {
                "id": 1,
                "label": "Modèle standard Boréal Conseil",
                "created_at": _dt(date(2024, 1, 2), 8, 0, 0, 0),
                "updated_at": _dt(date(2024, 1, 2), 8, 0, 0, 0),
            }
        ],
        "pa_registrations": [
            {
                "id": 1,
                "status": "registered",
                "platform": "pennylane",
                "registered_at": _dt(date(2026, 1, 15), 9, 0, 0, 0),
                "created_at": _dt(date(2026, 1, 15), 9, 0, 0, 0),
                "updated_at": _dt(date(2026, 1, 15), 9, 0, 0, 0),
            }
        ],
        **exports,
        # ── Appariements (facture ↔ transaction), servis en sous-ressource ──
        "matched_transactions_par_facture_client": apparie_client,
        "matched_transactions_par_facture_fournisseur": apparie_fournisseur,
    }
    donnees["changelogs"] = _journal_des_changements(donnees)
    return donnees
