"""The dataset: the accounting of "Boréal Conseil" in Pennylane.

Same world as the four other mocks in the insights360 ecosystem — a French
IT consultancy (ESN) of 34 people, three agencies, domain
`boreal-conseil.example`, anchored on 15 July 2026, seed 42 — seen this time
through the ACCOUNTING SYSTEM. The same flows as in `boondmanager-mock`
appear here in their Pennylane form: client companies → sales invoices →
bank receipts, purchases → supplier invoices → disbursements, and the
double entry that records all of it.

┌─ WHAT IS SHARED WITH boondmanager-mock, AND WHAT IS NOT ────────────────────┐
│ SHARED (duplicated here, with no package dependency — none of the five      │
│ mocks depends on another, see README):                                      │
│   • the company names of the ten clients and three suppliers;               │
│   • the time anchor (15/07/2026), the seed (42), the VAT rate (20%);        │
│   • the format of sales references `FAC-2026-NNNN` / `AV-2026-NNNN`.        │
│                                                                             │
│ NOT SHARED, and it needs saying clearly: the AMOUNTS. Reproducing them      │
│ to the cent would mean replaying here the days x daily-rate matrix of       │
│ BoondManager missions — ~1500 lines of duplicated business logic, which     │
│ would diverge the moment either repo changes. This mock's amounts are       │
│ therefore its OWN, drawn from the same seed and the same order of           │
│ magnitude. A downstream test comparing revenue between the two mocks        │
│ is therefore comparing SETS OF CLIENTS and series of references, not euros. │
└─────────────────────────────────────────────────────────────────────────────┘

┌─ THE CENTRAL INVARIANT: THE BALANCE BALANCES ───────────────────────────────┐
│ Everything is built from the LEDGER ENTRIES. A sales invoice is not an      │
│ amount set beside a plausible entry: the entry IS the source, and the       │
│ invoice derives from it. Amounts are handled as WHOLE CENTS — never as      │
│ floats — then formatted as a two-decimal string, which is the v2 dialect.   │
│ This guarantees `sum(debit) == sum(credit)` down to the byte, and           │
│ `tests/test_coherence.py` checks it.                                        │
└─────────────────────────────────────────────────────────────────────────────┘

Determinism: `random.Random(seed)` and a FIXED time anchor. Never
`datetime.now()` — two runs produce the same dataset down to the same
byte, which is what makes downstream tests reproducible.
"""

from __future__ import annotations

import calendar
import random
from datetime import date, timedelta
from typing import Any

from ..settings import settings

# ── Time anchor ──────────────────────────────────────────────────────────────

#: Same as boondmanager-mock, linkedin-mock and ga-mock.
AUJOURDHUI = date(2026, 7, 15)
#: Ceiling for all `updated_at` in the BASE dataset. Evolution events are
#: STRICTLY later: an incremental cursor placed here must return zero rows
#: as long as the world hasn't moved.
DERNIERE_MAJ = date(2026, 7, 12)
#: First invoiced month of the current fiscal year.
DEBUT_FACTURATION = date(2026, 1, 1)

TVA = 20  # whole percentage — cents are computed without floats
TAUX_TVA = "FR_200"
DEVISE = "EUR"
PAYS = "FR"

BASE_URL = "https://app.pennylane.com/api/external/v2"


# ── Shape utilities ──────────────────────────────────────────────────────────


def _d(jour: date) -> str:
    return f"{jour:%Y-%m-%d}"


def _dt(jour: date, h: int = 9, mn: int = 0, s: int = 0, micro: int = 0) -> str:
    """`2026-08-30T10:08:08.146343Z` — the vendor's format.

    UTC with a trailing `Z` and SIX digits of microseconds, never a `+02:00`
    offset: that's what every OpenAPI example shows. A consumer that parses
    with `datetime.fromisoformat` before Python 3.11 breaks on the `Z` — one
    more reason not to "simplify" it into `+00:00`.
    """
    return f"{jour:%Y-%m-%d}T{h:02d}:{mn:02d}:{s:02d}.{micro:06d}Z"


def _maj(rng: random.Random, apres: date) -> str:
    """A plausible `updated_at`: after creation, never after the anchor."""
    if apres >= DERNIERE_MAJ:
        jour = DERNIERE_MAJ
    else:
        jour = apres + timedelta(days=rng.randint(0, (DERNIERE_MAJ - apres).days))
    return _dt(
        jour, rng.randint(8, 18), rng.randint(0, 59), rng.randint(0, 59), rng.randrange(10**6)
    )


