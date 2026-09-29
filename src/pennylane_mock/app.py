"""FastAPI application assembly.

┌─ A SINGLE REQUEST PIPELINE ──────────────────────────────────────────────────┐
│ Every route on the provider surface goes through `_prelude`:                │
│                                                                              │
│     evolution → observation → injections → token → scope → handler         │
│                                                                              │
│ Failures are dispatched BEFORE authentication, so that an `auth_reject`     │
│ can preempt it. There can therefore be no "forgotten" route where failures  │
│ wouldn't apply, or where the scope wouldn't be checked — that's what the    │
│ route factory guarantees rather than a review discipline.                  │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ ONE DECLARATIVE TABLE, NOT 91 HANDLERS ────────────────────────────────────┐
│ The read-only v2 surface counts 91 GET operations. Writing them by hand     │
│ would produce 91 chances to forget a scope, a default sort or a pagination  │
│ envelope. `RESOURCES` and `SUB_RESOURCES` describe them; the factory mounts │
│ them. Adding a resource is one table row plus one key in the dataset.       │
└──────────────────────────────────────────────────────────────────────────────┘

Writes (POST/PUT/DELETE) are OUT OF SCOPE: this mock's consumer reads, it
doesn't write. They render 404 in the Pennylane dialect — the provider would
actually serve them; the gap is logged in docs/EXTRACTION.md.
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
from .auth import scope_granted, token_from_header, token_is_valid
from .errors import (
    error,
    not_found_error,
    rate_limit_error,
    rate_limit_headers,
    scope_error,
    token_error,
)
from .filters import (
    InvalidFilter,
    InvalidSort,
    apply_filter,
    apply_sort,
    parse_filter,
)
from .injection import engine
from .models import (
    ERROR_RESPONSES,
    BankAccount,
    BankEstablishment,
    Category,
    CategoryGroup,
    ChangelogEvent,
    Contact,
    Customer,
    CustomerInvoice,
    FiscalYear,
    GenericElement,
    InvoiceLine,
    Journal,
    LedgerAccount,
    LedgerEntry,
    LedgerEntryLine,
    Page,
    Payment,
    Product,
    Supplier,
    SupplierInvoice,
    Transaction,
    TrialBalanceLine,
    UserProfile,
)
from .pagination import InvalidCursor, InvalidLimit, paginate, requested_limit
from .settings import settings
from .state import state

PREFIX = "/api/external/v2"
VERSION = "0.2.0"


# ═════════════════════════════════════════════════════════════════════════════
#  The resource table
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ResourceSpec:
    """What's needed to serve a resource AND document it."""

    path: str  # URL segment under /api/external/v2
    key: str  # key in the dataset
    model: type  # pydantic model of the element — the contract's source
    scope: str | None  # required scope; None = none
    singular: str  # singular entity name (documentation)
    with_list: bool = True
    with_detail: bool = True
    default_sort: str = "-id"
    #: Lists that do NOT accept `filter` at the provider. The mock silently
    #: ignores it then, like the provider does — refusing it would be
    #: stricter than reality, and a consumer would stall here without
    #: stalling in production.
    filterable: bool = True
    #: Lists whose envelope ALSO carries offset pagination (`current_page`,
    #: `per_page`, `total_items`, `total_pages`). Four out of sixteen,
    #: observed on 2026-09-04 — and not `ledger_entry_lines`, although it's
    #: in the same family. No rule to guess: we reproduce what was observed.
    pagination_offset: bool = False


