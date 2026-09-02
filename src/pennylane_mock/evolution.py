"""L'évolution du jeu de données dans le temps — pour l'extraction incrémentale.

Le monde VIT : un événement scripté par intervalle (60 s par défaut). C'est ce
qui rend éprouvable la seule propriété qui compte pour un connecteur
incrémental — « une deuxième extraction ne recharge QUE ce qui a bougé » — et
qui permet de vérifier qu'il envoie réellement sa `start_date` au lieu de
recharger l'univers à chaque passage.

┌─ DÉTERMINISME ──────────────────────────────────────────────────────────────┐
│ L'événement k tire son aléa de `Random(f"{seed}:{k}")` et son horodatage     │
│ vaut TOUJOURS `EPOQUE + (k+1) x intervalle`. Deux exécutions du même mock,   │
│ avancées du même nombre d'événements, produisent le même monde — sans quoi   │
│ un test d'incrémentalité ne serait pas rejouable.                            │
│                                                                              │
│ `avancer()` est idempotent et protégé par un verrou : les handlers FastAPI   │
│ tournent dans un pool de threads, et deux requêtes simultanées feraient      │
│ sinon avancer la chronologie deux fois pour le même pas.                     │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ L'INVARIANT COMPTABLE TIENT AUSSI PENDANT L'ÉVOLUTION ─────────────────────┐
│ Chaque événement qui crée un flux passe une écriture ÉQUILIBRÉE. La balance  │
│ d'un mock qu'on a laissé tourner une heure doit toujours équilibrer — sinon  │
│ le mock finit par servir une comptabilité fausse, ce qui est pire que de ne  │
│ rien servir.                                                                 │
└──────────────────────────────────────────────────────────────────────────────┘

Pour figer le monde — le gate d'idempotence d'insights360 compare `raw` entre
deux exécutions et ne peut pas vivre avec un jeu qui bouge — poser
`PENNYLANE_MOCK_EVOLUTION_ENABLED=false`.
"""

from __future__ import annotations

import random
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .dataset.realiste import (
    TVA,
    Grand,
    _aux,
    _categorie,
    _d,
    _euros,
    _lien,
    _ref,
    _transaction,
)
from .settings import settings

#: L'origine de la chronologie. Postérieure à `DERNIERE_MAJ` du jeu de base
#: (2026-07-12) : un curseur posé sur le jeu de base rend zéro ligne, et le
#: PREMIER événement d'évolution est le premier changement qu'il verra.
EPOQUE = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)

#: Le cycle des événements. Six pas, puis on recommence — mais les entités
#: touchées, elles, avancent : le septième événement n'est pas le premier.
CYCLE: tuple[str, ...] = (
    "maj_facture_client",
    "reglement_client",
    "nouvelle_facture_client",
    "maj_client",
    "facture_fournisseur",
    "transaction_orpheline",
)


def _horodatage(quand: datetime) -> str:
    return quand.strftime("%Y-%m-%dT%H:%M:%S.") + f"{quand.microsecond:06d}Z"


