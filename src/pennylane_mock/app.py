"""Assemblage de l'application FastAPI.

┌─ UN SEUL PIPELINE DE REQUÊTE ───────────────────────────────────────────────┐
│ Toute route de la surface fournisseur passe par `_prelude` :                 │
│                                                                              │
│     évolution → observation → injections → jeton → scope → handler           │
│                                                                              │
│ Les pannes sont dispatchées AVANT l'authentification, pour qu'un             │
│ `auth_reject` puisse la préempter. Il ne peut donc pas exister de route      │
│ « oubliée » où les pannes ne s'appliqueraient pas, ni où le scope ne serait  │
│ pas vérifié — c'est ce que garantit la fabrique de routes plutôt qu'une      │
│ discipline de relecture.                                                     │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ UNE TABLE DÉCLARATIVE, PAS 91 HANDLERS ────────────────────────────────────┐
│ La surface v2 en lecture compte 91 opérations GET. Les écrire à la main      │
│ produirait 91 occasions d'oublier un scope, un tri par défaut ou une         │
│ enveloppe de pagination. `RESSOURCES` et `SOUS_RESSOURCES` les décrivent ;   │
│ la fabrique les monte. Ajouter une ressource, c'est une ligne de table plus  │
│ une clé dans le jeu de données.                                              │
└──────────────────────────────────────────────────────────────────────────────┘

Les écritures (POST/PUT/DELETE) sont HORS PÉRIMÈTRE : le consommateur de ce
mock lit, il n'écrit pas. Elles rendent 404 au dialecte Pennylane — le
fournisseur, lui, les servirait ; l'écart est inscrit dans docs/EXTRACTION.md.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import changelog as chg
from .auth import jeton_de_l_entete, jeton_est_valide, scope_accorde
from .errors import (
    entetes_debit,
    erreur,
    erreur_debit,
    erreur_introuvable,
    erreur_jeton,
    erreur_scope,
)
from .filtres import (
    FiltreInvalide,
    TriInvalide,
    analyser_filtre,
    appliquer_filtre,
    appliquer_tri,
)
from .injection import engine
from .models import (
    REPONSES_ERREUR,
    Categorie,
    CompteBancaire,
    ComptePlan,
    Contact,
    Ecriture,
    ElementGenerique,
    EtablissementBancaire,
    EvenementChangelog,
    Exercice,
    FactureClient,
    FactureFournisseur,
    Fournisseur,
    GroupeCategories,
    Journal,
    LigneBalance,
    LigneEcriture,
    LigneFacture,
    Page,
    Produit,
    ProfilUtilisateur,
    Reglement,
    TiersClient,
    Transaction,
)
from .pagination import CurseurInvalide, LimiteInvalide, limite_demandee, paginer
from .settings import settings
from .state import state

PREFIXE = "/api/external/v2"
VERSION = "0.1.1"


# ═════════════════════════════════════════════════════════════════════════════
#  La table des ressources
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RessourceSpec:
    """Ce qu'il faut savoir d'une ressource pour la servir ET la documenter."""

    chemin: str  # segment d'URL sous /api/external/v2
    cle: str  # clé dans le jeu de données
    modele: type  # modèle pydantic de l'élément — la source du contrat
    scope: str | None  # scope requis ; None = aucun
    singulier: str  # nom d'entité au singulier (documentation)
    avec_liste: bool = True
    avec_detail: bool = True
    tri_defaut: str = "-id"
    #: Les listes qui n'acceptent PAS `filter` chez le fournisseur. Le mock
    #: l'ignore alors silencieusement, comme lui — refuser serait plus sévère
    #: que le réel, et un consommateur calerait ici sans caler en production.
    filtrable: bool = True


#: Les scopes viennent de l'OpenAPI officiel, opération par opération. Un
#: endpoint documenté « one of x:readonly, x:all » se déclare avec la variante
#: readonly : `auth.scope_accorde` accepte `x:all` par-dessus.
RESSOURCES: tuple[RessourceSpec, ...] = (
    # ── Comptabilité ────────────────────────────────────────────────────────
    RessourceSpec("journals", "journals", Journal, "journals:readonly", "journal"),
    RessourceSpec(
        "ledger_accounts",
        "ledger_accounts",
        ComptePlan,
        "ledger_accounts:readonly",
        "ledgerAccount",
    ),
    RessourceSpec(
        "ledger_entries",
        "ledger_entries",
        Ecriture,
        "ledger_entries:readonly",
        "ledgerEntry",
    ),
    RessourceSpec(
        "ledger_entry_lines",
        "ledger_entry_lines",
        LigneEcriture,
        "ledger_entries:readonly",
        "ledgerEntryLine",
    ),
    RessourceSpec(
        "fiscal_years",
        "fiscal_years",
        Exercice,
        "fiscal_years:readonly",
        "fiscalYear",
        avec_detail=False,
        filtrable=False,
    ),
    # ── Analytique ──────────────────────────────────────────────────────────
    RessourceSpec("categories", "categories", Categorie, "categories:readonly", "category"),
    RessourceSpec(
        "category_groups",
        "category_groups",
        GroupeCategories,
        "categories:readonly",
        "categoryGroup",
        filtrable=False,
    ),
    # ── Tiers ───────────────────────────────────────────────────────────────
    RessourceSpec("customers", "customers", TiersClient, "customers:readonly", "customer"),
    RessourceSpec("suppliers", "suppliers", Fournisseur, "suppliers:readonly", "supplier"),
    RessourceSpec("products", "products", Produit, "products:readonly", "product"),
    # ── Facturation ─────────────────────────────────────────────────────────
    RessourceSpec(
        "customer_invoices",
        "customer_invoices",
        FactureClient,
        "customer_invoices:readonly",
        "customerInvoice",
    ),
    RessourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        FactureFournisseur,
        "supplier_invoices:readonly",
        "supplierInvoice",
    ),
    RessourceSpec(
        "customer_invoice_templates",
        "customer_invoice_templates",
        ElementGenerique,
        "customer_invoice_templates:readonly",
        "customerInvoiceTemplate",
        avec_detail=False,
        filtrable=False,
    ),
    RessourceSpec("quotes", "quotes", ElementGenerique, "quotes:readonly", "quote"),
    RessourceSpec(
        "commercial_documents",
        "commercial_documents",
        ElementGenerique,
        "commercial_documents:readonly",
        "commercialDocument",
    ),
    RessourceSpec(
        "billing_subscriptions",
        "billing_subscriptions",
        ElementGenerique,
        "billing_subscriptions:readonly",
        "billingSubscription",
    ),
    RessourceSpec(
        "purchase_requests",
        "purchase_requests",
        ElementGenerique,
        "purchase_requests:readonly",
        "purchaseRequest",
    ),
    # ── Banque ──────────────────────────────────────────────────────────────
    RessourceSpec(
        "bank_accounts",
        "bank_accounts",
        CompteBancaire,
        "bank_accounts:readonly",
        "bankAccount",
        filtrable=False,
    ),
    RessourceSpec(
        "bank_establishments",
        "bank_establishments",
        EtablissementBancaire,
        "bank_establishments:readonly",
        "bankEstablishment",
        avec_detail=False,
    ),
    RessourceSpec(
        "transactions",
        "transactions",
        Transaction,
        "transactions:readonly",
        "transaction",
    ),
    # ── Mandats ─────────────────────────────────────────────────────────────
    RessourceSpec(
        "sepa_mandates",
        "sepa_mandates",
        ElementGenerique,
        "customer_mandates:readonly",
        "sepaMandate",
    ),
    RessourceSpec(
        "gocardless_mandates",
        "gocardless_mandates",
        ElementGenerique,
        "customer_mandates:readonly",
        "gocardlessMandate",
    ),
    RessourceSpec(
        "pro_account/mandates",
        "pro_account_mandates",
        ElementGenerique,
        "customer_mandates:readonly",
        "proAccountMandate",
        avec_detail=False,
    ),
    RessourceSpec(
        "pro_account/mandate_migrations",
        "pro_account_mandate_migrations",
        ElementGenerique,
        "customer_mandates:readonly",
        "proAccountMandateMigration",
        avec_detail=False,
    ),
)


@dataclass(frozen=True)
class SousRessourceSpec:
    """Une collection accessible SOUS un élément — `/customer_invoices/{id}/payments`.

    Ces routes existent parce que la v2 sert des LIENS et non des tableaux
    imbriqués : sans elles, une facture ne donnerait jamais accès à ses lignes.
    """

    parent: str  # segment du parent
    parent_cle: str  # clé du parent dans le jeu de données
    chemin: str  # segment de la sous-collection
    modele: type
    scope: str | None
    #: (dataset, élément parent) → la liste à paginer.
    resoudre: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]
    tri_defaut: str = "-id"
    parametres: tuple[str, ...] = field(default_factory=lambda: ("cursor", "limit"))


def _sous(cle: str) -> Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]:
    """Résolveur pour les collections indexées par identifiant de parent."""

    def resoudre(donnees: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
        return list(donnees[cle].get(parent["id"], []))

    return resoudre


def _vide(_donnees: dict[str, Any], _parent: dict[str, Any]) -> list[dict[str, Any]]:
    """Une collection systématiquement vide, mais SERVIE.

    Annexes, sections de lignes, champs d'en-tête personnalisés, fichiers GED :
    Boréal Conseil n'en a aucun. La route existe quand même, et rend une page
    vide bien formée — parce que c'est exactement ce qu'un connecteur doit
    savoir traiter, et que la faire répondre 404 apprendrait le contraire.
    """
    return []


def _lignes_ecriture(donnees: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ligne
        for ligne in donnees["ledger_entry_lines"]
        if ligne["ledger_entry"]["id"] == parent["id"]
    ]


def _lignes_lettrees(donnees: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    identifiants = set(parent["lettered_ledger_entry_lines"]["ids"])
    return [ligne for ligne in donnees["ledger_entry_lines"] if ligne["id"] in identifiants]


def _categories_de(donnees: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    """Les catégories analytiques portées par l'élément.

    Une facture n'en porte pas directement : elle les tient de son écriture.
    C'est ce chaînage que le fournisseur expose, et qu'un consommateur qui
    lirait `facture["categories"]` comme un tableau ne verrait jamais.
    """
    if "categories" in parent and isinstance(parent["categories"], list):
        return list(parent["categories"])
    reference = parent.get("ledger_entry")
    if not reference:
        return []
    ecriture = next((e for e in donnees["ledger_entries"] if e["id"] == reference["id"]), None)
    return list(ecriture["categories"]) if ecriture else []


def _transactions_appariees(cle_index: str) -> Callable[..., list[dict[str, Any]]]:
    def resoudre(donnees: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
        identifiants = set(donnees[cle_index].get(parent["id"], []))
        return [t for t in donnees["transactions"] if t["id"] in identifiants]

    return resoudre


def _factures_appariees(donnees: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    """Les factures — clientes ET fournisseurs — rapprochées d'une transaction."""
    resultat: list[dict[str, Any]] = []
    for index, collection in (
        ("matched_transactions_par_facture_client", "customer_invoices"),
        ("matched_transactions_par_facture_fournisseur", "supplier_invoices"),
    ):
        for facture_id, transactions in donnees[index].items():
            if parent["id"] in transactions:
                facture = next((f for f in donnees[collection] if f["id"] == facture_id), None)
                if facture is not None:
                    resultat.append(facture)
    return resultat