#: Scopes come from the official OpenAPI, operation by operation. An
#: endpoint documented as "one of x:readonly, x:all" is declared with the
#: readonly variant: `auth.scope_granted` accepts `x:all` on top of it.
RESOURCES: tuple[ResourceSpec, ...] = (
    # ── Accounting ────────────────────────────────────────────────────────
    ResourceSpec(
        "journals", "journals", Journal, "journals:readonly", "journal", pagination_offset=True
    ),
    ResourceSpec(
        "ledger_accounts",
        "ledger_accounts",
        LedgerAccount,
        "ledger_accounts:readonly",
        "ledgerAccount",
        pagination_offset=True,
    ),
    ResourceSpec(
        "ledger_entries",
        "ledger_entries",
        LedgerEntry,
        "ledger_entries:readonly",
        "ledgerEntry",
        pagination_offset=True,
    ),
    ResourceSpec(
        "ledger_entry_lines",
        "ledger_entry_lines",
        LedgerEntryLine,
        "ledger_entries:readonly",
        "ledgerEntryLine",
    ),
    ResourceSpec(
        "fiscal_years",
        "fiscal_years",
        FiscalYear,
        "fiscal_years:readonly",
        "fiscalYear",
        with_detail=False,
        filterable=False,
        pagination_offset=True,
    ),
    # ── Analytics ─────────────────────────────────────────────────────────
    ResourceSpec("categories", "categories", Category, "categories:readonly", "category"),
    ResourceSpec(
        "category_groups",
        "category_groups",
        CategoryGroup,
        "categories:readonly",
        "categoryGroup",
        filterable=False,
    ),
    # ── Third parties ─────────────────────────────────────────────────────
    ResourceSpec("customers", "customers", Customer, "customers:readonly", "customer"),
    ResourceSpec("suppliers", "suppliers", Supplier, "suppliers:readonly", "supplier"),
    ResourceSpec("products", "products", Product, "products:readonly", "product"),
    # ── Invoicing ─────────────────────────────────────────────────────────
    ResourceSpec(
        "customer_invoices",
        "customer_invoices",
        CustomerInvoice,
        "customer_invoices:readonly",
        "customerInvoice",
    ),
    ResourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        SupplierInvoice,
        "supplier_invoices:readonly",
        "supplierInvoice",
    ),
    ResourceSpec(
        "customer_invoice_templates",
        "customer_invoice_templates",
        GenericElement,
        "customer_invoice_templates:readonly",
        "customerInvoiceTemplate",
        with_detail=False,
        filterable=False,
    ),
    ResourceSpec("quotes", "quotes", GenericElement, "quotes:readonly", "quote"),
    ResourceSpec(
        "commercial_documents",
        "commercial_documents",
        GenericElement,
        "commercial_documents:readonly",
        "commercialDocument",
    ),
    ResourceSpec(
        "billing_subscriptions",
        "billing_subscriptions",
        GenericElement,
        "billing_subscriptions:readonly",
        "billingSubscription",
    ),
    ResourceSpec(
        "purchase_requests",
        "purchase_requests",
        GenericElement,
        "purchase_requests:readonly",
        "purchaseRequest",
    ),
    # ── Banking ───────────────────────────────────────────────────────────
    ResourceSpec(
        "bank_accounts",
        "bank_accounts",
        BankAccount,
        "bank_accounts:readonly",
        "bankAccount",
        filterable=False,
    ),
    ResourceSpec(
        "bank_establishments",
        "bank_establishments",
        BankEstablishment,
        "bank_establishments:readonly",
        "bankEstablishment",
        with_detail=False,
    ),
    ResourceSpec(
        "transactions",
        "transactions",
        Transaction,
        "transactions:readonly",
        "transaction",
    ),
    # ── Mandates ──────────────────────────────────────────────────────────
    ResourceSpec(
        "sepa_mandates",
        "sepa_mandates",
        GenericElement,
        "customer_mandates:readonly",
        "sepaMandate",
    ),
    ResourceSpec(
        "gocardless_mandates",
        "gocardless_mandates",
        GenericElement,
        "customer_mandates:readonly",
        "gocardlessMandate",
    ),
    ResourceSpec(
        "pro_account/mandates",
        "pro_account_mandates",
        GenericElement,
        "customer_mandates:readonly",
        "proAccountMandate",
        with_detail=False,
    ),
    ResourceSpec(
        "pro_account/mandate_migrations",
        "pro_account_mandate_migrations",
        GenericElement,
        "customer_mandates:readonly",
        "proAccountMandateMigration",
        with_detail=False,
    ),
)


@dataclass(frozen=True)
class SubResourceSpec:
    """A collection reachable UNDER an element — `/customer_invoices/{id}/payments`.

    These routes exist because v2 serves LINKS, not nested arrays: without
    them, an invoice would never give access to its lines.
    """

    parent: str  # parent segment
    parent_key: str  # parent's key in the dataset
    path: str  # sub-collection segment
    model: type
    scope: str | None
    #: (dataset, parent element) → the list to paginate.
    resolve: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]
    default_sort: str = "-id"
    params: tuple[str, ...] = field(default_factory=lambda: ("cursor", "limit"))


