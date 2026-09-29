"""Served entities — fields taken from the official v2 OpenAPI.

┌─ EVERY AMOUNT IS A STRING ───────────────────────────────────────────────────┐
│ `amount: "230.32"`, `debit: "100.00"`, `quantity: "12"`, `weight: "0.25"`.   │
│ This isn't a serialization quirk: the error guide lists "amounts not sent   │
│ as strings" as a typical cause of a 400 on write, and the OpenAPI declares  │
│ them `type: string` on read. A connector that receives a number here and    │
│ tolerates it will break against the real API. Numeric typing is the        │
│ staging layer's job, not extraction's.                                     │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ GRADED FIDELITY ────────────────────────────────────────────────────────────┐
│ The entities of the financial flow — invoices, third parties, banking,      │
│ accounting — are typed field by field. The periphery (quotes, mandates,     │
│ subscriptions, commercial documents, purchase requests, exports) goes       │
│ through `GenericElement`: it is SERVED with the OpenAPI's fields, but not    │
│ constrained by the model. This is a decision, not an oversight — it's       │
│ written up in the README and in docs/UNVERIFIED-FIELDS.md.                  │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import Link, Permissive, Reference, unverified


class GenericElement(Permissive):
    """A peripheral resource: `id` and timestamps guaranteed, the rest passes
    through as-is. Served, but not constrained — see the module's callout."""

    id: int
    created_at: str
    updated_at: str


# ── Reference data ────────────────────────────────────────────────────────────


class Journal(Permissive):
    """An accounting journal. Note the absence of timestamps: the provider
    does NOT serve them on this resource, unlike every other one."""

    id: int
    code: str = Field(description="Journal code (VE, AC, BQ, OD, AN, SA).")
    label: str
    type: str = Field(
        json_schema_extra=unverified(
            "the OpenAPI declares `type: string` WITHOUT an enum; the served "
            "values (sale, purchase, bank, miscellaneous, new_year, payroll) "
            "follow the usual French nomenclature, they are not attested"
        )
    )


class LedgerAccount(Permissive):
    """An account in the chart of accounts — general or auxiliary."""

    id: int
    number: str = Field(description="Account number; an auxiliary account carries letters.")
    label: str
    vat_rate: str = Field(
        description=(
            "A rate CODE, not a percentage: `any` (the vast majority of "
            "accounts), `exempt`, `extracom`, `crossborder`, or `FR_200`, "
            "`FR_55`, `FR_15_385`… It used to be described here as a "
            'percentage string, and the mock served "20.0" — a consumer '
            "that cast it to a number passed against the mock and broke on "
            "the provider's first `any`."
        )
    )
    country_alpha2: str
    enabled: bool
    type: str = Field(
        json_schema_extra=unverified(
            "the OpenAPI declares `type: string` without an enum; the served "
            "values (customer, supplier, bank, tax, income, expense, equity, "
            "suspense) are plausible, not attested"
        )
    )
    letterable: bool
    created_at: str
    updated_at: str


class FiscalYear(Permissive):
    id: int
    start: str
    finish: str
    status: str = Field(description="open | reopen | closed | frozen")
    created_at: str
    updated_at: str


class CategoryGroup(Permissive):
    """An analytical axis. `categories` is a LINK, not an array."""

    id: int
    label: str
    categories: Link
    created_at: str
    updated_at: str


class Category(Permissive):
    id: int
    label: str
    direction: str | None = None
    category_group: Reference
    analytical_code: str | None = None
    created_at: str
    updated_at: str


class AllocatedCategory(Permissive):
    """A category as it appears ON an entry or a transaction: the category,
    plus the allocation's `weight` — also a string."""

    id: int
    label: str
    weight: str
    category_group: Reference
    analytical_code: str | None = None
    created_at: str
    updated_at: str


class BankEstablishment(Permissive):
    id: int
    name: str
    created_at: str
    updated_at: str


class BankAccount(Permissive):
    id: int
    name: str
    currency: str
    balance: str
    bank_establishment: Reference
    journal: Reference | None = None
    ledger_account: Reference
    created_at: str
    updated_at: str


