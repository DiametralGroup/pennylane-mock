"""Les entités servies — champs relevés sur l'OpenAPI officiel de la v2.

┌─ TOUS LES MONTANTS SONT DES CHAÎNES ────────────────────────────────────────┐
│ `amount: "230.32"`, `debit: "100.00"`, `quantity: "12"`, `weight: "0.25"`.   │
│ Ce n'est pas une bizarrerie de sérialisation : le guide d'erreurs liste      │
│ « amounts not sent as strings » comme cause typique de 400 à l'écriture, et  │
│ l'OpenAPI les déclare `type: string` en lecture. Un connecteur qui reçoit un │
│ nombre ici et le tolère se cassera contre la vraie API. Le typage numérique  │
│ est l'affaire de la couche de staging, pas de l'extraction.                  │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ FIDÉLITÉ GRADUÉE ──────────────────────────────────────────────────────────┐
│ Les entités du flux financier — factures, tiers, banque, comptabilité — sont │
│ typées champ par champ. La périphérie (devis, mandats, abonnements,          │
│ documents commerciaux, demandes d'achat, exports) passe par                  │
│ `ElementGenerique` : elle est SERVIE avec les champs de l'OpenAPI, mais pas  │
│ contrainte par le modèle. C'est une décision, pas un oubli — elle est écrite │
│ dans le README et dans docs/UNVERIFIED-FIELDS.md.                            │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import Lien, Permissif, Reference, unverified


class ElementGenerique(Permissif):
    """Une ressource de la périphérie : `id` et horodatages garantis, le reste
    passe tel quel. Servie, mais pas contrainte — cf. l'encadré du module."""

    id: int
    created_at: str
    updated_at: str


# ── Référentiels ─────────────────────────────────────────────────────────────


class Journal(Permissif):
    """Un journal comptable. Notez l'absence d'horodatages : le fournisseur
    n'en sert PAS sur cette ressource, contrairement à toutes les autres."""

    id: int
    code: str = Field(description="Code du journal (VE, AC, BQ, OD, AN, SA).")
    label: str
    type: str = Field(
        json_schema_extra=unverified(
            "l'OpenAPI déclare `type: string` SANS énumération ; les valeurs "
            "servies (sale, purchase, bank, miscellaneous, new_year, payroll) "
            "suivent la nomenclature française usuelle, elles ne sont pas attestées"
        )
    )


class ComptePlan(Permissif):
    """Un compte du plan comptable — général ou auxiliaire."""

    id: int
    number: str = Field(description="Numéro de compte ; un auxiliaire porte des lettres.")
    label: str
    vat_rate: str = Field(
        description=(
            "Un CODE de taux, pas un pourcentage : `any` (l'écrasante majorité "
            "des comptes), `exempt`, `extracom`, `crossborder`, ou `FR_200`, "
            "`FR_55`, `FR_15_385`… Il était décrit ici comme un pourcentage en "
            "chaîne, et le mock servait « 20.0 » — un consommateur qui le "
            "castait en numérique passait sur le mock et tombait sur le "
            "premier `any` du fournisseur."
        )
    )
    country_alpha2: str
    enabled: bool
    type: str = Field(
        json_schema_extra=unverified(
            "l'OpenAPI déclare `type: string` sans énumération ; les valeurs "
            "servies (customer, supplier, bank, tax, income, expense, equity, "
            "suspense) sont plausibles, pas attestées"
        )
    )
    letterable: bool
    created_at: str
    updated_at: str


class Exercice(Permissif):
    id: int
    start: str
    finish: str
    status: str = Field(description="open | reopen | closed | frozen")
    created_at: str
    updated_at: str


class GroupeCategories(Permissif):
    """Un axe analytique. `categories` est un LIEN, pas un tableau."""

    id: int
    label: str
    categories: Lien
    created_at: str
    updated_at: str


class Categorie(Permissif):
    id: int
    label: str
    direction: str | None = None
    category_group: Reference
    analytical_code: str | None = None
    created_at: str
    updated_at: str


class CategorieVentilee(Permissif):
    """Une catégorie telle qu'elle apparaît SUR une écriture ou une transaction :
    la catégorie, plus le `weight` de la ventilation — lui aussi en chaîne."""

    id: int
    label: str
    weight: str
    category_group: Reference
    analytical_code: str | None = None
    created_at: str
    updated_at: str


class EtablissementBancaire(Permissif):
    id: int
    name: str
    created_at: str
    updated_at: str


class CompteBancaire(Permissif):
    id: int
    name: str
    currency: str
    balance: str
    bank_establishment: Reference
    journal: Reference | None = None
    ledger_account: Reference
    created_at: str
    updated_at: str


# ── Tiers ────────────────────────────────────────────────────────────────────


class Adresse(Permissif):
    address: str
    postal_code: str
    city: str
    country_alpha2: str