def _sub_resolver(key: str) -> Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]:
    """Resolver for collections indexed by parent id."""

    def resolve(data: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
        return list(data[key].get(parent["id"], []))

    return resolve


def _empty(_data: dict[str, Any], _parent: dict[str, Any]) -> list[dict[str, Any]]:
    """A collection that's always empty, but SERVED.

    Appendices, line sections, custom header fields, GED files: Boréal
    Conseil has none. The route exists anyway, and renders a well-formed
    empty page — because that's exactly what a connector must know how to
    handle, and returning 404 would teach it the opposite.
    """
    return []


def _ledger_entry_lines_of(data: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        line for line in data["ledger_entry_lines"] if line["ledger_entry"]["id"] == parent["id"]
    ]


def _lettered_lines(data: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    ids = set(parent["lettered_ledger_entry_lines"]["ids"])
    return [line for line in data["ledger_entry_lines"] if line["id"] in ids]


def _categories_of(data: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    """The analytical categories carried by the element.

    An invoice doesn't carry them directly: it gets them from its ledger
    entry. This is the chaining the provider exposes, and a consumer that
    reads `invoice["categories"]` as an array would never see it.
    """
    if "categories" in parent and isinstance(parent["categories"], list):
        return list(parent["categories"])
    reference = parent.get("ledger_entry")
    if not reference:
        return []
    entry = next((e for e in data["ledger_entries"] if e["id"] == reference["id"]), None)
    return list(entry["categories"]) if entry else []


def _matched_transactions(index_key: str) -> Callable[..., list[dict[str, Any]]]:
    def resolve(data: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
        ids = set(data[index_key].get(parent["id"], []))
        return [t for t in data["transactions"] if t["id"] in ids]

    return resolve


def _matched_invoices(data: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
    """Invoices — customer AND supplier — reconciled with a transaction."""
    result: list[dict[str, Any]] = []
    for index, collection in (
        ("matched_transactions_par_facture_client", "customer_invoices"),
        ("matched_transactions_par_facture_fournisseur", "supplier_invoices"),
    ):
        for invoice_id, transactions in data[index].items():
            if parent["id"] in transactions:
                invoice = next((f for f in data[collection] if f["id"] == invoice_id), None)
                if invoice is not None:
                    result.append(invoice)
    return result


def _payments(collection: str) -> Callable[..., list[dict[str, Any]]]:
    """The payments recorded ON the invoice.

    WARNING: these are NOT the reconciled transactions — the provider
    devotes a page to the distinction. A settled invoice carries a
    `payment`; the corresponding bank movement is a `matched_transaction`.
    Adding them up counts the cash-in twice.
    """

    def resolve(data: dict[str, Any], parent: dict[str, Any]) -> list[dict[str, Any]]:
        del data
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
    return resolve


SUB_RESOURCES: tuple[SubResourceSpec, ...] = (
    # ── Customer invoice ────────────────────────────────────────────────────
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "invoice_lines",
        InvoiceLine,
        "customer_invoices:readonly",
        _sub_resolver("customer_invoice_lines"),
    ),
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "invoice_line_sections",
        GenericElement,
        "customer_invoices:readonly",
        _empty,
    ),
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "payments",
        Payment,
        "customer_invoices:readonly",
        _payments("customer_invoices"),
    ),
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "matched_transactions",
        Transaction,
        "customer_invoices:readonly",
        _matched_transactions("matched_transactions_par_facture_client"),
    ),
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "appendices",
        GenericElement,
        "customer_invoices:readonly",
        _empty,
    ),
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "categories",
        Category,
        "customer_invoices:readonly",
        _categories_of,
    ),
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "custom_header_fields",
        GenericElement,
        "customer_invoices:readonly",
        _empty,
    ),
    SubResourceSpec(
        "customer_invoices",
        "customer_invoices",
        "installments",
        GenericElement,
        "customer_invoices:readonly",
        _empty,
        default_sort="deadline",
    ),
    # ── Supplier invoice ────────────────────────────────────────────────────
    SubResourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "invoice_lines",
        InvoiceLine,
        "supplier_invoices:readonly",
        _sub_resolver("supplier_invoice_lines"),
    ),
    SubResourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "categories",
        Category,
        "supplier_invoices:readonly",
        _categories_of,
    ),
    SubResourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "payments",
        Payment,
        "supplier_invoices:readonly",
        _payments("supplier_invoices"),
    ),
    SubResourceSpec(
        "supplier_invoices",
        "supplier_invoices",
        "matched_transactions",
        Transaction,
        "supplier_invoices:readonly",
        _matched_transactions("matched_transactions_par_facture_fournisseur"),
    ),
    # ── Third parties ───────────────────────────────────────────────────────
    SubResourceSpec(
        "customers",
        "customers",
        "contacts",
        Contact,
        "customers:readonly",
        _sub_resolver("customer_contacts"),
    ),
    SubResourceSpec(
        "customers",
        "customers",
        "categories",
        Category,
        "customers:readonly",
        _empty,
    ),
    SubResourceSpec(
        "suppliers",
        "suppliers",
        "categories",
        Category,
        "suppliers:readonly",
        _empty,
    ),
    # ── Accounting ──────────────────────────────────────────────────────────
    SubResourceSpec(
        "ledger_entries",
        "ledger_entries",
        "ledger_entry_lines",
        LedgerEntryLine,
        "ledger_entries:readonly",
        _ledger_entry_lines_of,
    ),
    SubResourceSpec(
        "ledger_entries",
        "ledger_entries",
        "dms_files",
        GenericElement,
        "ledger_entries:readonly",
        _empty,
    ),
    SubResourceSpec(
        "ledger_entry_lines",
        "ledger_entry_lines",
        "categories",
        Category,
        "ledger_entries:readonly",
        _categories_of,
    ),
    SubResourceSpec(
        "ledger_entry_lines",
        "ledger_entry_lines",
        "lettered_ledger_entry_lines",
        LedgerEntryLine,
        "ledger_entries:readonly",
        _lettered_lines,
    ),
    # ── Analytics ───────────────────────────────────────────────────────────
    SubResourceSpec(
        "category_groups",
        "category_groups",
        "categories",
        Category,
        "categories:readonly",
        lambda data, parent: [
            c for c in data["categories"] if c["category_group"]["id"] == parent["id"]
        ],
    ),
    # ── Banking ─────────────────────────────────────────────────────────────
    SubResourceSpec(
        "transactions",
        "transactions",
        "categories",
        Category,
        "transactions:readonly",
        _categories_of,
    ),
    SubResourceSpec(
        "transactions",
        "transactions",
        "matched_invoices",
        CustomerInvoice,
        "transactions:readonly",
        _matched_invoices,
    ),
    # ── Periphery ───────────────────────────────────────────────────────────
    SubResourceSpec(
        "quotes",
        "quotes",
        "invoice_lines",
        InvoiceLine,
        "quotes:readonly",
        _empty,
    ),
    SubResourceSpec(
        "quotes",
        "quotes",
        "invoice_line_sections",
        GenericElement,
        "quotes:readonly",
        _empty,
    ),
    SubResourceSpec(
        "quotes",
        "quotes",
        "appendices",
        GenericElement,
        "quotes:readonly",
        _empty,
    ),
    SubResourceSpec(
        "commercial_documents",
        "commercial_documents",
        "invoice_lines",
        InvoiceLine,
        "commercial_documents:readonly",
        _empty,
    ),
    SubResourceSpec(
        "commercial_documents",
        "commercial_documents",
        "invoice_line_sections",
        GenericElement,
        "commercial_documents:readonly",
        _empty,
    ),
    SubResourceSpec(
        "commercial_documents",
        "commercial_documents",
        "appendices",
        GenericElement,
        "commercial_documents:readonly",
        _empty,
    ),
    SubResourceSpec(
        "billing_subscriptions",
        "billing_subscriptions",
        "invoice_lines",
        InvoiceLine,
        "billing_subscriptions:readonly",
        _empty,
    ),
    SubResourceSpec(
        "billing_subscriptions",
        "billing_subscriptions",
        "invoice_line_sections",
        GenericElement,
        "billing_subscriptions:readonly",
        _empty,
    ),
)