def _reglements(collection: str) -> Callable[..., list[dict[str, Any]]]:
    """Les règlements enregistrés SUR la facture.

    ⚠️ Ce ne sont PAS les transactions rapprochées — le fournisseur consacre
    une page à la distinction. Une facture réglée porte un `payment` ; le
    mouvement bancaire correspondant est une `matched_transaction`. Les
    additionner compte l'encaissement deux fois.
    """

    def resoudre(donnees: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
        del donnees
        if not parent.get("paid"):
            return []
        return [
            {
                "id": parent["id"],
                "label": f"Règlement {parent['invoice_number']}",
                "currency": parent["currency"],
                "currency_amount": parent["amount"],
                "status": "settled",
                "created_at": parent["updated_at"],
                "updated_at": parent["updated_at"],
            }
        ]

    del collection
    return resoudre


SOUS_RESSOURCES: tuple[SousRessourceSpec, ...] = (
    # ── Facture client ──────────────────────────────────────────────────────
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "invoice_lines",
        LigneFacture,
        "customer_invoices:readonly",
        _sous("customer_invoice_lines"),
    ),
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "invoice_line_sections",
        ElementGenerique,
        "customer_invoices:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "payments",
        Reglement,
        "customer_invoices:readonly",
        _reglements("customer_invoices"),
    ),
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "matched_transactions",
        Transaction,
        "customer_invoices:readonly",
        _transactions_appariees("matched_transactions_par_facture_client"),
    ),
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "appendices",
        ElementGenerique,
        "customer_invoices:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "categories",
        Categorie,
        "customer_invoices:readonly",
        _categories_de,
    ),
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "custom_header_fields",
        ElementGenerique,
        "customer_invoices:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "customer_invoices",
        "customer_invoices",
        "installments",
        ElementGenerique,
        "customer_invoices:readonly",
        _vide,
        tri_defaut="deadline",
    ),
    # ── Facture fournisseur ─────────────────────────────────────────────────
    SousRessourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "invoice_lines",
        LigneFacture,
        "supplier_invoices:readonly",
        _sous("supplier_invoice_lines"),
    ),
    SousRessourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "categories",
        Categorie,
        "supplier_invoices:readonly",
        _categories_de,
    ),
    SousRessourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "payments",
        Reglement,
        "supplier_invoices:readonly",
        _reglements("supplier_invoices"),
    ),
    SousRessourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "matched_transactions",
        Transaction,
        "supplier_invoices:readonly",
        _transactions_appariees("matched_transactions_par_facture_fournisseur"),
    ),
    # ── Tiers ───────────────────────────────────────────────────────────────
    SousRessourceSpec(
        "customers",
        "customers",
        "contacts",
        Contact,
        "customers:readonly",
        _sous("customer_contacts"),
    ),
    SousRessourceSpec(
        "customers",
        "customers",
        "categories",
        Categorie,
        "customers:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "suppliers",
        "suppliers",
        "categories",
        Categorie,
        "suppliers:readonly",
        _vide,
    ),
    # ── Comptabilité ────────────────────────────────────────────────────────
    SousRessourceSpec(
        "ledger_entries",
        "ledger_entries",
        "ledger_entry_lines",
        LigneEcriture,
        "ledger_entries:readonly",
        _lignes_ecriture,
    ),
    SousRessourceSpec(
        "ledger_entries",
        "ledger_entries",
        "dms_files",
        ElementGenerique,
        "ledger_entries:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "ledger_entry_lines",
        "ledger_entry_lines",
        "categories",
        Categorie,
        "ledger_entries:readonly",
        _categories_de,
    ),
    SousRessourceSpec(
        "ledger_entry_lines",
        "ledger_entry_lines",
        "lettered_ledger_entry_lines",
        LigneEcriture,
        "ledger_entries:readonly",
        _lignes_lettrees,
    ),
    # ── Analytique ──────────────────────────────────────────────────────────
    SousRessourceSpec(
        "category_groups",
        "category_groups",
        "categories",
        Categorie,
        "categories:readonly",
        lambda donnees, parent: [
            c for c in donnees["categories"] if c["category_group"]["id"] == parent["id"]
        ],
    ),
    # ── Banque ──────────────────────────────────────────────────────────────
    SousRessourceSpec(
        "transactions",
        "transactions",
        "categories",
        Categorie,
        "transactions:readonly",
        _categories_de,
    ),
    SousRessourceSpec(
        "transactions",
        "transactions",
        "matched_invoices",
        FactureClient,
        "transactions:readonly",
        _factures_appariees,
    ),
    # ── Périphérie ──────────────────────────────────────────────────────────
    SousRessourceSpec(
        "quotes",
        "quotes",
        "invoice_lines",
        LigneFacture,
        "quotes:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "quotes",
        "quotes",
        "invoice_line_sections",
        ElementGenerique,
        "quotes:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "quotes",
        "quotes",
        "appendices",
        ElementGenerique,
        "quotes:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "commercial_documents",
        "commercial_documents",
        "invoice_lines",
        LigneFacture,
        "commercial_documents:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "commercial_documents",
        "commercial_documents",
        "invoice_line_sections",
        ElementGenerique,
        "commercial_documents:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "commercial_documents",
        "commercial_documents",
        "appendices",
        ElementGenerique,
        "commercial_documents:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "billing_subscriptions",
        "billing_subscriptions",
        "invoice_lines",
        LigneFacture,
        "billing_subscriptions:readonly",
        _vide,
    ),
    SousRessourceSpec(
        "billing_subscriptions",
        "billing_subscriptions",
        "invoice_line_sections",
        ElementGenerique,
        "billing_subscriptions:readonly",
        _vide,
    ),
)

