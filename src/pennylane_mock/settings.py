"""Configuration — everything via environment variables, no file.

Same mechanism as the ecosystem's four other mocks: an object reread on the
fly by `reload()`, so a test can change a value without reloading the
module. It's also the only mechanism that works identically in docker
compose, a Kubernetes Deployment and a GitHub Actions service.

The prefix is `PENNYLANE_MOCK_*`. There is deliberately NO `.env.example`
here: the example file lives with the consumer (insights360), because it's
the one that has to document how to wire the five sources together.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: The default token. Static — Pennylane issues long-lived company tokens,
#: not session tokens (cf. docs/EXTRACTION.md §auth).
DEFAULT_TOKEN = "mock-pennylane-token"

#: The read scopes for the ENTIRE v2 surface, taken from
#: https://pennylane.readme.io/docs/v2-scopes (as of 2026-03-31). This is the
#: mock's default scope set: a read-only consumer has all of them.
#:
#: `ledger` has NO `:readonly` variant at the provider — it's a single
#: read/write scope, and that's what makes access to accounting-entry
#: attachments broader than everything else. Reproduced as-is.
READ_SCOPES: tuple[str, ...] = (
    # SALES
    "customers:readonly",
    "products:readonly",
    "customer_invoices:readonly",
    "quotes:readonly",
    "customer_mandates:readonly",
    "billing_subscriptions:readonly",
    "commercial_documents:readonly",
    "customer_invoice_templates:readonly",
    # PURCHASES
    "suppliers:readonly",
    "supplier_invoices:readonly",
    "purchase_requests:readonly",
    # ACCOUNTING
    "ledger",
    "trial_balance:readonly",
    "exports:fec",
    "exports:agl",
    # WARNING: `exports:gl` is NOT listed on the "Understand Scopes" page (as
    # of 2026-03-31), which only knows `exports:fec` and `exports:agl`. The
    # `exportGeneralLedger` reference, though, explicitly requires it. The
    # guide page is therefore incomplete — cf. docs/UNVERIFIED-FIELDS.md.
    "exports:gl",
    "fiscal_years:readonly",
    "journals:readonly",
    "ledger_accounts:readonly",
    "ledger_entries:readonly",
    # ANALYTICS
    "categories:readonly",
    # BANKING
    "transactions:readonly",
    "bank_accounts:readonly",
    "bank_establishments:readonly",
    # CORE
    "file_attachments:readonly",
)


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Configuration state, reread on the fly by `reload()`."""

    token: str = ""

    #: Scopes carried by the token. The mock's "403" lever: removing a scope
    #: reproduces a token with a restricted scope set, exactly as at the
    #: provider — and it's the most frequent failure in real integrations.
    scopes: frozenset[str] = frozenset()

    seed: int = 42

    # /__admin control plane. Closed by default: it only makes sense in test.
    admin_enabled: bool = False
    admin_token: str = "mock-admin-token"

    # ── Pagination ───────────────────────────────────────────────────────────
    # Default of 20 everywhere. The CEILING, though, isn't the same across
    # endpoints — 100 on ordinary lists, 1000 on changelogs — and the OpenAPI
    # declares it that way, endpoint by endpoint. Hence two settings.
    #
    # An out-of-bounds `limit` renders 400: it is NOT silently clamped. A
    # silent cap would make a pipeline believe it requested 5000 rows and got
    # them all, when it actually read 100 — the costliest pagination default,
    # because it shows up nowhere.
    default_limit: int = 20
    max_limit: int = 100
    max_limit_changelog: int = 1000

    #: Rate limit — 25 requests / 5 s per token, in force on ALL endpoints
    #: (production as well as the sandbox). The `ratelimit-*` headers go out
    #: on every response, not just on 429s.
    rate_limit: int = 25
    rate_window: float = 5.0
    rate_limit_enforced: bool = False

    #: The identity returned by /me.
    company_name: str = "Boréal Conseil"
    company_id: int = 918_244
    company_reg_no: str = "824419236"

    # ── Temporal evolution (incremental extraction) ──────────────────────────
    # The dataset LIVES: a scripted event every `evolution_interval` seconds
    # (invoice issued, transaction reconciled, entry posted…), each logged to
    # the changelog. Setting this to false — or the interval to 0 — freezes
    # the dataset for uses that require byte-for-byte stable content
    # (insights360's idempotence gate).
    evolution_enabled: bool = True
    evolution_interval: float = 60.0

    #: Changelog retention, in days. The provider retains 4 weeks and REFUSES
    #: an older `start_date` — this is a constraint the consumer must hit in
    #: test, not in production.
    changelog_retention_days: int = 28

    #: Serves the OPTIONAL fields that the real tenant never fills in.
    #:
    #: ┌─ WHY THE DEFAULT IS "NO" ────────────────────────────────────────────┐
    #: │ `analytical_code` (categories and allocations), `product` (customer │
    #: │ invoice line) and `ledger_account` (supplier invoice line) are      │
    #: │ declared by the OpenAPI, served by the API — and `null` on 100% of  │
    #: │ the rows of the tenant checked on 2026-09-07: 0 analytical codes    │
    #: │ across 159 categories, no product on 1,898 customer invoices, no    │
    #: │ account on 4,559 supplier invoices.                                 │
    #: │                                                                      │
    #: │ The mock ALWAYS filled them in. A consumer reading them was          │
    #: │ therefore green in development and red in production — and not     │
    #: │ from a "null value" but from a "column does not exist", because a   │
    #: │ loader that infers its schema doesn't materialize a column it never │
    #: │ saw a value for. That happened, and this default exists to          │
    #: │ reproduce it.                                                       │
    #: │                                                                      │
    #: │ Setting it to `1` restores the rich form: it remains legitimate,    │
    #: │ another tenant may very well fill in these three fields.            │
    #: └──────────────────────────────────────────────────────────────────────┘
    optional_fields_served: bool = False

    extra: dict[str, str] = field(default_factory=dict)

    def reload(self) -> None:
        self.token = os.environ.get("PENNYLANE_MOCK_TOKEN", DEFAULT_TOKEN)
        raw = os.environ.get("PENNYLANE_MOCK_SCOPES")
        self.scopes = (
            frozenset(READ_SCOPES)
            if raw is None
            else frozenset(s.strip() for s in raw.split(",") if s.strip())
        )
        self.seed = int(os.environ.get("PENNYLANE_MOCK_SEED", "42"))
        self.admin_enabled = _flag("PENNYLANE_MOCK_ADMIN_ENABLED", False)
        self.admin_token = os.environ.get("PENNYLANE_MOCK_ADMIN_TOKEN", "mock-admin-token")
        self.default_limit = int(os.environ.get("PENNYLANE_MOCK_DEFAULT_LIMIT", "20"))
        self.max_limit = int(os.environ.get("PENNYLANE_MOCK_MAX_LIMIT", "100"))
        self.max_limit_changelog = int(os.environ.get("PENNYLANE_MOCK_MAX_LIMIT_CHANGELOG", "1000"))
        self.rate_limit = int(os.environ.get("PENNYLANE_MOCK_RATE_LIMIT", "25"))
        self.rate_window = float(os.environ.get("PENNYLANE_MOCK_RATE_WINDOW", "5"))
        self.rate_limit_enforced = _flag("PENNYLANE_MOCK_RATE_LIMIT_ENFORCED", False)
        self.company_name = os.environ.get("PENNYLANE_MOCK_COMPANY", "Boréal Conseil")
        self.company_id = int(os.environ.get("PENNYLANE_MOCK_COMPANY_ID", "918244"))
        self.company_reg_no = os.environ.get("PENNYLANE_MOCK_COMPANY_REG_NO", "824419236")
        self.evolution_enabled = _flag("PENNYLANE_MOCK_EVOLUTION_ENABLED", True)
        self.evolution_interval = float(os.environ.get("PENNYLANE_MOCK_EVOLUTION_INTERVAL", "60"))
        self.changelog_retention_days = int(
            os.environ.get("PENNYLANE_MOCK_CHANGELOG_RETENTION_DAYS", "28")
        )
        self.optional_fields_served = _flag("PENNYLANE_MOCK_OPTIONAL_FIELDS", False)


settings = Settings()
settings.reload()