class TiersClient(Permissif):
    """Client — personne morale OU physique.

    Le fournisseur en fait un `oneOf` à deux variantes discriminées par
    `customer_type`, et elles n'ont pas les mêmes champs : `name`/`reg_no`/
    `vat_number` d'un côté, `first_name`/`last_name` de l'autre. Le modèle les
    réunit avec les champs propres en optionnels — un `oneOf` pydantic
    produirait un contrat plus juste mais rendrait la fabrique de routes
    illisible ; la nuance est portée par la description et par le champ
    discriminant, qui lui est requis.
    """

    id: int
    customer_type: str = Field(description="company | individual — LE discriminant.")
    name: str
    first_name: str | None = Field(default=None, description="Personnes physiques seulement.")
    last_name: str | None = Field(default=None, description="Personnes physiques seulement.")
    reg_no: str | None = Field(default=None, description="Personnes morales seulement (SIREN).")
    vat_number: str | None = Field(default=None, description="Personnes morales seulement.")
    billing_iban: str | None = None
    payment_conditions: str
    recipient: str
    phone: str
    reference: str | None = None
    notes: str | None = None
    ledger_account: Reference | None = None
    emails: list[str]
    billing_address: Adresse
    delivery_address: Adresse
    external_reference: str
    billing_language: str
    mandates: Lien
    pro_account_mandates: Lien
    contacts: Lien
    created_at: str
    updated_at: str


class Contact(Permissif):
    """Le contact d'un client.

    ⚠️ DONNÉE À CARACTÈRE PERSONNEL. Le mock la sert parce que le fournisseur
    la sert ; un connecteur ne doit en extraire qu'une liste blanche.
    """

    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    job_title: str = Field(
        json_schema_extra=unverified(
            "la référence `getcustomercontacts` ne détaille pas le schéma de "
            "l'élément ; les champs servis sont plausibles"
        )
    )
    customer: Reference
    created_at: str
    updated_at: str


class Fournisseur(Permissif):
    id: int
    name: str
    establishment_no: str | None = None
    reg_no: str | None = None
    vat_number: str
    ledger_account: Reference | None = None
    emails: list[str]
    iban: str
    postal_address: Adresse
    supplier_payment_method: str | None = None
    supplier_due_date_delay: int | None = None
    supplier_due_date_rule: str | None = None
    external_reference: str
    created_at: str
    updated_at: str


class Produit(Permissif):
    id: int
    label: str
    description: str
    external_reference: str
    price_before_tax: str
    vat_rate: str = Field(description="Code de taux (`FR_200` = 20 %), pas un nombre.")
    price: str = Field(description="Le TTC. Il se LIT, il ne se recalcule pas.")
    unit: str
    currency: str
    reference: str | None = None
    ledger_account: Reference | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


# ── Facturation ──────────────────────────────────────────────────────────────


class Remise(Permissif):
    type: str = Field(description="absolute | relative")
    value: str | None = None


class FactureClient(Permissif):
    """Une facture de vente. Un AVOIR en est une aussi : `status:
    "credit_note"`, montants négatifs, `credited_invoice` renseigné — pas une
    entité d'un autre type. Sommer `amount` sans regarder le signe fausse le CA."""

    id: int
    label: str | None = None
    invoice_number: str = Field(description="Vide tant que la facture est un brouillon.")
    currency: str
    amount: str
    currency_amount: str
    currency_amount_before_tax: str
    exchange_rate: str
    date: str | None = None
    deadline: str | None = Field(
        default=None,
        description="Échéance SERVIE : elle dépend des conditions du client, "
        "elle ne se déduit pas de `date`.",
    )
    currency_tax: str
    tax: str
    language: str
    paid: bool
    status: str
    discount: Remise
    ledger_entry: Reference | None = Field(
        default=None, description="`null` sur un brouillon : un brouillon n'a pas d'écriture."
    )
    public_file_url: str | None = None
    filename: str | None = None
    remaining_amount_with_tax: str | None = None
    remaining_amount_without_tax: str | None = None
    draft: bool
    special_mention: str | None = None
    customer: Reference | None = None
    invoice_line_sections: Lien
    invoice_lines: Lien
    custom_header_fields: Lien
    categories: Lien
    pdf_invoice_free_text: str
    pdf_invoice_subject: str
    pdf_description: str | None = None
    billing_subscription: Reference | None = None
    credited_invoice: Reference | None = None
    customer_invoice_template: Reference | None = None
    transaction_reference: dict[str, Any] | None = None
    payments: Lien
    matched_transactions: Lien
    appendices: Lien
    quote: Reference | None = None
    external_reference: str
    e_invoicing: dict[str, Any] | None = None
    factur_x: bool
    schematron_validation_status: str | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