def _euros(centimes: int) -> str:
    """Whole cents → the v2 dialect string.

    Amounts in the v2 API are STRINGS (`"230.32"`), never numbers.
    The error guide even lists "amounts not sent as strings" as a typical
    cause of 400s. A connector that receives a float here and tolerates it
    will break in production, when the real API sends the string.
    """
    signe = "-" if centimes < 0 else ""
    a = abs(centimes)
    return f"{signe}{a // 100}.{a % 100:02d}"


def _fin_mois(annee: int, mois: int) -> date:
    return date(annee, mois, calendar.monthrange(annee, mois)[1])


def _jours_ouvres(annee: int, mois: int) -> int:
    """Business days in the month (weekends excluded; public holidays are
    ignored — mock)."""
    dernier = calendar.monthrange(annee, mois)[1]
    return sum(1 for j in range(1, dernier + 1) if date(annee, mois, j).weekday() < 5)


def _lien(chemin: str) -> dict[str, str]:
    """A nested collection is a LINK, not an array.

    This is the most structural shape difference between v1 and v2 (migration
    guide): `{"invoice_lines": {"url": "…"}}` and not `{"invoice_lines":
    [...]}`. A consumer that iterates over it must make a second call, and that
    is exactly the behavior a mock must force on it.
    """
    return {"url": f"{BASE_URL}{chemin}"}


def _ref(ident: int, chemin: str) -> dict[str, Any]:
    return {"id": ident, "url": f"{BASE_URL}{chemin}/{ident}"}


# ── Catalogs — the same company names as boondmanager-mock ───────────────────

#: (company name, city, postal code, address, sector, prospect?)
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
    # Prospect at BoondManager: it exists as a Pennylane client (a record
    # was created) but carries NO invoice. This is a deliberate edge case —
    # a zero-euro client must still show up in listings.
    ("MediaQuartz", "Paris", "75011", "22 rue Oberkampf", "Télécoms & Médias", True),
)

#: (company name, city, postal code, address, expense account, purchase label)
_FOURNISSEURS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("Fivetech Partners", "Paris", "75008", "31 rue de Ponthieu", "604000", "Sous-traitance"),
    ("Softalliance", "Paris", "75010", "8 rue des Petites Écuries", "651600", "Licences"),
    ("Foncière Beaumont", "Paris", "75017", "40 rue de Courcelles", "613200", "Loyer"),
    ("Nordnet Télécom", "Lille", "59200", "2 avenue de la Marne", "626000", "Télécoms"),
    ("Bureau & Cie", "Nantes", "44100", "17 rue de la Convention", "606300", "Fournitures"),
)

#: Two INDIVIDUAL clients — the `customers` surface is a `oneOf` between
#: a legal entity and a private individual, and a connector that only handles
#: the first breaks on the second. They buy training (account 706100).
_PARTICULIERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("Camille", "Rousset", "Paris", "75012", "8 rue Crozatier"),
    ("Yanis", "Belkacem", "Lyon", "69006", "3 rue Duquesne"),
)