#: The ten changelogs. The first seven are documented in the guide; `quotes`
#: and the two `*_categories` ones only appear in the reference — a consumer
#: that stuck to the guide would miss them.
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
#  The request pipeline
# ═════════════════════════════════════════════════════════════════════════════


def _current_rate_limit_headers(path: str) -> dict[str, str]:
    """The `ratelimit-*` headers, on EVERY response.

    The provider serves them even on a 200: this is what lets a client
    throttle itself BEFORE getting rate limited. A mock that only served
    them on 429s would teach the consumer not to read them.
    """
    seen = engine.request_counts.get(path, 0)
    remaining = max(0, settings.rate_limit - seen)
    reset = int(engine.now()) + int(settings.rate_window)
    return rate_limit_headers(settings.rate_limit, remaining, reset)


def current_virtual_time() -> datetime:
    """The mock's reference instant — the dataset's anchor, not `now()`.

    It advances with the evolution timeline: each event is worth
    `EPOCH + (k+1) x interval`, so the "now" of a mock that has played N
    events is `EPOCH + N x interval`. This is what makes changelog
    retention reproducible: the same mock, advanced by the same number of
    steps, retains exactly the same events — whatever day it's run on.
    """
    from .evolution import EPOQUE

    return EPOQUE + timedelta(seconds=settings.evolution_interval * state.evolution.rank)


def _dispatch_injections(path: str, rank: int) -> Response | None:
    """The single dispatch point, evaluated before authentication."""
    if (rule := engine.first("latency", path)) is not None and rule.consume():
        time.sleep(rule.seconds)

    rule = engine.first("rate_limit", path)
    if rule is not None and rank > rule.after_requests and rule.consume():
        # `ratelimit-remaining: 0` on a 429 — by definition: it's precisely
        # because nothing is left that we're rate limited. Serving the
        # nominal counter here would make a client believe it can retry
        # right away, and it loops.
        headers = rate_limit_headers(
            settings.rate_limit, 0, int(engine.now() + settings.rate_window)
        )
        return rate_limit_error(rule.retry_after_seconds, headers)

    if (rule := engine.first("auth_reject", path)) is not None and rule.consume():
        return token_error()

    if (rule := engine.first("scope_reject", path)) is not None and rule.consume():
        return scope_error(rule.missing_scope)

    if (rule := engine.first("cursor_reject", path)) is not None and rule.consume():
        return error(400, "Invalid cursor")

    if (rule := engine.first("status", path)) is not None and rule.consume():
        return error(rule.status, f"Injected failure ({rule.status})")
    return None


def _prelude(request: Request, path: str, scope: str | None) -> Response | None:
    """The shared pipeline. Returns `None` when the request may proceed."""
    params = dict(request.query_params)
    state.advance_evolution(engine.now())
    rank = engine.observe(path, params)

    if (rejection := _dispatch_injections(path, rank)) is not None:
        return rejection

    token = token_from_header(request.headers.get("Authorization"))
    if not token_is_valid(token):
        return token_error()
    if not scope_granted(scope):
        return scope_error(scope or "")
    return None