class Evolution:
    """La chronologie. `avancer()` la fait progresser jusqu'à l'instant donné."""

    def __init__(self, seed: int, depart: float) -> None:
        self.seed = seed
        self.depart = depart
        self.rang = 0
        self.journal: list[dict[str, Any]] = []
        self._verrou = threading.Lock()

    # ── Progression ──────────────────────────────────────────────────────────

    def pas_attendus(self, maintenant: float) -> int:
        if not settings.evolution_enabled or settings.evolution_interval <= 0:
            return 0
        ecoule = max(0.0, maintenant - self.depart)
        return int(ecoule // settings.evolution_interval)

    def avancer(self, donnees: dict[str, Any], maintenant: float) -> bool:
        """Applique les événements dus. Rend True si le monde a bougé."""
        with self._verrou:
            cible = self.pas_attendus(maintenant)
            if cible <= self.rang:
                return False
            for k in range(self.rang, cible):
                self._appliquer(donnees, k)
            self.rang = cible
            return True

    def forcer(self, donnees: dict[str, Any], pas: int = 1) -> None:
        """Avance de `pas` événements, quelle que soit l'horloge — le levier
        de `/__admin/evolve`, pour un test qui ne veut pas manipuler le temps."""
        with self._verrou:
            for k in range(self.rang, self.rang + pas):
                self._appliquer(donnees, k)
            self.rang += pas

    # ── Les événements ───────────────────────────────────────────────────────

    def _appliquer(self, donnees: dict[str, Any], k: int) -> None:
        genre = CYCLE[k % len(CYCLE)]
        rng = random.Random(f"{self.seed}:{k}")
        quand = EPOQUE + timedelta(seconds=settings.evolution_interval * (k + 1))
        horodatage = _horodatage(quand)
        jour = quand.date()

        applique = getattr(self, f"_evt_{genre}")
        detail = applique(donnees, rng, horodatage, jour)
        self.journal.append({"rang": k, "genre": genre, "at": horodatage, "detail": detail})

    # -- Un simple `updated_at` qui bouge : le cas le plus fréquent en réel ---

    def _evt_maj_facture_client(
        self, donnees: dict[str, Any], rng: random.Random, horodatage: str, jour: date
    ) -> str:
        del jour
        candidates = [f for f in donnees["customer_invoices"] if not f["draft"]]
        facture = candidates[rng.randrange(len(candidates))]
        facture["updated_at"] = horodatage
        facture["pdf_description"] = "Mention de relance ajoutée."
        _changement(donnees, "customer_invoices", facture, "update", horodatage)
        return f"customer_invoice:{facture['id']}"

    # -- Un encaissement : la facture passe à `paid`, une transaction naît ----

    def _evt_reglement_client(
        self, donnees: dict[str, Any], rng: random.Random, horodatage: str, jour: date
    ) -> str:
        impayees = [
            f
            for f in donnees["customer_invoices"]
            if not f["paid"] and not f["draft"] and f["status"] != "credit_note"
        ]
        if not impayees:
            return "aucune facture impayée"
        facture = impayees[rng.randrange(len(impayees))]
        client = next(c for c in donnees["customers"] if c["id"] == facture["customer"]["id"])
        ttc = round(float(facture["amount"]) * 100)
        grand = _grand_courant(donnees)
        repere = len(grand.lignes)
        compte_tiers = _compte_client(client)
        libelle = f"VIR SEPA {client['name'].upper()} {facture['invoice_number']}"
        ecriture = grand.passer(
            journal="BQ",
            jour=jour,
            libelle=libelle,
            numero_piece=f"BQ-EVO-{ecriture_suivante(donnees):04d}",
            mouvements=[("512000", ttc, libelle), (compte_tiers, -ttc, "Règlement")],
            maj=horodatage,
        )
        _absorber(donnees, grand, horodatage, repere)
        if facture["ledger_entry"]:
            grand.lettrer(facture["ledger_entry"]["id"], ecriture["id"], "")

        facture["paid"] = True
        facture["status"] = "paid"
        facture["remaining_amount_with_tax"] = "0.00"
        facture["remaining_amount_without_tax"] = "0.00"
        facture["updated_at"] = horodatage
        _changement(donnees, "customer_invoices", facture, "update", horodatage)

        transaction = _transaction(
            _prochain_id(donnees["transactions"]),
            jour=jour,
            libelle=libelle,
            montant=ttc,
            compte=donnees["bank_accounts"][0],
            journal=next(j for j in donnees["journals"] if j["code"] == "BQ"),
            tiers_client=_ref(client["id"], "/customers"),
            tiers_fournisseur=None,
            reste=0,
            categories=[],
            rng=rng,
        )
        transaction["created_at"] = transaction["updated_at"] = horodatage
        donnees["transactions"].append(transaction)
        donnees["matched_transactions_par_facture_client"].setdefault(facture["id"], []).append(
            transaction["id"]
        )
        _changement(donnees, "transactions", transaction, "insert", horodatage)
        _recaler_solde(donnees)
        return f"customer_invoice:{facture['id']} → paid"

    # -- Une nouvelle facture : le cas qu'un curseur DOIT rapporter -----------

    def _evt_nouvelle_facture_client(
        self, donnees: dict[str, Any], rng: random.Random, horodatage: str, jour: date
    ) -> str:
        clients = [
            c
            for c in donnees["customers"]
            if c["customer_type"] == "company" and c["notes"] is None
        ]
        client = clients[rng.randrange(len(clients))]
        produit = donnees["products"][rng.randrange(len(donnees["products"]))]
        quantite = rng.randint(3, 12)
        pu = round(float(produit["price_before_tax"]) * 100)
        ht = pu * quantite
        tva = ht * TVA // 100
        ttc = ht + tva
        ident = _prochain_id(donnees["customer_invoices"])
        numero = f"FAC-2026-{ident:04d}"

        grand = _grand_courant(donnees)
        repere = len(grand.lignes)
        ecriture = grand.passer(
            journal="VE",
            jour=jour,
            libelle=f"Facture {numero} — {client['name']}",
            numero_piece=numero,
            numero_facture=numero,
            echeance=jour + timedelta(days=30),
            categories=_categorie(donnees["categories"], "Paris"),
            mouvements=[
                (_compte_client(client), ttc, f"{client['name']} — {numero}"),
                ("706000", -ht, "Prestations"),
                ("445710", -tva, f"TVA collectée {TVA}%"),
            ],
            maj=horodatage,
        )
        _absorber(donnees, grand, horodatage, repere)

        modele = donnees["customer_invoices"][0]
        facture = {
            **modele,
            "id": ident,
            "label": f"Prestations complémentaires — {produit['label']}",
            "invoice_number": numero,
            "amount": _euros(ttc),
            "currency_amount": _euros(ttc),
            "currency_amount_before_tax": _euros(ht),
            "currency_tax": _euros(tva),
            "tax": _euros(tva),
            "date": _d(jour),
            "deadline": _d(jour + timedelta(days=30)),
            "paid": False,
            "status": "upcoming",
            "draft": False,
            "ledger_entry": {"id": ecriture["id"]},
            "remaining_amount_with_tax": _euros(ttc),
            "remaining_amount_without_tax": _euros(ht),
            "customer": _ref(client["id"], "/customers"),
            "credited_invoice": None,
            "filename": f"{numero}.pdf",
            "public_file_url": f"https://files.example/{numero}.pdf",
            "external_reference": f"evolution:invoice:{ident}",
            "created_at": horodatage,
            "updated_at": horodatage,
            **{
                cle: _lien(f"/customer_invoices/{ident}/{cle}")
                for cle in (
                    "invoice_line_sections",
                    "invoice_lines",
                    "custom_header_fields",
                    "categories",
                    "payments",
                    "matched_transactions",
                    "appendices",
                )
            },
        }
        donnees["customer_invoices"].append(facture)
        donnees["customer_invoice_lines"][ident] = [
            {
                "id": ident * 100 + 1,
                "label": produit["label"],
                "unit": "jour",
                "quantity": str(quantite),
                "amount": _euros(ttc),
                "currency_amount": _euros(ttc),
                "description": produit["description"],
                "product": _ref(produit["id"], "/products"),
                "vat_rate": produit["vat_rate"],
                "currency_amount_before_tax": _euros(ht),
                "currency_tax": _euros(tva),
                "tax": _euros(tva),
                "raw_currency_unit_price": _euros(pu),
                "discount": {"type": "relative", "value": "0"},
                "section_rank": 1,
                "imputation_dates": {"start_date": _d(jour), "end_date": _d(jour)},
                "created_at": horodatage,
                "updated_at": horodatage,
            }
        ]
        _changement(donnees, "customer_invoices", facture, "insert", horodatage)
        return f"customer_invoice:{ident} créée"

    def _evt_maj_client(
        self, donnees: dict[str, Any], rng: random.Random, horodatage: str, jour: date
    ) -> str:
        del jour
        client = donnees["customers"][rng.randrange(len(donnees["customers"]))]
        client["phone"] = f"+331{rng.randint(10_000_000, 99_999_999)}"
        client["updated_at"] = horodatage
        _changement(donnees, "customers", client, "update", horodatage)
        return f"customer:{client['id']}"

    def _evt_facture_fournisseur(
        self, donnees: dict[str, Any], rng: random.Random, horodatage: str, jour: date
    ) -> str:
        fournisseur = donnees["suppliers"][rng.randrange(len(donnees["suppliers"]))]
        compte_charge = dict((nom, compte) for nom, _, _, _, compte, _ in _CHARGES_FOURNISSEUR)[
            fournisseur["name"]
        ]
        ht = rng.randrange(20_000, 400_000, 1_000)
        tva = ht * TVA // 100
        ttc = ht + tva
        ident = _prochain_id(donnees["supplier_invoices"])
        numero = f"{fournisseur['name'][:3].upper()}-2026-EVO{ident:03d}"

        grand = _grand_courant(donnees)
        repere = len(grand.lignes)
        ecriture = grand.passer(
            journal="AC",
            jour=jour,
            libelle=f"{fournisseur['name']} — facture {numero}",
            numero_piece=numero,
            numero_facture=numero,
            echeance=jour + timedelta(days=30),
            statut="validation_needed",
            mouvements=[
                (compte_charge, ht, "Achat"),
                ("445660", tva, f"TVA déductible {TVA}%"),
                (_aux("401", fournisseur["name"]), -ttc, f"{fournisseur['name']} — {numero}"),
            ],
            maj=horodatage,
        )
        _absorber(donnees, grand, horodatage, repere)

        modele = donnees["supplier_invoices"][0]
        facture = {
            **modele,
            "id": ident,
            "label": "Achat complémentaire",
            "invoice_number": numero,
            "amount": _euros(ttc),
            "currency_amount": _euros(ttc),
            "currency_amount_before_tax": _euros(ht),
            "currency_tax": _euros(tva),
            "tax": _euros(tva),
            "date": _d(jour),
            "deadline": _d(jour + timedelta(days=30)),
            "reconciled": False,
            "accounting_status": "validation_needed",
            "payment_status": "to_be_processed",
            "paid": False,
            "remaining_amount_with_tax": _euros(ttc),
            "remaining_amount_without_tax": _euros(ht),
            "ledger_entry": {"id": ecriture["id"]},
            "supplier": _ref(fournisseur["id"], "/suppliers"),
            "filename": f"{numero}.pdf",
            "public_file_url": f"https://files.example/{numero}.pdf",
            "external_reference": f"evolution:purchase:{ident}",
            "invoice_lines": _lien(f"/supplier_invoices/{ident}/invoice_lines"),
            "categories": _lien(f"/supplier_invoices/{ident}/categories"),
            "payments": _lien(f"/supplier_invoices/{ident}/payments"),
            "matched_transactions": _lien(f"/supplier_invoices/{ident}/matched_transactions"),
            "created_at": horodatage,
            "updated_at": horodatage,
        }
        donnees["supplier_invoices"].append(facture)
        donnees["supplier_invoice_lines"][ident] = [
            {
                "id": ident * 100 + 1,
                "label": "Achat complémentaire",
                "quantity": "1",
                "unit": "forfait",
                "amount": _euros(ttc),
                "currency_amount": _euros(ttc),
                "currency_amount_before_tax": _euros(ht),
                "currency_tax": _euros(tva),
                "tax": _euros(tva),
                "vat_rate": "FR_200",
                "raw_currency_unit_price": _euros(ht),
                "description": "Ligne unique.",
                "ledger_account": {"id": grand.compte(compte_charge)["id"]},
                "created_at": horodatage,
                "updated_at": horodatage,
            }
        ]
        _changement(donnees, "supplier_invoices", facture, "insert", horodatage)
        return f"supplier_invoice:{ident} créée"

    def _evt_transaction_orpheline(
        self, donnees: dict[str, Any], rng: random.Random, horodatage: str, jour: date
    ) -> str:
        """Un encaissement SANS facture — le travail réel d'un cabinet.

        Il déséquilibrerait la comptabilité s'il n'était pas passé : la
        contrepartie va donc en compte d'attente 471, ce que fait tout
        comptable devant un mouvement non identifié.
        """
        montant = rng.randrange(15_000, 90_000, 500)
        grand = _grand_courant(donnees)
        repere = len(grand.lignes)
        grand.passer(
            journal="BQ",
            jour=jour,
            libelle="Encaissement non identifié",
            numero_piece=f"BQ-ATT-{_prochain_id(donnees['transactions']):04d}",
            statut="waiting_details",
            mouvements=[
                ("512000", montant, "Encaissement non identifié"),
                ("471000", -montant, "Compte d'attente"),
            ],
            maj=horodatage,
        )
        _absorber(donnees, grand, horodatage, repere)
        transaction = _transaction(
            _prochain_id(donnees["transactions"]),
            jour=jour,
            libelle="VIR RECU TIERS NON IDENTIFIE",
            montant=montant,
            compte=donnees["bank_accounts"][0],
            journal=next(j for j in donnees["journals"] if j["code"] == "BQ"),
            tiers_client=None,
            tiers_fournisseur=None,
            reste=montant,
            categories=[],
            rng=rng,
        )
        transaction["created_at"] = transaction["updated_at"] = horodatage
        donnees["transactions"].append(transaction)
        _changement(donnees, "transactions", transaction, "insert", horodatage)
        _recaler_solde(donnees)
        return f"transaction:{transaction['id']} orpheline"


# ── Utilitaires partagés par les événements ──────────────────────────────────

#: Le compte de charge de chaque fournisseur — recopié du catalogue du dataset
#: pour éviter un import circulaire au chargement du module.
_CHARGES_FOURNISSEUR: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("Fivetech Partners", "", "", "", "604000", ""),
    ("Softalliance", "", "", "", "651600", ""),
    ("Foncière Beaumont", "", "", "", "613200", ""),
    ("Nordnet Télécom", "", "", "", "626000", ""),
    ("Bureau & Cie", "", "", "", "606300", ""),
)