#: The chart of accounts served: (number, label, type, letterable, VAT rate).
#: Reduced to what the business actually moves — a full PCG would have 400
#: accounts with 390 sitting at zero, which tests nothing.
#: ┌─ `vat_rate` IS A CODE, NOT A PERCENTAGE ───────────────────────────────────┐
#: │ The mock used to serve "0.0" and "20.0". The vendor serves a rate CODE,    │
#: │ recorded from a real tenant on 2026-09-07: `any` (2,633 accounts),         │
#: │ `FR_200` (166), `exempt` (141), `extracom` (51), `crossborder` (40),       │
#: │ `FR_100` (19), `FR_55` (17), and up to `FR_15_385`.                        │
#: │                                                                             │
#: │ The difference isn't cosmetic: a consumer that cast the value to           │
#: │ numeric worked fine on the mock and broke in production on the first       │
#: │ `any` — "invalid input syntax for type numeric". And `any` is by far       │
#: │ the most common value, so the failure was certain, not probable.           │
#: │                                                                             │
#: │ `any` is the default here, just like at the vendor's: an account that      │
#: │ doesn't impose any particular rate. `exempt` on banking services           │
#: │ isn't decorative — they're VAT exempt.                                      │
#: └─────────────────────────────────────────────────────────────────────────────┘
_PLAN: tuple[tuple[str, str, str, bool, str], ...] = (
    ("101000", "Capital social", "equity", False, "any"),
    ("110000", "Report à nouveau", "equity", False, "any"),
    ("401000", "Fournisseurs", "supplier", True, "any"),
    ("411000", "Clients", "customer", True, "any"),
    ("445660", "TVA déductible sur autres biens et services", "tax", False, "FR_200"),
    ("471000", "Compte d'attente", "suspense", True, "any"),
    ("445710", "TVA collectée", "tax", False, "FR_200"),
    ("512000", "Banque — compte courant", "bank", True, "any"),
    ("512100", "Banque — compte de réserve", "bank", True, "any"),
    ("604000", "Achats d'études et prestations de services", "expense", False, "FR_200"),
    ("606300", "Fournitures d'entretien et petit équipement", "expense", False, "FR_200"),
    ("613200", "Locations immobilières", "expense", False, "FR_200"),
    ("626000", "Frais postaux et de télécommunications", "expense", False, "FR_200"),
    ("627000", "Services bancaires et assimilés", "expense", False, "exempt"),
    ("641100", "Salaires et appointements", "expense", False, "any"),
    ("645000", "Charges de sécurité sociale et de prévoyance", "expense", False, "any"),
    ("651600", "Droits d'auteur et de reproduction", "expense", False, "FR_100"),
    ("706000", "Prestations de services", "income", False, "FR_200"),
    ("706100", "Formations", "income", False, "exempt"),
)

#: (code, label) — the journals of a small ESN.
_JOURNAUX: tuple[tuple[str, str, str], ...] = (
    ("VE", "Journal des ventes", "sale"),
    ("AC", "Journal des achats", "purchase"),
    ("BQ", "Journal de banque", "bank"),
    ("OD", "Opérations diverses", "miscellaneous"),
    ("AN", "À-nouveaux", "new_year"),
    ("SA", "Journal de paie", "payroll"),
)

#: The analytical axes: agency and practice, exactly insights360's two
#: partitioning axes. One category group per axis.
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

#: (service label, daily rate in cents)
_PRESTATIONS: tuple[tuple[str, int], ...] = (
    ("Ingénierie data — consultant confirmé", 68_000),
    ("Ingénierie data — consultant senior", 78_000),
    ("Architecture cloud — senior", 82_000),
    ("Expertise MLOps", 85_000),
    ("Pilotage de programme", 92_000),
    ("Audit sécurité", 88_000),
    ("Formation Power BI (jour)", 145_000),
)

#: Banking establishments — two accounts, same as BoondManager.
_BANQUES: tuple[tuple[str, str, str], ...] = (
    ("Banque Hexagone Entreprises", "Compte courant", "512000"),
    ("Banque Hexagone Entreprises", "Compte de réserve", "512100"),
)


def _aux(prefixe: str, nom: str) -> str:
    """The number of a subsidiary account: `411LUMIN`, `401FIVET`.

    Classic French shape — general root + five letters of the third party.
    The vendor does NOT document a composition rule (every firm has its
    own); this one is plausible, not attested. See docs/UNVERIFIED-FIELDS.md.
    """
    lettres = "".join(c for c in nom.upper() if c.isalpha())[:5].ljust(5, "X")
    return f"{prefixe}{lettres}"


# ═════════════════════════════════════════════════════════════════════════════
#  Reference data — journals, chart of accounts, fiscal years, analytical axes
# ═════════════════════════════════════════════════════════════════════════════


def _journaux() -> list[dict[str, Any]]:
    """Six journals. `type` has NO enum in the official OpenAPI: the values
    below are plausible (usual French nomenclature), not attested — see
    docs/UNVERIFIED-FIELDS.md."""
    return [
        {"id": i, "code": code, "label": libelle, "type": type_}
        for i, (code, libelle, type_) in enumerate(_JOURNAUX, start=1)
    ]