def _ok(content: Any, path: str) -> JSONResponse:
    return JSONResponse(content=content, headers=_current_rate_limit_headers(path))


def _page(
    elements: list[dict[str, Any]],
    request: Request,
    *,
    default_sort: str,
    filterable: bool,
    maximum: int | None = None,
    pagination_offset: bool = False,
) -> dict[str, Any] | Response:
    """Filters, sorts, paginates — in that order, which is the only correct one.

    Sorting before filtering would give the same result but cost more;
    paginating before filtering would give a WRONG result (short pages, then
    empty ones, without `has_more` saying so). The order therefore matters.
    """
    try:
        if filterable:
            elements = apply_filter(elements, parse_filter(request.query_params.get("filter")))
        elements = apply_sort(elements, request.query_params.get("sort"), default=default_sort)
        limit = requested_limit(request.query_params.get("limit"), maximum=maximum)
        return paginate(
            elements,
            cursor=request.query_params.get("cursor"),
            limit=limit,
            key=default_sort.lstrip("-") if default_sort.lstrip("-") in {"id"} else "id",
            offset=pagination_offset,
        )
    except (InvalidFilter, InvalidSort, InvalidLimit, InvalidCursor) as exc:
        return error(400, str(exc))


# ═════════════════════════════════════════════════════════════════════════════
#  Query parameters, declared FOR THE CONTRACT
# ═════════════════════════════════════════════════════════════════════════════
#
# ┌─ WHY THEY AREN'T IN THE HANDLERS' SIGNATURE ────────────────────────────────┐
# │ Declaring `limit: int` as an argument would make FastAPI validate IN OUR   │
# │ PLACE, and a `limit=abc` would render FastAPI's 422 — an error shape that  │
# │ doesn't exist at Pennylane, where it's a 400 in the `{"error","status"}`   │
# │ envelope. The mock would teach the consumer a false error-handling model.  │
# │                                                                            │
# │ The parameters are therefore READ from `request.query_params` and         │
# │ validated by `pagination.py` / `filters.py`, and declared here only so    │
# │ the published contract describes them. This is the contract insights360   │
# │ copies: if it didn't carry `cursor`, a consumer wouldn't know it has to    │
# │ paginate.                                                                  │
# └────────────────────────────────────────────────────────────────────────────┘


def _cursor_param() -> dict[str, Any]:
    return {
        "name": "cursor",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "Pagination cursor, OPAQUE. Reuse the previous response's "
            "`next_cursor` as-is; decoding it means leaning on an "
            "implementation detail the provider's documentation shows under "
            "three incompatible forms. An unreadable cursor renders 400."
        ),
    }


def _limit_param(maximum: int) -> dict[str, Any]:
    return {
        "name": "limit",
        "in": "query",
        "required": False,
        "schema": {"type": "integer", "minimum": 1, "maximum": maximum},
        "description": (
            f"Page size. Default 20, between 1 and {maximum}. An out-of-bounds "
            "value renders 400 — it is NOT silently clamped."
        ),
    }


def _sort_param(default: str) -> dict[str, Any]:
    return {
        "name": "sort",
        "in": "query",
        "required": False,
        "schema": {"type": "string", "default": default},
        "description": (
            f"Sort field, prefixed with `-` for descending order. Default `{default}` "
            "— i.e. DESCENDING if nothing is specified, which is the opposite of "
            "intuition."
        ),
    }


def _filter_param() -> dict[str, Any]:
    return {
        "name": "filter",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "example": '[{"field": "date", "operator": "gteq", "value": "2026-01-01"}]',
        "description": (
            "JSON array of `{field, operator, value}` objects, combined with AND. "
            "Operators: eq, not_eq, lt, lteq, gt, gteq, in, not_in, start_with. "
            "WARNING: the cursor does NOT ENCODE filters: they must be REPLAYED on "
            "every page, otherwise pages 2+ return unfiltered results."
        ),
    }


def _list_params(*, default_sort: str, filterable: bool, maximum: int) -> list[dict[str, Any]]:
    """A list endpoint's query parameters.

    WARNING: the PATH parameter isn't in here: FastAPI ADDS the
    `openapi_extra` entries to those it infers from the signature, it
    doesn't replace them. Putting it here would publish it twice, and a
    client generator would produce a function with two identical arguments.
    """
    params = [_cursor_param(), _limit_param(maximum), _sort_param(default_sort)]
    if filterable:
        params.append(_filter_param())
    return params