def _prochain_id(elements: list[dict[str, Any]]) -> int:
    return max((e["id"] for e in elements), default=0) + 1


def ecriture_suivante(donnees: dict[str, Any]) -> int:
    return _prochain_id(donnees["ledger_entries"])


def _compte_client(client: dict[str, Any]) -> str:
    if client["customer_type"] == "company":
        return _aux("411", client["name"])
    return _aux("411", f"{client['last_name']}{client['first_name']}")


def _grand_courant(donnees: dict[str, Any]) -> Grand:
    """Un accumulateur repositionné sur l'état courant du grand livre.

    Il porte les MÊMES listes que le dataset (pas des copies) : ce qu'il passe
    atterrit directement dans `ledger_entries` / `ledger_entry_lines`, et il
    n'y a donc aucune fenêtre où les deux divergeraient.
    """
    grand = Grand(donnees["journals"], donnees["ledger_accounts"])
    grand.ecritures = donnees["ledger_entries"]
    grand.lignes = donnees["ledger_entry_lines"]
    grand._id_ecriture = _prochain_id(donnees["ledger_entries"])
    grand._id_ligne = _prochain_id(donnees["ledger_entry_lines"])
    return grand


def _absorber(donnees: dict[str, Any], grand: Grand, horodatage: str, repere: int) -> None:
    """Inscrit au changelog les lignes d'écriture créées depuis `repere`.

    Le repère est une POSITION dans la liste, relevée avant l'appel à
    `passer()`. C'est la seule façon fiable de désigner « ce qui vient d'être
    ajouté » : filtrer sur l'horodatage raterait deux écritures passées dans
    la même seconde, ce qui arrive dès qu'un test force plusieurs pas d'un coup.
    """
    for ligne in grand.lignes[repere:]:
        ligne["created_at"] = ligne["updated_at"] = horodatage
        _changement(donnees, "ledger_entry_lines", ligne, "insert", horodatage)


def _changement(
    donnees: dict[str, Any],
    famille: str,
    element: dict[str, Any],
    operation: str,
    horodatage: str,
) -> None:
    donnees["changelogs"].setdefault(famille, []).append(
        {
            "id": element["id"],
            "operation": operation,
            "processed_at": horodatage,
            "created_at": element["created_at"],
            "updated_at": element["updated_at"],
        }
    )


def _recaler_solde(donnees: dict[str, Any]) -> None:
    from .dataset.realiste import _recaler_soldes_bancaires

    _recaler_soldes_bancaires(
        donnees["bank_accounts"], donnees["ledger_entry_lines"], donnees["ledger_accounts"]
    )