# ── Third parties ─────────────────────────────────────────────────────────────


class Address(Permissive):
    address: str
    postal_code: str
    city: str
    country_alpha2: str


class Customer(Permissive):
    """Customer — a company OR an individual.

    The provider makes this a two-variant `oneOf` discriminated by
    `customer_type`, and the two don't share the same fields: `name`/
    `reg_no`/`vat_number` on one side, `first_name`/`last_name` on the
    other. The model merges them, with the per-variant fields made
    optional — a pydantic `oneOf` would produce a more accurate contract but
    would make the route factory unreadable; the nuance is carried by the
    description and by the discriminant field, which itself is required.
    """

    id: int
    customer_type: str = Field(description="company | individual — THE discriminant.")
    name: str
    first_name: str | None = Field(default=None, description="Individuals only.")
    last_name: str | None = Field(default=None, description="Individuals only.")
    reg_no: str | None = Field(default=None, description="Companies only (SIREN).")
    vat_number: str | None = Field(default=None, description="Companies only.")
    billing_iban: str | None = None
    payment_conditions: str
    recipient: str
    phone: str
    reference: str | None = None
    notes: str | None = None
    ledger_account: Reference | None = None
    emails: list[str]
    billing_address: Address
    delivery_address: Address
    external_reference: str
    billing_language: str
    mandates: Link
    pro_account_mandates: Link
    contacts: Link
    created_at: str
    updated_at: str


class Contact(Permissive):
    """A customer's contact.

    WARNING: PERSONAL DATA. The mock serves it because the provider serves
    it; a connector should only extract an allow-listed subset.
    """

    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    job_title: str = Field(
        json_schema_extra=unverified(
            "the `getcustomercontacts` reference doesn't detail the element's "
            "schema; the served fields are plausible"
        )
    )
    customer: Reference
    created_at: str
    updated_at: str


class Supplier(Permissive):
    id: int
    name: str
    establishment_no: str | None = None
    reg_no: str | None = None
    vat_number: str
    ledger_account: Reference | None = None
    emails: list[str]
    iban: str
    postal_address: Address
    supplier_payment_method: str | None = None
    supplier_due_date_delay: int | None = None
    supplier_due_date_rule: str | None = None
    external_reference: str
    created_at: str
    updated_at: str


class Product(Permissive):
    id: int
    label: str
    description: str
    external_reference: str
    price_before_tax: str
    vat_rate: str = Field(description="Rate code (`FR_200` = 20%), not a number.")
    price: str = Field(description="The tax-inclusive price. It's READ, never recomputed.")
    unit: str
    currency: str
    reference: str | None = None
    ledger_account: Reference | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


# ── Invoicing ──────────────────────────────────────────────────────────────────


class Discount(Permissive):
    type: str = Field(description="absolute | relative")
    value: str | None = None


class CustomerInvoice(Permissive):
    """A sales invoice. A CREDIT NOTE is one too: `status:
    "credit_note"`, negative amounts, `credited_invoice` set — not a
    separate entity type. Summing `amount` without checking the sign
    misstates revenue."""

    id: int
    label: str | None = None
    invoice_number: str = Field(description="Empty while the invoice is a draft.")
    currency: str
    amount: str
    currency_amount: str
    currency_amount_before_tax: str
    exchange_rate: str
    date: str | None = None
    deadline: str | None = Field(
        default=None,
        description="Due date as SERVED: it depends on the customer's payment "
        "terms, it isn't derived from `date`.",
    )
    currency_tax: str
    tax: str
    language: str
    paid: bool
    status: str
    discount: Discount
    ledger_entry: Reference | None = Field(
        default=None, description="`null` on a draft: a draft has no ledger entry."
    )
    public_file_url: str | None = None
    filename: str | None = None
    remaining_amount_with_tax: str | None = None
    remaining_amount_without_tax: str | None = None
    draft: bool
    special_mention: str | None = None
    customer: Reference | None = None
    invoice_line_sections: Link
    invoice_lines: Link
    custom_header_fields: Link
    categories: Link
    pdf_invoice_free_text: str
    pdf_invoice_subject: str
    pdf_description: str | None = None
    billing_subscription: Reference | None = None
    credited_invoice: Reference | None = None
    customer_invoice_template: Reference | None = None
    transaction_reference: dict[str, Any] | None = None
    payments: Link
    matched_transactions: Link
    appendices: Link
    quote: Reference | None = None
    external_reference: str
    e_invoicing: dict[str, Any] | None = None
    factur_x: bool
    schematron_validation_status: str | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