def _comptes(rng: random.Random) -> list[dict[str, Any]]:
    """The general chart, then one subsidiary account per client and per supplier.

    Subsidiary accounts carry the SAME `type` as their root (`customer`,
    `supplier`): this is what lets a consumer group them without knowing
    the French numbering rule.
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
    del rng  # the chart of accounts has no randomness at all: it's decided, not drawn
    return comptes


def _compte_auxiliaire(ident: int, numero: str, libelle: str, type_: str) -> dict[str, Any]:
    return {
        "id": ident,
        "number": numero,
        "label": libelle,
        # `any` — this is the vendor's value on the overwhelming majority of
        # accounts, subsidiary accounts included: a third-party account doesn't impose a rate.
        "vat_rate": "any",
        "country_alpha2": PAYS,
        "enabled": True,
        "type": type_,
        "letterable": True,
        "created_at": _dt(date(2024, 1, 2), 8, 0, 0, 0),
        "updated_at": _dt(date(2024, 1, 2), 8, 0, 0, 0),
    }


def _exercices() -> list[dict[str, Any]]:
    """Three fiscal years: two closed, the one holding the anchor open.

    A CLOSED fiscal year and an OPEN one in the same dataset: this is what
    lets you test a consumer that extracts "the current fiscal year" without
    saying which one — it has to choose, and the choice has to show.
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
    """Two groups (Agency, Practice) and their categories.

    This is insights360's partitioning axis: a Pennylane category
    "Agency / Lyon" must hook onto the `agence` perimeter of the marts.
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
#  Third parties — clients (legal entities AND individuals) and suppliers
# ═════════════════════════════════════════════════════════════════════════════


def _clients(comptes: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """Ten legal entities, then two private individuals.

    The OpenAPI's `oneOf` has only TWO variants, distinguished by
    `customer_type` — and they don't share the same fields: the legal entity
    carries `name`, `reg_no`, `vat_number`; the individual carries
    `first_name`/`last_name` and NONE of the other three. A consumer that reads
    `reg_no` without checking `customer_type` gets a KeyError on the eleventh
    row, never on the first.
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
                # The payment term: 30 days by default, 45 days end of month
                # for the big accounts. This is what makes `deadline` diverge
                # from `date + 30`, and a consumer that recomputes the due
                # date instead of reading it gets those wrong.
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
    """A client's contacts, served on `/customers/{id}/contacts`.

    ⚠️ MINIMIZATION: these records carry personal identity. The mock serves
    them because the vendor serves them — but insights360's connector must
    only extract a whitelist of fields from them. See the note in the README.
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
    """The service catalog — the daily rate per profile.

    `price_before_tax` and `price` are strings, like all amounts.
    The second one IS the tax-included price: it isn't recomputed, it's read.
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
                # The balance is RECOMPUTED at the end of construction, once
                # all entries are posted: it must match the corresponding
                # 512 account's balance, or the bank and the accounting
                # contradict each other in the same dataset.
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
#  The general ledger — the SOURCE, from which everything else derives
# ═════════════════════════════════════════════════════════════════════════════