#: Les dix journaux de changements. Les sept premiers sont documentés dans le
#: guide ; `quotes` et les deux `*_categories` n'apparaissent que dans la
#: référence — un consommateur qui s'en tiendrait au guide les manquerait.
CHANGELOGS: tuple[tuple[str, str], ...] = (
    ("customer_invoices", "customer_invoices:readonly"),
    ("supplier_invoices", "supplier_invoices:readonly"),
    ("customers", "customers:readonly"),
    ("suppliers", "suppliers:readonly"),
    ("products", "products:readonly"),
    ("transactions", "transactions:readonly"),
    ("quotes", "quotes:readonly"),
    ("ledger_entry_lines", "ledger_entries:readonly"),
    ("ledger_entries_categories", "ledger_entries:readonly"),
    ("ledger_entry_lines_categories", "ledger_entries:readonly"),
)


# ═════════════════════════════════════════════════════════════════════════════
#  Le pipeline de requête
# ═════════════════════════════════════════════════════════════════════════════


def _entetes_debit_courants(chemin: str) -> dict[str, str]:
    """Les en-têtes `ratelimit-*`, sur TOUTE réponse.

    Le fournisseur les sert même sur un 200 : c'est ce qui permet à un client
    de se réguler AVANT de se faire limiter. Un mock qui ne les servirait que
    sur les 429 apprendrait au consommateur à ne pas les lire.
    """
    vus = engine.request_counts.get(chemin, 0)
    reste = max(0, settings.rate_limit - vus)
    reset = int(engine.now()) + int(settings.rate_window)
    return entetes_debit(settings.rate_limit, reste, reset)