class InvoiceLine(Permissive):
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
    discount: Discount | None = None
    section_rank: int | None = None
    imputation_dates: dict[str, str] | None = None
    ledger_account: Reference | None = None
    created_at: str
    updated_at: str


class Payment(Permissive):
    """A payment attached to an invoice.

    WARNING: a `payment` is NOT a `matched_transaction`: the former is the
    payment recorded on the invoice, the latter the reconciled bank
    movement. The provider devotes an entire page to the distinction, and a
    consumer that adds them up counts the payment twice.
    """

    id: int
    label: str
    currency: str
    currency_amount: str
    status: str
    created_at: str
    updated_at: str


class SupplierInvoice(Permissive):
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
    invoice_lines: Link
    categories: Link
    transaction_reference: dict[str, Any] | None = None
    payment_status: str
    paid: bool
    payments: Link
    matched_transactions: Link
    external_reference: str
    import_source: dict[str, Any] | None = None
    e_invoicing: dict[str, Any] | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


# ── Banking ────────────────────────────────────────────────────────────────────


class Transaction(Permissive):
    """A bank movement. `amount` is SIGNED; `outstanding_balance` is `null`
    when there's nothing left to reconcile and an amount when there is."""

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
    categories: list[AllocatedCategory]
    matched_invoices: Link
    interbank_code: str | None = None
    archived_at: str | None = None
    created_at: str
    updated_at: str


# ── Accounting ─────────────────────────────────────────────────────────────────


class LedgerEntry(Permissive):
    """An accounting entry. Not all of them have an invoice: payroll and
    bank fees have none. Assuming "one entry = one document" loses whole
    expense categories."""

    id: int
    label: str | None = None
    piece_number: str | None = None
    date: str | None = None
    due_date: str | None = None
    invoice_number: str | None = None
    journal_id: int
    journal: Reference
    status: str | None = None
    categories: list[AllocatedCategory]
    ledger_attachment_filename: str | None = None
    attachment: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class LedgerEntryLine(Permissive):
    """An entry line. `debit` and `credit` coexist: one of the two is
    `"0.00"`, never `null`, and there is no single signed amount."""

    id: int
    debit: str
    credit: str
    label: str
    categories: list[AllocatedCategory]
    ledger_account: dict[str, Any]
    journal: Reference
    date: str
    ledger_entry: Reference
    lettered_ledger_entry_lines: dict[str, Any] = Field(
        description="The lines lettered WITH this one — this is what "
        "distinguishes a settled receivable from an open one."
    )
    created_at: str
    updated_at: str


class TrialBalanceLine(Permissive):
    """A trial balance line. The mock's only resource WITHOUT an `id` or
    timestamps: it's computed, not stored. A consumer that expects one
    fails here."""

    number: str
    formatted_number: str
    label: str
    debits: str
    credits: str


# ── Changelogs and profile ─────────────────────────────────────────────────────


class ChangelogEvent(Permissive):
    """A change. It carries the ID and the operation, **never the state**: a
    second, batched call is needed to get the resource."""

    id: int
    operation: str = Field(description="insert | update | delete")
    processed_at: str
    created_at: str
    updated_at: str


class UserProfile(Permissive):
    """`GET /me` — the only endpoint WITHOUT a required scope, hence a
    connector's natural smoke test: it says who you are and what you're
    allowed to read."""

    user: dict[str, Any] | None
    company: dict[str, Any]
    scopes: list[str]