class LigneFacture(Permissif):
    id: int
    label: str
    unit: str | None = None
    quantity: str
    amount: str
    currency_amount: str
    description: str
    product: Reference | None = None
    vat_rate: str
    currency_amount_before_tax: str
    currency_tax: str
    tax: str
    raw_currency_unit_price: str
    discount: Remise | None = None
    section_rank: int | None = None
    imputation_dates: dict[str, str] | None = None
    ledger_account: Reference | None = None
    created_at: str
    updated_at: str


class Reglement(Permissif):
    """Un règlement rattaché à une facture.

    ⚠️ Un `payment` n'est PAS une `matched_transaction` : le premier est le
    règlement enregistré sur la facture, la seconde le mouvement bancaire
    apparié. Le fournisseur consacre une page entière à la distinction, et un
    consommateur qui les additionne compte deux fois l'encaissement.
    """

    id: int
    label: str
    currency: str
    currency_amount: str
    status: str
    created_at: str
    updated_at: str


class FactureFournisseur(Permissif):
    id: int
    label: str | None = None
    invoice_number: str
    currency: str
    amount: str
    currency_amount: str
    currency_amount_before_tax: str
    exchange_rate: str
    date: str | None = None
    deadline: str | None = None
    currency_tax: str
    tax: str
    reconciled: bool
    accounting_status: str = Field(
        description="draft | archived | entry | validation_needed | complete"
    )
    filename: str | None = None
    public_file_url: str | None = None
    remaining_amount_with_tax: str | None = None
    remaining_amount_without_tax: str | None = None
    ledger_entry: Reference | None = None
    supplier: Reference | None = None
    invoice_lines: Lien
    categories: Lien
    transaction_reference: dict[str, Any] | None = None
    payment_status: str
    paid: bool
    payments: Lien
    matched_transactions: Lien
    external_reference: str
    import_source: dict[str, Any] | None = None
    e_invoicing: dict[str, Any] | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


# ── Banque ───────────────────────────────────────────────────────────────────


class Transaction(Permissif):
    """Un mouvement bancaire. `amount` est SIGNÉ ; `outstanding_balance` vaut
    `null` quand il n'y a rien à rapprocher et un montant quand il en reste."""

    id: int
    label: str | None = None
    attachment_required: bool
    date: str
    outstanding_balance: str | None = None
    currency: str
    currency_amount: str
    amount: str
    currency_fee: str | None = None
    fee: str | None = None
    journal: Reference
    bank_account: Reference
    pro_account_expense: dict[str, Any] | None = None
    customer: Reference | None = None
    supplier: Reference | None = None
    categories: list[CategorieVentilee]
    matched_invoices: Lien
    interbank_code: str | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


# ── Comptabilité ─────────────────────────────────────────────────────────────


class Ecriture(Permissif):
    """Une écriture comptable. Toutes n'ont PAS de facture : la paie et les
    frais bancaires n'en ont aucune. Supposer « une écriture = une pièce »
    fait perdre des charges entières."""

    id: int
    label: str | None = None
    piece_number: str | None = None
    date: str | None = None
    due_date: str | None = None
    invoice_number: str | None = None
    journal_id: int
    journal: Reference
    status: str | None = None
    categories: list[CategorieVentilee]
    ledger_attachment_filename: str | None = None
    attachment: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class LigneEcriture(Permissif):
    """Une ligne d'écriture. `debit` et `credit` coexistent : l'un des deux vaut
    `"0.00"`, jamais `null`, et il n'y a pas de montant signé unique."""

    id: int
    debit: str
    credit: str
    label: str
    categories: list[CategorieVentilee]
    ledger_account: dict[str, Any]
    journal: Reference
    date: str
    ledger_entry: Reference
    lettered_ledger_entry_lines: dict[str, Any] = Field(
        description="Les lignes lettrées AVEC celle-ci — c'est ce qui distingue "
        "une créance soldée d'une créance ouverte."
    )
    created_at: str
    updated_at: str


class LigneBalance(Permissif):
    """Une ligne de balance. La seule ressource du mock SANS `id` ni horodatage :
    elle est calculée, pas stockée. Un consommateur qui l'attend échoue ici."""

    number: str
    formatted_number: str
    label: str
    debits: str
    credits: str


# ── Changelogs et profil ─────────────────────────────────────────────────────


class EvenementChangelog(Permissif):
    """Un changement. Il porte l'ID et l'opération, **jamais l'état** : il faut
    un second appel, par lots, pour obtenir la ressource."""

    id: int
    operation: str = Field(description="insert | update | delete")
    processed_at: str
    created_at: str
    updated_at: str


class ProfilUtilisateur(Permissif):
    """`GET /me` — le seul endpoint SANS scope requis, donc le test de fumée
    naturel d'un connecteur : il dit qui on est et ce qu'on a le droit de lire."""

    user: dict[str, Any] | None
    company: dict[str, Any]
    scopes: list[str]