# ═════════════════════════════════════════════════════════════════════════════
#  The application
# ═════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Pennylane Company API v2 — mock",
    version=VERSION,
    description=(
        "Read-only mock of the Pennylane v2 API on the « Boréal Conseil » world. "
        "Cursor envelope `{items, has_more, next_cursor}`, amounts as STRINGS, "
        "granular scopes, changelogs for incremental extraction."
    ),
    docs_url="/docs",
    redoc_url=None,
)
router = APIRouter()


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    """Liveness probe — NOT authenticated, outside the provider surface.

    The image's healthcheck polls it, and the consumer's `depends_on:
    service_healthy` relies on it. Putting it behind the token would make the
    container "unhealthy" for a configuration problem.
    """
    return {"status": "ok", "service": "pennylane-mock"}


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    """Unknown routes and methods: the Pennylane envelope, not FastAPI's 404.

    A `{"detail": "Not Found"}` would teach the consumer an error shape that
    doesn't exist at the provider — and its error-handling code would break
    the day it talks to the real API.
    """
    del request
    if exc.status_code in (404, 405):
        return not_found_error()
    return error(exc.status_code, str(exc.detail))


# ── /me: the only endpoint without a scope ────────────────────────────────────


@router.get(
    f"{PREFIX}/me",
    response_model=UserProfile,
    responses=ERROR_RESPONSES,
    tags=["Users"],
    summary="User and company profile",
)
def me(request: Request) -> Any:
    """A connector's smoke test: who am I, and what can I read?

    No scope required — this is precisely the endpoint used to discover
    which scopes are available. A connector must call it BEFORE opening its
    pipeline: failing on authentication beats a half-done run.
    """
    path = f"{PREFIX}/me"
    if (rejection := _prelude(request, path, None)) is not None:
        return rejection
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
        path,
    )


# ── The trial balance ──────────────────────────────────────────────────────────


@router.get(
    f"{PREFIX}/trial_balance",
    response_model=Page[TrialBalanceLine],
    responses=ERROR_RESPONSES,
    tags=["Accounting"],
    summary="General trial balance over a period",
    openapi_extra={
        "parameters": [
            {
                "name": "period_start",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "format": "date"},
                "description": "Start of the period. REQUIRED.",
            },
            {
                "name": "period_end",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "format": "date"},
                "description": "End of the period. REQUIRED.",
            },
            {
                "name": "is_auxiliary",
                "in": "query",
                "required": False,
                "schema": {"type": "boolean"},
                "description": (
                    "Detail auxiliary accounts. When false, they are AGGREGATED "
                    "into their root: summing both views doubles the assets."
                ),
            },
            _cursor_param(),
            _limit_param(1000),
        ]
    },
)
def trial_balance(request: Request) -> Any:
    """`period_start` and `period_end` are REQUIRED.

    This is the mock's only resource that requires parameters — and it's
    deliberate at the provider: a trial balance without a period makes no
    sense. A consumer that omits them must get 400, not a balance for the
    current fiscal year chosen on its behalf.
    """
    path = f"{PREFIX}/trial_balance"
    if (rejection := _prelude(request, path, "trial_balance:readonly")) is not None:
        return rejection

    from .dataset.realiste import balance

    raw_start = request.query_params.get("period_start")
    raw_end = request.query_params.get("period_end")
    if not raw_start or not raw_end:
        return error(400, "period_start and period_end are required")
    try:
        start, end = date.fromisoformat(raw_start), date.fromisoformat(raw_end)
    except ValueError:
        return error(400, "period_start and period_end must be ISO 8601 dates")

    auxiliary = (request.query_params.get("is_auxiliary") or "").lower() in {
        "1",
        "true",
        "yes",
    }
    lines = balance(
        state.dataset["ledger_entry_lines"],
        state.dataset["ledger_accounts"],
        debut=start,
        fin=end,
        auxiliaires=auxiliary,
    )
    result = _page(
        lines,
        request,
        default_sort="number",
        filterable=False,
        maximum=settings.max_limit_changelog,
    )
    if isinstance(result, Response):
        return result
    return _ok(result, path)


# ═════════════════════════════════════════════════════════════════════════════
#  The route factory
# ═════════════════════════════════════════════════════════════════════════════


def _mount_resource(spec: ResourceSpec) -> None:
    """Mounts a resource's list and detail routes.

    The factory exists to bind `spec` at each iteration: without it, the
    closure would capture the loop variable and all 24 resources would end
    up serving the last one.
    """
    base = f"{PREFIX}/{spec.path}"

    if spec.with_list:

        @router.get(
            base,
            response_model=Page[spec.model],  # type: ignore[name-defined]
            responses=ERROR_RESPONSES,
            tags=[spec.singular],
            summary=f"List {spec.path}",
            name=f"list_{spec.key}",
            openapi_extra={
                "parameters": _list_params(
                    default_sort=spec.default_sort,
                    filterable=spec.filterable,
                    maximum=settings.max_limit,
                )
            },
        )
        def list_resource(request: Request) -> Any:
            if (rejection := _prelude(request, base, spec.scope)) is not None:
                return rejection
            result = _page(
                list(state.dataset[spec.key]),
                request,
                default_sort=spec.default_sort,
                filterable=spec.filterable,
                pagination_offset=spec.pagination_offset,
            )
            if isinstance(result, Response):
                return result
            return _ok(result, base)

    if spec.with_detail:

        @router.get(
            base + "/{ident}",
            response_model=spec.model,
            responses=ERROR_RESPONSES,
            tags=[spec.singular],
            summary=f"Detail of a {spec.path} element",
            name=f"get_{spec.key}",
        )
        def detail(ident: int, request: Request) -> Any:
            path = f"{PREFIX}/{spec.path}/{ident}"
            if (rejection := _prelude(request, path, spec.scope)) is not None:
                return rejection
            element = state.index().get((spec.key, ident))
            if element is None:
                return not_found_error()
            return _ok(element, path)