def maintenant_virtuel() -> datetime:
    """L'instant de référence du mock — l'ancre du jeu de données, pas `now()`.

    Elle avance avec la chronologie d'évolution : chaque événement vaut
    `EPOQUE + (k+1) x intervalle`, donc le « maintenant » d'un mock qui a joué
    N événements est `EPOQUE + N x intervalle`. C'est ce qui rend la rétention
    du changelog reproductible : le même mock, avancé du même nombre de pas,
    retient exactement les mêmes événements — quel que soit le jour où on le
    lance.
    """
    from .evolution import EPOQUE

    return EPOQUE + timedelta(seconds=settings.evolution_interval * state.evolution.rang)


def _dispatch_injections(chemin: str, rang: int) -> Response | None:
    """Le point de dispatch unique, évalué avant l'authentification."""
    if (regle := engine.first("latency", chemin)) is not None and regle.consume():
        time.sleep(regle.seconds)

    regle = engine.first("rate_limit", chemin)
    if regle is not None and rang > regle.after_requests and regle.consume():
        # `ratelimit-remaining: 0` sur un 429 — par définition : c'est
        # justement parce qu'il ne reste rien qu'on est limité. Servir le
        # compteur nominal ici ferait croire à un client qu'il peut repartir
        # tout de suite, et il boucle.
        entetes = entetes_debit(settings.rate_limit, 0, int(engine.now() + settings.rate_window))
        return erreur_debit(regle.retry_after_seconds, entetes)

    if (regle := engine.first("auth_reject", chemin)) is not None and regle.consume():
        return erreur_jeton()

    if (regle := engine.first("scope_reject", chemin)) is not None and regle.consume():
        return erreur_scope(regle.scope_manquant)

    if (regle := engine.first("cursor_reject", chemin)) is not None and regle.consume():
        return erreur(400, "Invalid cursor")

    if (regle := engine.first("status", chemin)) is not None and regle.consume():
        return erreur(regle.status, f"Injected failure ({regle.status})")
    return None


def _prelude(request: Request, chemin: str, scope: str | None) -> Response | None:
    """Le pipeline commun. Rend `None` quand la requête peut passer."""
    parametres = dict(request.query_params)
    state.avancer_evolution(engine.now())
    rang = engine.observe(chemin, parametres)

    if (refus := _dispatch_injections(chemin, rang)) is not None:
        return refus

    jeton = jeton_de_l_entete(request.headers.get("Authorization"))
    if not jeton_est_valide(jeton):
        return erreur_jeton()
    if not scope_accorde(scope):
        return erreur_scope(scope or "")
    return None


def _ok(contenu: Any, chemin: str) -> JSONResponse:
    return JSONResponse(content=contenu, headers=_entetes_debit_courants(chemin))