class Grand:
    """Entry accumulator. An unbalanced entry is a bug, not a piece of
    data: `passer()` checks it and raises.

    Amounts flow as WHOLE CENTS. It's the only way to get
    `sum(debit) == sum(credit)` exactly — with floats, the balance of a
    3000-line dataset ends up showing a two-cent gap that nobody can
    explain.
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
        """Posts an entry. `mouvements`: (account, signed cents, label).

        A POSITIVE amount is a DEBIT, a NEGATIVE amount a CREDIT. A single
        sign convention across the whole module: two conventions, and half
        the entries end up reversed with nothing to show it.
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
        """Reconciles the third-party lines of two entries — invoice ↔ payment.

        Reconciliation is what distinguishes a settled receivable from an
        open one, so it's what drives an invoice's `remaining_amount`. A mock
        that skipped it would serve an `outstanding_balance` that's always zero.
        """
        del numero  # the vendor doesn't expose the reconciliation code in v2
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
    """The analytical split of an entry: one category at weight 1.

    `weight` is a STRING (`"1.0"`), like every number in the dialect, and
    the sum of a line's weights equals 1 — this is what lets you split the
    same amount across two axes without counting it twice.
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
#  Sales — client invoices, lines, payments
# ═════════════════════════════════════════════════════════════════════════════

#: Home agency of each client, by id. Reuses the one from boondmanager-mock
#: (Lyon → agency 2, Nantes → 3, the rest → Paris).
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
    # A June invoice, issued in early July, due in 30 days: it isn't late
    # yet on 15 July. `upcoming` and not `late` — the distinction drives
    # an entire collections dashboard.
    del mois
    return "upcoming"


def _echeance(emission: date, conditions: str) -> date:
    """`deadline` per the client's payment conditions.

    The vendor SERVES the due date; it doesn't ask you to recompute it. It's
    reproduced here so the two values stay consistent — but a consumer must
    READ `deadline`, not derive it from `date`, or it gets the "end of
    month" clients wrong.
    """
    if conditions == "upon_receipt":
        return emission
    if conditions == "45_days_end_of_month":
        cible = emission + timedelta(days=45)
        return _fin_mois(cible.year, cible.month)
    return emission + timedelta(days=30)


def _ventes(  # noqa: PLR0915 — three sales families in a single pass
    grand: Grand,
    clients: list[dict[str, Any]],
    produits: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    """One invoice per active client and per elapsed month of 2026.

    January→May: paid. June: issued, unpaid. July: a draft for one client
    in four — a draft has NO final invoice number nor accounting entry, and
    it's a case every connector must encounter (`draft: true`, a provisional
    `invoice_number`, `status: "draft"`).
    """
    factures: list[dict[str, Any]] = []
    lignes_par_facture: dict[int, list[dict[str, Any]]] = {}
    reglements: list[dict[str, Any]] = []  # (invoice, date, entry) — for the bank
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

            # One to three consultants billed, stable for a given (client, month).
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
                        "id": 0,  # assigned below, once the invoice is known
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
                # The sales entry: the third party at debit, the product and
                # VAT at credit. THIS is what carries the amount; the invoice
                # derives from it, not the other way around.
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

    # ── The credit note ───────────────────────────────────────────────────────
    # Correction of an overbilling on the first paid invoice. A credit note is
    # an invoice with a NEGATIVE AMOUNT carrying `status: "credit_note"` and
    # `credited_invoice` — not an entity of a different type. A connector
    # that naively sums `amount` without checking the sign gets revenue wrong.
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

    # ── The two training sessions, billed to INDIVIDUALS ─────────────────────
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
#  Purchases — supplier invoices, lines, disbursements
# ═════════════════════════════════════════════════════════════════════════════

#: (supplier, label, amount excl. VAT in cents, monthly?, issue month if one-off)
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
    """Supplier invoices and their payments.

    Three distinct `accounting_status` values in the dataset — `complete`,
    `entry`, `validation_needed` — because this is the field a firm filters
    on, and a dataset where everything is `complete` proves nothing.
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
            # June invoices remain unpaid; earlier ones are settled.
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
#  The bank — transactions, reconciliations, payroll, fees
# ═════════════════════════════════════════════════════════════════════════════


def _banque(  # noqa: PLR0915, PLR0917 — the bank sees ALL flows pass through
    grand: Grand,
    comptes_bancaires: list[dict[str, Any]],
    reglements_clients: list[dict[str, Any]],
    reglements_fournisseurs: list[dict[str, Any]],
    journaux: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[int, list[int]], dict[int, list[int]]]:
    """Bank movements, and invoice ↔ transaction reconciliation.

    ┌─ THREE UNRECONCILED TRANSACTIONS, DELIBERATELY ────────────────────────┐
    │ A dataset where everything is reconciled proves nothing: it's exactly   │
    │ the ORPHAN transaction that does the work for a firm, and the           │
    │ nonzero `outstanding_balance` that must surface on a dashboard.         │
    │ Three client receipts are therefore left without a matched invoice.     │
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

    #: The last three client receipts remain UNRECONCILED.
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

    # ── Payroll, from month 1 to month 6 ─────────────────────────────────────
    # 34 employees. A single movement per month: gross pay + contributions at
    # debit, bank at credit. Payroll has no invoice — it's the only entry
    # family in the dataset with NO document, and a consumer that assumes
    # "one entry = one invoice" breaks on it.
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

    # ── Bank fees ─────────────────────────────────────────────────────────────
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

    # ── The internal transfer to the reserve account ─────────────────────────
    # Two bank accounts, hence a movement BETWEEN them: it's the only entry
    # in the dataset touching two 512 accounts, and it earns its place
    # because a naive reconciliation counts it twice in cash flow.
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
    """A bank statement line.

    `amount` is SIGNED: negative when the account is debited. `outstanding_balance`
    is what's left to reconcile — `null` when there's nothing to reconcile, `"0.0"`
    when everything is. The distinction between the two exists at the vendor's
    end, and a consumer that conflates them counts orphan transactions that
    aren't.
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
#  The periphery — quotes, subscriptions, mandates, documents, purchase requests
# ═════════════════════════════════════════════════════════════════════════════
#
# ┌─ GRADED FIDELITY, AND THAT'S A DELIBERATE DECISION ────────────────────────┐
# │ The resources below exist at the vendor and are served — the requested     │
# │ scope is "the full v2 surface, read-only." But they don't carry the flow   │
# │ insights360 consumes (billing, banking, accounting), so their dataset      │
# │ is deliberately thin: a few consistent items, with the fields declared     │
# │ by the OpenAPI, without the double-entry machinery that carries the core.  │
# │                                                                            │
# │ What is NOT negotiable even here: the SHAPE. An empty list paginates       │
# │ like the others, an unknown resource returns the dialect's 404, and        │
# │ amounts stay strings. It's the shape that breaks a connector, not          │
# │ the volume.                                                                │
# └────────────────────────────────────────────────────────────────────────────┘