def _mount_sub_resource(spec: SubResourceSpec) -> None:
    model_path = f"{PREFIX}/{spec.parent}/{{ident}}/{spec.path}"

    @router.get(
        model_path,
        response_model=Page[spec.model],  # type: ignore[name-defined]
        responses=ERROR_RESPONSES,
        tags=[spec.parent],
        summary=f"{spec.path} of a {spec.parent} element",
        name=f"list_{spec.parent}_{spec.path}",
        openapi_extra={
            "parameters": _list_params(
                default_sort=spec.default_sort,
                filterable=spec.path == "ledger_entry_lines",
                maximum=settings.max_limit,
            )
        },
    )
    def list_sub_resource(ident: int, request: Request) -> Any:
        path = f"{PREFIX}/{spec.parent}/{ident}/{spec.path}"
        if (rejection := _prelude(request, path, spec.scope)) is not None:
            return rejection
        parent = state.index().get((spec.parent_key, ident))
        if parent is None:
            return not_found_error()
        result = _page(
            spec.resolve(state.dataset, parent),
            request,
            default_sort=spec.default_sort,
            # A sub-collection only accepts `filter` on
            # `/ledger_entries/{id}/ledger_entry_lines` at the provider.
            # Elsewhere it's ignored — not refused.
            filterable=spec.path == "ledger_entry_lines",
        )
        if isinstance(result, Response):
            return result
        return _ok(result, path)


def _mount_changelog(family: str, scope: str) -> None:
    path = f"{PREFIX}/changelogs/{family}"

    @router.get(
        path,
        response_model=Page[ChangelogEvent],
        responses=ERROR_RESPONSES,
        tags=["Changelogs"],
        summary=f"Changes on {family}",
        name=f"changelog_{family}",
        openapi_extra={
            "parameters": [
                _cursor_param(),
                _limit_param(settings.max_limit_changelog),
                {
                    "name": "start_date",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string", "format": "date-time"},
                    "example": "2026-07-15T09:00:00Z",
                    "description": (
                        "RFC 3339 lower bound. Without it, the oldest changes are "
                        "returned. Four-week retention: an older bound renders "
                        "**422**. `start_date` and `cursor` together render "
                        "**400** — pagination continues a window, it doesn't "
                        "open a new one."
                    ),
                },
            ]
        },
    )
    def changelog_route(request: Request) -> Any:
        if (rejection := _prelude(request, path, scope)) is not None:
            return rejection

        raw_since = request.query_params.get("start_date")
        cursor = request.query_params.get("cursor")
        # `start_date` AND `cursor` together → 400. Pagination CONTINUES a
        # window, it doesn't open a new one: without this refusal, a
        # consumer that resends its start_date on every page replays the
        # first one forever and believes it read everything.
        if raw_since and cursor:
            return error(400, "start_date and cursor cannot be used together")
        # The reference instant is the MOCK's, not the wall clock's. The
        # dataset is anchored on July 15, 2026: evaluating a four-week
        # retention against the real date would render 422 on any
        # legitimate `start_date` from the day after the dataset was built,
        # and the mock would stop working without a line of code having
        # moved.
        now = current_virtual_time()
        try:
            since = chg.parse_start_date(raw_since)
            chg.check_window(since, now)
        except chg.InvalidDate as exc:
            return error(400, str(exc))
        except chg.WindowTooOld as exc:
            return error(422, str(exc))

        events = chg.select(state.dataset["changelogs"].get(family, []), since, now)
        try:
            limit = requested_limit(
                request.query_params.get("limit"), maximum=settings.max_limit_changelog
            )
            # STRICTLY ASCENDING chronological order, never `-id`: this is
            # the only list family in the dialect that doesn't follow the
            # default.
            result = paginate(events, cursor=cursor, limit=limit, key="id")
        except (InvalidLimit, InvalidCursor) as exc:
            return error(400, str(exc))
        return _ok(result, path)