def _page(
    elements: list[dict[str, Any]],
    request: Request,
    *,
    tri_defaut: str,
    filtrable: bool,
    maximum: int | None = None,
) -> dict[str, Any] | Response:
    """Filtre, trie, pagine — dans cet ordre, qui est le seul correct.

    Trier avant de filtrer donnerait le même résultat mais coûterait plus ;
    paginer avant de filtrer donnerait un résultat FAUX (des pages courtes,
    puis vides, sans que `has_more` le dise). L'ordre est donc porteur.
    """
    try:
        if filtrable:
            elements = appliquer_filtre(
                elements, analyser_filtre(request.query_params.get("filter"))
            )
        elements = appliquer_tri(elements, request.query_params.get("sort"), defaut=tri_defaut)
        limite = limite_demandee(request.query_params.get("limit"), maximum=maximum)
        return paginer(
            elements,
            curseur=request.query_params.get("cursor"),
            limite=limite,
            cle=tri_defaut.lstrip("-") if tri_defaut.lstrip("-") in {"id"} else "id",
        )
    except (FiltreInvalide, TriInvalide, LimiteInvalide, CurseurInvalide) as exc:
        return erreur(400, str(exc))


# ═════════════════════════════════════════════════════════════════════════════
#  Les paramètres de requête, déclarés POUR LE CONTRAT
# ═════════════════════════════════════════════════════════════════════════════
#
# ┌─ POURQUOI ILS NE SONT PAS DANS LA SIGNATURE DES HANDLERS ──────────────────┐
# │ Déclarer `limit: int` en argument ferait valider FastAPI À NOTRE PLACE, et │
# │ un `limit=abc` rendrait le 422 de FastAPI — une forme d'erreur qui         │
# │ n'existe pas chez Pennylane, où c'est un 400 à l'enveloppe                 │
# │ `{"error","status"}`. Le mock apprendrait au consommateur une gestion      │
# │ d'erreur fausse.                                                          │
# │                                                                            │
# │ Les paramètres sont donc LUS de `request.query_params` et validés par      │
# │ `pagination.py` / `filtres.py`, et déclarés ici uniquement pour que le     │
# │ contrat publié les décrive. C'est le contrat que copie insights360 : s'il  │
# │ ne portait pas `cursor`, un consommateur ne saurait pas qu'il doit         │
# │ paginer.                                                                   │
# └────────────────────────────────────────────────────────────────────────────┘


def _param_curseur() -> dict[str, Any]:
    return {
        "name": "cursor",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "Curseur de pagination, OPAQUE. Reprendre tel quel le `next_cursor` "
            "de la réponse précédente ; le décoder, c'est s'adosser à un détail "
            "d'implémentation que la documentation du fournisseur montre sous "
            "trois formes incompatibles. Un curseur illisible rend 400."
        ),
    }


def _param_limite(maximum: int) -> dict[str, Any]:
    return {
        "name": "limit",
        "in": "query",
        "required": False,
        "schema": {"type": "integer", "minimum": 1, "maximum": maximum},
        "description": (
            f"Taille de page. Défaut 20, entre 1 et {maximum}. Une valeur hors "
            "bornes rend 400 — elle n'est PAS rabotée en silence."
        ),
    }


def _param_tri(defaut: str) -> dict[str, Any]:
    return {
        "name": "sort",
        "in": "query",
        "required": False,
        "schema": {"type": "string", "default": defaut},
        "description": (
            f"Champ de tri, préfixé de `-` pour l'ordre décroissant. Défaut `{defaut}` "
            "— donc DÉCROISSANT si rien n'est précisé, ce qui est l'inverse de "
            "l'intuition."
        ),
    }


def _param_filtre() -> dict[str, Any]:
    return {
        "name": "filter",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "example": '[{"field": "date", "operator": "gteq", "value": "2026-01-01"}]',
        "description": (
            "Tableau JSON d'objets `{field, operator, value}`, cumulés en ET. "
            "Opérateurs : eq, not_eq, lt, lteq, gt, gteq, in, not_in, start_with. "
            "⚠️ Le curseur N'ENCODE PAS les filtres : il faut les REJOUER sur "
            "chaque page, sinon les pages 2+ rendent des résultats non filtrés."
        ),
    }


def _params_liste(*, tri_defaut: str, filtrable: bool, maximum: int) -> list[dict[str, Any]]:
    """Les paramètres de requête d'une liste.

    ⚠️ Le paramètre de CHEMIN n'y figure pas : FastAPI AJOUTE les entrées
    d'`openapi_extra` à celles qu'il déduit de la signature, il ne les remplace
    pas. L'y mettre le publierait deux fois, et un générateur de client
    produirait une fonction à deux arguments identiques.
    """
    parametres = [_param_curseur(), _param_limite(maximum), _param_tri(tri_defaut)]
    if filtrable:
        parametres.append(_param_filtre())
    return parametres


# ═════════════════════════════════════════════════════════════════════════════
#  L'application
# ═════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Pennylane Company API v2 — mock",
    version=VERSION,
    description=(
        "Mock de l'API Pennylane v2 (lecture seule) sur le monde « Boréal Conseil ». "
        "Enveloppe à curseur `{items, has_more, next_cursor}`, montants en CHAÎNES, "
        "scopes granulaires, changelogs pour l'extraction incrémentale."
    ),
    docs_url="/docs",
    redoc_url=None,
)
routeur = APIRouter()


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    """Sonde de vivacité — NON authentifiée, hors de la surface fournisseur.

    Le healthcheck de l'image l'interroge, et `depends_on: service_healthy`
    côté consommateur en dépend. La mettre derrière le jeton rendrait le
    conteneur « unhealthy » pour un problème de configuration.
    """
    return {"status": "ok", "service": "pennylane-mock"}