def _devis(clients: list[dict[str, Any]], produits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Five quotes covering the five documented statuses — `pending`, `accepted`,
    `denied`, `expired`, `invoiced`. A single-status dataset tests no filter."""
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
    """Two maintenance (TMA) subscriptions — the "recurring billing" case."""
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
    """SEPA, GoCardless, Pro Account mandates — and the migration candidates."""
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
    """The three documented `document_type` values, one instance each."""
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
    """Three pre-existing exports — one per type.

    The vendor CREATES them via POST then serves them via GET: the mock,
    being read-only, only serves the second step. An export carries a
    `status` and a file `url`; the file itself isn't served — it holds no
    interest for a connector that will never read the xlsx.
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
#  Derived data — trial balance, bank balances, changelog
# ═════════════════════════════════════════════════════════════════════════════


def balance(
    lignes: list[dict[str, Any]],
    comptes: list[dict[str, Any]],
    *,
    debut: date,
    fin: date,
    auxiliaires: bool,
) -> list[dict[str, Any]]:
    """The trial balance: a debit / credit total per account, over the period.

    Computed ON DEMAND from the entry lines, never stored. This is what makes
    the one inconsistency that matters impossible: a trial balance that no
    longer matches the ledger it's supposed to come from.

    `is_auxiliary`: when false, subsidiary accounts (411XXXXX, 401XXXXX) are
    AGGREGATED into their root — this is the vendor's behavior, and a
    consumer that sums both views doubles its assets.
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
            # `formatted_number`: the number padded to eight characters, the
            # shape French accounting software aligns on. The vendor serves
            # BOTH, and one can't be derived from the other for a subsidiary
            # account (`411LUMIN` doesn't get zero-padded).
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
    """A bank account's balance IS the balance of its 512 account.

    Without this recalibration, the bank and the accounting would contradict
    each other in the same dataset — and a cash-flow dashboard built on
    either source would give a different figure from the other, with no
    test ever catching it.
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


#: The seven families for which the vendor exposes a changelog. Any other
#: resource has NO changelog — a consumer that expected one for entries
#: would be wrong: there are only LINES.
FAMILLES_CHANGELOG: tuple[str, ...] = (
    "customer_invoices",
    "supplier_invoices",
    "customers",
    "suppliers",
    "products",
    "ledger_entry_lines",
    "transactions",
)

#: Two extra families served by the API but absent from the guide: changes
#: to analytical CATEGORIES. They have their own shape.
FAMILLES_CHANGELOG_CATEGORIES: tuple[str, ...] = (
    "ledger_entries_categories",
    "ledger_entry_lines_categories",
)

#: Quotes also have a changelog (`/changelogs/quotes`), documented in the
#: reference but not in the guide.
FAMILLES_CHANGELOG_AUTRES: tuple[str, ...] = ("quotes",)


def _journal_des_changements(donnees: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """An `insert` event at creation, an `update` if the line changed.

    ┌─ WHAT THE CHANGELOG IS NOT ─────────────────────────────────────────────┐
    │ This isn't a copy of the resource: an event carries the ID, the         │
    │ operation type and three timestamps, NOTHING else. A second call is    │
    │ needed — `filter=[{"field":"id","operator":"in","value":[…]}]` —         │
    │ to get the state. This is the pattern the vendor recommends, and       │
    │ the one insights360's connector must exercise.                          │
    │                                                                         │
    │ Ascending CHRONOLOGICAL order (oldest first), and a retention of        │
    │ four weeks: an older `start_date` returns 422.                          │
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

    # Analytical category changes: one event per entry and per line that
    # actually carries a split.
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
#  Assembly
# ═════════════════════════════════════════════════════════════════════════════


def build_realiste_dataset(seed: int = 42) -> dict[str, Any]:
    """Builds the complete accounting world. A single entry point.

    The order MATTERS: reference data first (it carries the ids everything
    else refers to), then third parties, then flows, then derived data —
    the trial balance and bank balances can't be computed before the last
    entry is posted.
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

    # The opening balance: the fiscal year's opening statement. Without it,
    # the bank starts from zero and goes negative on the very first payroll
    # run — a dataset where cash is absurd isn't a realistic dataset.
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
        # ── Reference data ───────────────────────────────────────────────────
        "journals": journaux,
        "ledger_accounts": comptes,
        "fiscal_years": exercices,
        "category_groups": groupes,
        "categories": categories,
        "bank_establishments": etablissements,
        "bank_accounts": comptes_bancaires,
        # ── Third parties ────────────────────────────────────────────────────
        "customers": clients,
        "customer_contacts": contacts,
        "suppliers": fournisseurs,
        "products": produits,
        # ── Flows ────────────────────────────────────────────────────────────
        "customer_invoices": factures_clients,
        "customer_invoice_lines": lignes_vente,
        "supplier_invoices": factures_fournisseurs,
        "supplier_invoice_lines": lignes_achat,
        "transactions": transactions,
        # ── Accounting ───────────────────────────────────────────────────────
        "ledger_entries": grand.ecritures,
        "ledger_entry_lines": grand.lignes,
        # ── Periphery ────────────────────────────────────────────────────────
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
        # ── Matches (invoice ↔ transaction), served as a sub-resource ────────
        "matched_transactions_par_facture_client": apparie_client,
        "matched_transactions_par_facture_fournisseur": apparie_fournisseur,
    }
    _gommer_champs_facultatifs(donnees)
    donnees["changelogs"] = _journal_des_changements(donnees)
    return donnees


#: (collection, field) — the OPTIONAL fields that the real tenant surveyed
#: never populated. See the box in `settings.optional_fields_served`.
CHAMPS_FACULTATIFS: tuple[tuple[str, str], ...] = (
    ("categories", "analytical_code"),
    ("customer_invoice_lines", "product"),
    ("supplier_invoice_lines", "ledger_account"),
)


def _gommer_champs_facultatifs(donnees: dict[str, Any]) -> None:
    """Sets optional fields to `null`, unless the environment asks for them.

    This is done on the BUILT dataset, not at generation time: the world
    stays coherent (a sales line KNOWS which product it comes from, and the
    amounts derive from that) — it's only what's SERVED that aligns with
    what the vendor actually serves.

    ⚠️ Set to `null`, don't DELETE the key: the OpenAPI declares all three
    fields, and the vendor does return them — as `null`. A mock that omitted
    them would diverge in the other direction, and would hide from the
    consumer that the key exists.
    """
    if settings.optional_fields_served:
        return

    def _elements(valeur: Any) -> list[dict[str, Any]]:
        """A collection's rows, whether it's a list or an index.

        Invoice LINES are grouped by invoice (`{id: [line, …]}`) rather than
        flat: treating them as a list would iterate over the keys, change
        nothing, and the scrubbing would look applied when it wasn't.
        """
        if isinstance(valeur, list):
            return [e for e in valeur if isinstance(e, dict)]
        if isinstance(valeur, dict):
            return [e for lot in valeur.values() if isinstance(lot, list) for e in lot]
        return []

    for collection, champ in CHAMPS_FACULTATIFS:
        for element in _elements(donnees.get(collection)):
            if champ in element:
                element[champ] = None

    # SPLITS carry a copy of their category's analytical code. Forgetting
    # them would leave the mock contradicting itself: the category without a
    # code, the split that targets it carrying one — and a consumer reading
    # the latter would stay none the wiser. They're nested inside their
    # carriers, not in a collection of their own.
    for collection in ("ledger_entries", "ledger_entry_lines", "transactions"):
        for porteur in donnees.get(collection, []):
            if not isinstance(porteur, dict):
                continue
            for ventilation in porteur.get("categories") or []:
                if isinstance(ventilation, dict) and "analytical_code" in ventilation:
                    ventilation["analytical_code"] = None