def _mount_customer_detail(segment: str, expected_type: str) -> None:
    """`/company_customers/{id}` and `/individual_customers/{id}`.

    They serve the SAME entity as `/customers/{id}`, but render 404 when the
    type doesn't match. This lets a consumer validate the type without
    reading the discriminant — and the 404 is the only way the provider
    tells it so.
    """

    @router.get(
        f"{PREFIX}/{segment}/{{ident}}",
        response_model=Customer,
        responses=ERROR_RESPONSES,
        tags=["customer"],
        summary=f"Detail of a {expected_type} customer",
        name=f"get_{segment}",
    )
    def customer_detail(ident: int, request: Request) -> Any:
        path = f"{PREFIX}/{segment}/{ident}"
        if (rejection := _prelude(request, path, "customers:readonly")) is not None:
            return rejection
        element = state.index().get(("customers", ident))
        if element is None or element["customer_type"] != expected_type:
            return not_found_error()
        return _ok(element, path)


def _mount_export(segment: str, key: str, scope: str) -> None:
    """RETRIEVAL of an export. Its creation is a POST, out of scope: the
    mock therefore serves pre-existing exports, which is enough to exercise
    the second half of the create → retrieve pair."""

    @router.get(
        f"{PREFIX}/exports/{segment}/{{ident}}",
        response_model=GenericElement,
        responses=ERROR_RESPONSES,
        tags=["Exports"],
        summary=f"Retrieve a {segment} export",
        name=f"get_export_{segment}",
    )
    def export_detail(ident: int, request: Request) -> Any:
        path = f"{PREFIX}/exports/{segment}/{ident}"
        if (rejection := _prelude(request, path, scope)) is not None:
            return rejection
        element = state.index().get((key, ident))
        if element is None:
            return not_found_error()
        return _ok(element, path)


@router.get(
    f"{PREFIX}/pa_registrations",
    response_model=Page[GenericElement],
    responses=ERROR_RESPONSES,
    tags=["PA registrations"],
    summary="Registrations with authorized e-invoicing platforms",
)
def pa_registrations(request: Request) -> Any:
    """The OpenAPI declares NO scope and NO parameter on this endpoint — the
    only one on the surface in this case besides `/me`. It is therefore NOT
    paginated: the response carries the envelope, but `has_more` is always
    false there.
    """
    path = f"{PREFIX}/pa_registrations"
    if (rejection := _prelude(request, path, None)) is not None:
        return rejection
    return _ok(
        {"items": state.dataset["pa_registrations"], "has_more": False, "next_cursor": None},
        path,
    )


for _spec in RESOURCES:
    _mount_resource(_spec)
for _sub_spec in SUB_RESOURCES:
    _mount_sub_resource(_sub_spec)
for _family, _changelog_scope in CHANGELOGS:
    _mount_changelog(_family, _changelog_scope)
for _segment, _customer_type in (
    ("company_customers", "company"),
    ("individual_customers", "individual"),
):
    _mount_customer_detail(_segment, _customer_type)
for _segment, _export_key, _export_scope in (
    ("general_ledgers", "general_ledger_exports", "exports:gl"),
    ("analytical_general_ledgers", "analytical_general_ledger_exports", "exports:agl"),
    ("fecs", "fec_exports", "exports:fec"),
):
    _mount_export(_segment, _export_key, _export_scope)

app.include_router(router)

# The control plane isn't "mounted then forbidden": when disabled, the
# surface doesn't exist. This is what makes it impossible to accidentally
# leave it open on a cluster.
if settings.admin_enabled:
    from .admin import router as admin_router

    app.include_router(admin_router)


# ═════════════════════════════════════════════════════════════════════════════
#  The contract
# ═════════════════════════════════════════════════════════════════════════════


def openapi_contract() -> dict[str, Any]:
    """The PUBLISHED contract — the provider surface, and only that.

    `/__admin` and `/health` are affordances of the MOCK: publishing them
    would pass off as Pennylane API something that isn't, and `/__admin` is
    only mounted conditionally — the contract would then depend on the
    generation environment, which would make it uninterpretable.
    """
    schema = app.openapi()
    paths = {
        path: operations for path, operations in schema["paths"].items() if path.startswith(PREFIX)
    }
    contract: dict[str, Any] = {
        "openapi": schema["openapi"],
        "info": dict(schema["info"]),
        "servers": [{"url": "https://app.pennylane.com"}],
        "paths": paths,
    }
    components = schema.get("components", {})
    if components:
        contract["components"] = _prune_schemas(components, paths)
    return contract


def _prune_schemas(components: dict[str, Any], paths: dict[str, Any]) -> dict[str, Any]:
    """Removes schemas left orphaned once /__admin is stripped out.

    Without this pruning, the contract would carry the control plane's
    models — shapes that exist nowhere at the provider.
    """
    import json
    import re

    schemas = dict(components.get("schemas", {}))
    used: set[str] = set()
    to_visit = set(re.findall(r"#/components/schemas/([A-Za-z0-9_.\[\]-]+)", json.dumps(paths)))
    while to_visit:
        name = to_visit.pop()
        if name in used or name not in schemas:
            continue
        used.add(name)
        to_visit |= set(
            re.findall(r"#/components/schemas/([A-Za-z0-9_.\[\]-]+)", json.dumps(schemas[name]))
        )
    result = dict(components)
    result["schemas"] = {name: schemas[name] for name in sorted(used)}
    return result