@app.exception_handler(StarletteHTTPException)
async def _erreur_http(request: Request, exc: StarletteHTTPException) -> Response:
    """Routes et méthodes inconnues : l'enveloppe Pennylane, pas le 404 FastAPI.

    Un `{"detail": "Not Found"}` apprendrait au consommateur une forme d'erreur
    qui n'existe pas chez le fournisseur — et son code de gestion d'erreur
    casserait le jour où il parle à la vraie API.
    """
    del request
    if exc.status_code in (404, 405):
        return erreur_introuvable()
    return erreur(exc.status_code, str(exc.detail))


# ── /me : le seul endpoint sans scope ────────────────────────────────────────


@routeur.get(
    f"{PREFIXE}/me",
    response_model=ProfilUtilisateur,
    responses=REPONSES_ERREUR,
    tags=["Users"],
    summary="Profil de l'utilisateur et de la société",
)
def profil(request: Request) -> Any:
    """Le test de fumée d'un connecteur : qui suis-je, et que puis-je lire ?

    Aucun scope requis — c'est justement lui qui sert à découvrir les scopes
    dont on dispose. Un connecteur doit l'appeler AVANT d'ouvrir son pipeline :
    échouer sur une authentification vaut mieux qu'un run à moitié fait.
    """
    chemin = f"{PREFIXE}/me"
    if (refus := _prelude(request, chemin, None)) is not None:
        return refus
    return _ok(
        {
            "user": {
                "id": 1,
                "first_name": "Intégration",
                "last_name": "insights360",
                "email": "integration@boreal-conseil.example",
                "locale": "fr",
            },
            "company": {
                "id": settings.company_id,
                "name": settings.company_name,
                "reg_no": settings.company_reg_no,
                "accounting_logic": "FR_PCG",
            },
            "scopes": sorted(settings.scopes),
        },
        chemin,
    )


# ── La balance ───────────────────────────────────────────────────────────────


@routeur.get(
    f"{PREFIXE}/trial_balance",
    response_model=Page[LigneBalance],
    responses=REPONSES_ERREUR,
    tags=["Accounting"],
    summary="Balance générale sur une période",
    openapi_extra={
        "parameters": [
            {
                "name": "period_start",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "format": "date"},
                "description": "Début de la période. OBLIGATOIRE.",
            },
            {
                "name": "period_end",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "format": "date"},
                "description": "Fin de la période. OBLIGATOIRE.",
            },
            {
                "name": "is_auxiliary",
                "in": "query",
                "required": False,
                "schema": {"type": "boolean"},
                "description": (
                    "Détailler les comptes auxiliaires. À faux, ils sont AGRÉGÉS "
                    "dans leur racine : sommer les deux vues double l'actif."
                ),
            },
            _param_curseur(),
            _param_limite(1000),
        ]
    },
)
def trial_balance(request: Request) -> Any:
    """`period_start` et `period_end` sont OBLIGATOIRES.

    C'est la seule ressource du mock qui exige des paramètres — et c'est
    délibéré chez le fournisseur : une balance sans période n'a pas de sens.
    Un consommateur qui les oublie doit recevoir 400, pas une balance de
    l'exercice courant choisie à sa place.
    """
    chemin = f"{PREFIXE}/trial_balance"
    if (refus := _prelude(request, chemin, "trial_balance:readonly")) is not None:
        return refus

    from .dataset.realiste import balance

    debut_brut = request.query_params.get("period_start")
    fin_brut = request.query_params.get("period_end")
    if not debut_brut or not fin_brut:
        return erreur(400, "period_start and period_end are required")
    try:
        debut, fin = date.fromisoformat(debut_brut), date.fromisoformat(fin_brut)
    except ValueError:
        return erreur(400, "period_start and period_end must be ISO 8601 dates")

    auxiliaires = (request.query_params.get("is_auxiliary") or "").lower() in {
        "1",
        "true",
        "yes",
    }
    lignes = balance(
        state.dataset["ledger_entry_lines"],
        state.dataset["ledger_accounts"],
        debut=debut,
        fin=fin,
        auxiliaires=auxiliaires,
    )
    resultat = _page(
        lignes,
        request,
        tri_defaut="number",
        filtrable=False,
        maximum=settings.limite_max_changelog,
    )
    if isinstance(resultat, Response):
        return resultat
    return _ok(resultat, chemin)


# ═════════════════════════════════════════════════════════════════════════════
#  La fabrique de routes
# ═════════════════════════════════════════════════════════════════════════════


def _monter_ressource(spec: RessourceSpec) -> None:
    """Monte la liste et le détail d'une ressource.

    La fabrique existe pour lier `spec` à chaque itération : sans elle, la
    fermeture capturerait la variable de boucle et les 24 ressources
    serviraient toutes la dernière.
    """
    base = f"{PREFIXE}/{spec.chemin}"

    if spec.avec_liste:

        @routeur.get(
            base,
            response_model=Page[spec.modele],  # type: ignore[name-defined]
            responses=REPONSES_ERREUR,
            tags=[spec.singulier],
            summary=f"Liste des {spec.chemin}",
            name=f"list_{spec.cle}",
            openapi_extra={
                "parameters": _params_liste(
                    tri_defaut=spec.tri_defaut,
                    filtrable=spec.filtrable,
                    maximum=settings.limite_max,
                )
            },
        )
        def lister(request: Request) -> Any:
            if (refus := _prelude(request, base, spec.scope)) is not None:
                return refus
            resultat = _page(
                list(state.dataset[spec.cle]),
                request,
                tri_defaut=spec.tri_defaut,
                filtrable=spec.filtrable,
            )
            if isinstance(resultat, Response):
                return resultat
            return _ok(resultat, base)

    if spec.avec_detail:

        @routeur.get(
            base + "/{ident}",
            response_model=spec.modele,
            responses=REPONSES_ERREUR,
            tags=[spec.singulier],
            summary=f"Détail d'un élément de {spec.chemin}",
            name=f"get_{spec.cle}",
        )
        def detail(ident: int, request: Request) -> Any:
            chemin = f"{PREFIXE}/{spec.chemin}/{ident}"
            if (refus := _prelude(request, chemin, spec.scope)) is not None:
                return refus
            element = state.index().get((spec.cle, ident))
            if element is None:
                return erreur_introuvable()
            return _ok(element, chemin)


def _monter_sous_ressource(spec: SousRessourceSpec) -> None:
    chemin_modele = f"{PREFIXE}/{spec.parent}/{{ident}}/{spec.chemin}"

    @routeur.get(
        chemin_modele,
        response_model=Page[spec.modele],  # type: ignore[name-defined]
        responses=REPONSES_ERREUR,
        tags=[spec.parent],
        summary=f"{spec.chemin} d'un élément de {spec.parent}",
        name=f"list_{spec.parent}_{spec.chemin}",
        openapi_extra={
            "parameters": _params_liste(
                tri_defaut=spec.tri_defaut,
                filtrable=spec.chemin == "ledger_entry_lines",
                maximum=settings.limite_max,
            )
        },
    )
    def lister(ident: int, request: Request) -> Any:
        chemin = f"{PREFIXE}/{spec.parent}/{ident}/{spec.chemin}"
        if (refus := _prelude(request, chemin, spec.scope)) is not None:
            return refus
        parent = state.index().get((spec.parent_cle, ident))
        if parent is None:
            return erreur_introuvable()
        resultat = _page(
            spec.resoudre(state.dataset, parent),
            request,
            tri_defaut=spec.tri_defaut,
            # Une sous-collection n'accepte `filter` que sur
            # `/ledger_entries/{id}/ledger_entry_lines` chez le fournisseur.
            # Ailleurs il est ignoré — pas refusé.
            filtrable=spec.chemin == "ledger_entry_lines",
        )
        if isinstance(resultat, Response):
            return resultat
        return _ok(resultat, chemin)


def _monter_changelog(famille: str, scope: str) -> None:
    chemin = f"{PREFIXE}/changelogs/{famille}"

    @routeur.get(
        chemin,
        response_model=Page[EvenementChangelog],
        responses=REPONSES_ERREUR,
        tags=["Changelogs"],
        summary=f"Changements sur {famille}",
        name=f"changelog_{famille}",
        openapi_extra={
            "parameters": [
                _param_curseur(),
                _param_limite(settings.limite_max_changelog),
                {
                    "name": "start_date",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string", "format": "date-time"},
                    "example": "2026-07-15T09:00:00Z",
                    "description": (
                        "Borne basse RFC 3339. Sans elle, les changements les plus "
                        "anciens sont rendus. Rétention de quatre semaines : une "
                        "borne plus ancienne rend **422**. `start_date` et `cursor` "
                        "ensemble rendent **400** — la pagination continue une "
                        "fenêtre, elle n'en ouvre pas une nouvelle."
                    ),
                },
            ]
        },
    )
    def journal(request: Request) -> Any:
        if (refus := _prelude(request, chemin, scope)) is not None:
            return refus

        brut_depuis = request.query_params.get("start_date")
        curseur = request.query_params.get("cursor")
        # `start_date` ET `cursor` ensemble → 400. La pagination CONTINUE une
        # fenêtre, elle n'en ouvre pas une nouvelle : sans ce refus, un
        # consommateur qui renvoie sa start_date à chaque page rejoue la
        # première indéfiniment et croit avoir tout lu.
        if brut_depuis and curseur:
            return erreur(400, "start_date and cursor cannot be used together")
        # L'instant de référence est celui du MOCK, pas l'horloge murale. Le
        # jeu de données est ancré au 15 juillet 2026 : évaluer une rétention
        # de quatre semaines contre la date réelle rendrait 422 sur toute
        # `start_date` légitime dès le lendemain de la construction du jeu, et
        # le mock cesserait de fonctionner sans qu'une ligne de code ait bougé.
        maintenant = maintenant_virtuel()
        try:
            depuis = chg.analyser_start_date(brut_depuis)
            chg.verifier_fenetre(depuis, maintenant)
        except chg.DateInvalide as exc:
            return erreur(400, str(exc))
        except chg.FenetreTropAncienne as exc:
            return erreur(422, str(exc))

        evenements = chg.selectionner(
            state.dataset["changelogs"].get(famille, []), depuis, maintenant
        )
        try:
            limite = limite_demandee(
                request.query_params.get("limit"), maximum=settings.limite_max_changelog
            )
            # Ordre CHRONOLOGIQUE CROISSANT, jamais `-id` : c'est la seule
            # famille de listes du dialecte qui ne suit pas le défaut.
            resultat = paginer(evenements, curseur=curseur, limite=limite, cle="id")
        except (LimiteInvalide, CurseurInvalide) as exc:
            return erreur(400, str(exc))
        return _ok(resultat, chemin)


def _monter_detail_client(segment: str, type_attendu: str) -> None:
    """`/company_customers/{id}` et `/individual_customers/{id}`.

    Ils servent la MÊME entité que `/customers/{id}`, mais rendent 404 quand le
    type ne correspond pas. C'est ce qui permet à un consommateur de valider le
    type sans lire le discriminant — et le 404 est la seule façon dont le
    fournisseur le lui dit.
    """

    @routeur.get(
        f"{PREFIXE}/{segment}/{{ident}}",
        response_model=TiersClient,
        responses=REPONSES_ERREUR,
        tags=["customer"],
        summary=f"Détail d'un client de type {type_attendu}",
        name=f"get_{segment}",
    )
    def detail_client(ident: int, request: Request) -> Any:
        chemin = f"{PREFIXE}/{segment}/{ident}"
        if (refus := _prelude(request, chemin, "customers:readonly")) is not None:
            return refus
        element = state.index().get(("customers", ident))
        if element is None or element["customer_type"] != type_attendu:
            return erreur_introuvable()
        return _ok(element, chemin)


def _monter_export(segment: str, cle: str, scope: str) -> None:
    """La RÉCUPÉRATION d'un export. Sa création est un POST, hors périmètre :
    le mock sert donc des exports pré-existants, ce qui suffit à exercer le
    second temps du couple création → récupération."""

    @routeur.get(
        f"{PREFIXE}/exports/{segment}/{{ident}}",
        response_model=ElementGenerique,
        responses=REPONSES_ERREUR,
        tags=["Exports"],
        summary=f"Récupération d'un export {segment}",
        name=f"get_export_{segment}",
    )
    def detail_export(ident: int, request: Request) -> Any:
        chemin = f"{PREFIXE}/exports/{segment}/{ident}"
        if (refus := _prelude(request, chemin, scope)) is not None:
            return refus
        element = state.index().get((cle, ident))
        if element is None:
            return erreur_introuvable()
        return _ok(element, chemin)


@routeur.get(
    f"{PREFIXE}/pa_registrations",
    response_model=Page[ElementGenerique],
    responses=REPONSES_ERREUR,
    tags=["PA registrations"],
    summary="Inscriptions aux plateformes agréées de facturation électronique",
)
def pa_registrations(request: Request) -> Any:
    """L'OpenAPI ne déclare AUCUN scope ni AUCUN paramètre sur cet endpoint —
    c'est le seul de la surface dans ce cas avec `/me`. Il n'est donc PAS
    paginé : la réponse porte l'enveloppe, mais `has_more` y est toujours faux.
    """
    chemin = f"{PREFIXE}/pa_registrations"
    if (refus := _prelude(request, chemin, None)) is not None:
        return refus
    return _ok(
        {"items": state.dataset["pa_registrations"], "has_more": False, "next_cursor": None},
        chemin,
    )


for _spec in RESSOURCES:
    _monter_ressource(_spec)
for _sspec in SOUS_RESSOURCES:
    _monter_sous_ressource(_sspec)
for _famille, _scope_changelog in CHANGELOGS:
    _monter_changelog(_famille, _scope_changelog)
for _segment, _type_client in (
    ("company_customers", "company"),
    ("individual_customers", "individual"),
):
    _monter_detail_client(_segment, _type_client)
for _segment, _cle_export, _scope_export in (
    ("general_ledgers", "general_ledger_exports", "exports:gl"),
    ("analytical_general_ledgers", "analytical_general_ledger_exports", "exports:agl"),
    ("fecs", "fec_exports", "exports:fec"),
):
    _monter_export(_segment, _cle_export, _scope_export)

app.include_router(routeur)

# Le plan de contrôle n'est pas « monté puis interdit » : quand il est
# désactivé, la surface n'existe pas. C'est ce qui rend impossible de le
# laisser ouvert par accident sur un cluster.
if settings.admin_enabled:
    from .admin import router as admin_router

    app.include_router(admin_router)


# ═════════════════════════════════════════════════════════════════════════════
#  Le contrat
# ═════════════════════════════════════════════════════════════════════════════


def contrat_openapi() -> dict[str, Any]:
    """Le contrat PUBLIÉ — la surface fournisseur, et elle seule.

    `/__admin` et `/health` sont des affordances du MOCK : les publier ferait
    passer pour de l'API Pennylane ce qui n'en est pas, et `/__admin` n'est
    monté que conditionnellement — le contrat dépendrait alors de
    l'environnement de génération, ce qui le rendrait ininterprétable.
    """
    schema = app.openapi()
    chemins = {
        chemin: operations
        for chemin, operations in schema["paths"].items()
        if chemin.startswith(PREFIXE)
    }
    contrat: dict[str, Any] = {
        "openapi": schema["openapi"],
        "info": dict(schema["info"]),
        "servers": [{"url": "https://app.pennylane.com"}],
        "paths": chemins,
    }
    composants = schema.get("components", {})
    if composants:
        contrat["components"] = _elaguer_schemas(composants, chemins)
    return contrat


def _elaguer_schemas(composants: dict[str, Any], chemins: dict[str, Any]) -> dict[str, Any]:
    """Retire les schémas devenus orphelins après le retrait de /__admin.

    Sans cet élagage, le contrat porterait les modèles du plan de contrôle —
    des formes qui n'existent nulle part chez le fournisseur.
    """
    import json
    import re

    schemas = dict(composants.get("schemas", {}))
    utilises: set[str] = set()
    a_visiter = set(re.findall(r"#/components/schemas/([A-Za-z0-9_.\[\]-]+)", json.dumps(chemins)))
    while a_visiter:
        nom = a_visiter.pop()
        if nom in utilises or nom not in schemas:
            continue
        utilises.add(nom)
        a_visiter |= set(
            re.findall(r"#/components/schemas/([A-Za-z0-9_.\[\]-]+)", json.dumps(schemas[nom]))
        )
    resultat = dict(composants)
    resultat["schemas"] = {nom: schemas[nom] for nom in sorted(utilises)}
    return resultat
