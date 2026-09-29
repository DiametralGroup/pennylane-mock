# pennylane-mock

[![CI](https://github.com/LittleBigCode/pennylane-mock/actions/workflows/ci.yml/badge.svg)](https://github.com/LittleBigCode/pennylane-mock/actions/workflows/ci.yml)

Mock of the **Pennylane Company API v2** (read surface), shipped both as a
**container image** and as an **installable Python package**. It serves the
**91 GET operations** of the documented v2 surface over one coherent accounting
world — the books of *Boréal Conseil*, the same fictional French IT consultancy
that already populates `boondmanager-mock`, `entra-mock`, `linkedin-mock` and
`ga-mock`.

The shapes are not written from memory. Pennylane embeds a **full OpenAPI
fragment in every one of its 163 reference pages**; those were downloaded and
merged into a single spec (123 paths, 158 operations, 91 of them GET), and that
spec is what this mock reproduces. The method is replayable — see
[`docs/EXTRACTION.md`](docs/EXTRACTION.md).

## Start in one command

```bash
# Pre-built image from GitHub Container Registry (published by CI):
docker run -p 8014:8000 -e PENNYLANE_MOCK_ADMIN_ENABLED=true \
    ghcr.io/littlebigcode/pennylane-mock:latest

# Or build locally (admin plane open, evolution active):
docker compose up --build           # or: make up
curl http://localhost:8014/health

# Or locally without Docker:
make bootstrap && make run
```

The image is non-root (uid/gid 65532) with a built-in healthcheck, so
`depends_on: condition: service_healthy` works on the consumer side. Port
**8014** is this mock's slot in the insights360 ecosystem (8010 boondmanager,
8011 entra, 8012 linkedin, 8013 ga are taken — the map is shared).

## Credentials

Everything is overridable through environment variables (see
[Configuration](#configuration)); out of the box:

| Role | Variable | Default value |
|---|---|---|
| Company API token | `PENNYLANE_MOCK_TOKEN` | `mock-pennylane-token` |
| Granted scopes | `PENNYLANE_MOCK_SCOPES` | all 24 read scopes of the v2 surface |
| `/__admin` control plane | `PENNYLANE_MOCK_ADMIN_TOKEN` | `mock-admin-token` (header `X-Mock-Admin-Token`; only mounted when `PENNYLANE_MOCK_ADMIN_ENABLED=true`) |

```bash
curl -H "Authorization: Bearer mock-pennylane-token" \
     "http://localhost:8014/api/external/v2/customer_invoices?limit=2"

# Who am I, and what may I read? The natural smoke test for a connector —
# it is the ONLY endpoint that requires no scope.
curl -H "Authorization: Bearer mock-pennylane-token" \
     http://localhost:8014/api/external/v2/me
```

Heads-up, exactly as on the real API: **401** means the token is missing,
invalid or expired — the three are indistinguishable. **403** means the token
is fine but lacks a scope, and the message *names the missing scope*. Neither is
retryable.

## Two modes, both maintained

```python
# In-process — for test suites.
from fastapi.testclient import TestClient
import pennylane_mock as mock

client = TestClient(mock.app)
mock.state.reset(seed=42)
```

```bash
# In a container — for docker compose and CI services.
python -m pennylane_mock
```

The property worth keeping: **the application your stack queries IS the one the
tests exercise.** The container mode is also what makes the `/__admin` control
plane necessary — outside the process, a test can no longer mutate state in
Python.

## Served surface

All **91 GET operations**, mounted from a declarative table
(`RESOURCES` / `SUB_RESOURCES` / `CHANGELOGS` in `app.py`) rather than 91
hand-written handlers. Every route goes through the same prelude —
*evolution → observation → injections → token → scope* — so no route can escape
a scope check or a failure rule.

| Domain | Resources |
|---|---|
| Accounting | `journals`, `ledger_accounts`, `ledger_entries` (+lines, DMS files), `ledger_entry_lines` (+categories, lettered lines), `trial_balance`, `fiscal_years` |
| Sales | `customer_invoices` (+lines, sections, payments, matched transactions, appendices, categories, custom header fields, installments), `customer_invoice_templates`, `quotes`, `commercial_documents`, `billing_subscriptions` |
| Purchases | `supplier_invoices` (+lines, categories, payments, matched transactions), `purchase_requests` |
| Third parties | `customers` (company **and** individual, +contacts, categories), `suppliers`, `products` |
| Banking | `bank_accounts`, `bank_establishments`, `transactions` (+categories, matched invoices) |
| Analytics | `categories`, `category_groups` |
| Mandates | `sepa_mandates`, `gocardless_mandates`, `pro_account/mandates`, `pro_account/mandate_migrations` |
| Exports | general ledger, analytical general ledger, FEC (retrieval) |
| Changelogs | 10 families — `customer_invoices`, `supplier_invoices`, `customers`, `suppliers`, `products`, `transactions`, `quotes`, `ledger_entry_lines`, and both `*_categories` |
| Misc | `me`, `pa_registrations` |

**Writes are out of scope.** This mock's consumer reads; it does not write. A
POST/PUT/DELETE answers 404 *in the Pennylane dialect* — never FastAPI's 405.

## The reproduced dialect

Five things that will break a consumer if they are not exact — and each one is
different from the four sibling mocks:

| | Pennylane | ...vs the neighbours |
|---|---|---|
| Auth | static `Authorization: Bearer` + **granular scopes** | static JWT (Boond), client_credentials (Entra), RS256 SA (GA), bearer + version header (LinkedIn) |
| Pagination | **opaque cursor** — `{items, has_more, next_cursor}`, plus four **inert** offset keys on four collections (see below) | `page`/`maxResults`, `@odata.nextLink`, `start`/`count`, `limit`/`offset` |
| Amounts | **strings** (`"230.32"`), including `quantity`, `weight`, `debit`/`credit` | numbers everywhere else |
| Nested collections | **links** `{"url": …}` — a second call is required | inline arrays or `included` |
| Rate limit | 25 req / 5 s; **429 body is plain text**, `ratelimit-*` headers on *every* response | 429 with (Boond, GA) or without (LinkedIn) `Retry-After`, always JSON |

Three traps reproduced on purpose, because they are silent in production:

1. **The cursor does not encode filters.** The vendor says it plainly:
   *"Omitting the filters on page 2+ will return unfiltered results from the
   cursor position."* No error, no warning — just extra rows. A pipeline that
   forgets to replay its `filter` loads rows it believes it excluded.
2. **`limit` out of bounds returns 400, it is not silently capped.** A silent
   cap makes a pipeline believe it asked for 5000 rows and got them all, when it
   read 100.
3. **The default sort is `-id`** — descending. A consumer that assumes ascending
   order anchors its checkpoint on the newest row and never sees anything again.

### What the vendor declares and never fills

Three fields are declared by the OpenAPI, served by the API — and `null` on
100 % of the rows of a real tenant (surveyed 2026-09-07):

| Field | Where | Observed |
|---|---|---|
| `analytical_code` | `Category`, and the ventilation copy on entries, entry lines and transactions | `null` on 159 categories out of 159 |
| `product` | customer invoice line | not one of 1 898 invoices fills it |
| `ledger_account` | supplier invoice line | not one of 4 559 |

This mock now serves them **`null` by default** — the key is present, the value
is not. The distinction matters: a consumer that infers its schema from the
data does not materialise a column it has never seen a value for, so the
failure is not *"a null value"* but *"column does not exist"*, weeks later, in
production. That happened. Set `PENNYLANE_MOCK_OPTIONAL_FIELDS=1` to serve the
rich shape — it stays legitimate, another tenant may well fill these three.

### `vat_rate` is a code, not a percentage

A ledger account's `vat_rate` is a **rate code**, and the non-numeric ones are
the majority: `any` (2 633 accounts on the surveyed tenant), `FR_200` (166),
`exempt` (141), `extracom` (51), `crossborder` (40), then `FR_100`, `FR_55`,
`FR_15_385`. Up to 0.2.0 this mock served `"0.0"` and `"20.0"` — a consumer
casting the value to a number passed against the mock and failed against the
vendor on the very first `any`.

The mock does not decode the code into a percentage, and neither should you:
`FR_200` → 20 % is a readable *shape*, not a documented rule.

## The dataset: Boréal Conseil's books

One coherent world, deterministic at seed **42**, anchored at **2026-07-15** —
never `datetime.now()`. Two runs produce the same bytes, which is what makes a
consumer's idempotence gate possible.

Everything derives from the **ledger entries**: an invoice is not an amount
placed next to a plausible entry, the entry *is* the source and the invoice
derives from it. Amounts are handled as **integer cents** and formatted to
strings, so `sum(debit) == sum(credit)` holds exactly — and
`tests/test_coherence.py` asserts it, per entry and in total, before *and after*
the world has evolved.

| | |
|---|---|
| chart of accounts | 19 general + 17 auxiliary (French PCG subset) |
| journals | VE, AC, BQ, OD, AN, SA |
| customers | 10 companies + 2 individuals (the `oneOf` a connector must handle) |
| suppliers | 5 |
| customer invoices | 60, including drafts, one credit note, and one customer with **zero** invoices |
| supplier invoices | 34, across three `accounting_status` values |
| bank transactions | 90, three of them **deliberately unreconciled** |
| ledger entries / lines | 181 / 460 |

### What is shared with the sibling mocks, and what is not

**Shared** (duplicated here, with no package dependency — none of the five mocks
depends on another): the ten client and three supplier company names, the time
anchor, the seed, the 20 % VAT rate, and the `FAC-2026-NNNN` / `AV-2026-NNNN`
reference format. BoondManager's prospect *MediaQuartz* exists here as a
customer with no invoice at all.

**Not shared, and worth stating plainly: the amounts.** Reproducing them to the
cent would mean replaying BoondManager's mission×day-rate matrix here — some
1500 lines of duplicated business logic that would diverge on the first change
to either repo. A downstream test comparing the two mocks compares **sets of
customers and reference series, not euros.**

## Incremental extraction

Two mechanisms, and they work together.

**Changelogs** — the vendor's native mechanism. Ten endpoints returning change
events (id + operation + timestamps, **never** the resource state: a second,
batched call via `filter=[{"field":"id","operator":"in","value":[…]}]` is
required). Four dialect rules, all reproduced:

- chronological **ascending** order;
- **4-week retention** — the mock *purges*, it does not merely refuse: an older
  event is not returned at all, so a consumer cannot mistake the changelog for a
  full-resync channel;
- a `start_date` beyond the window returns **422**, not a truncated list;
- `start_date` and `cursor` together return **400** — pagination continues a
  window, it does not open a new one.

**Time evolution** — the world lives. One scripted event per interval (60 s by
default): an invoice updated, a payment received, a new invoice, a customer
edited, a supplier invoice, an orphan transaction. Event *k* draws its
randomness from `Random(f"{seed}:{k}")` and is stamped `EPOQUE + (k+1) x
interval`, so two mocks advanced by the same number of steps hold the same
world. Every event that creates a flow posts a **balanced** entry.

Set `PENNYLANE_MOCK_EVOLUTION_ENABLED=false` to freeze the dataset — which is
what a consumer's idempotence gate needs.

The mock's reference instant is the dataset anchor, **not the wall clock**: the
retention window is reproducible whatever day the container is started.

## Failure modes

*The point of the mock is to reproduce failure modes, not just happy paths.*
Rules are declarative and driven over HTTP, because the mock runs in a container
at the consumer's side.

```bash
A='X-Mock-Admin-Token: mock-admin-token'
BASE=http://localhost:8014

# 429 with a plain-text body — a client calling .json() on it breaks here,
# not in production.
curl -H "$A" -X POST $BASE/__admin/inject \
  -d '{"kind":"rate_limit","scope":"/api/external/v2/*","after_requests":5,"retry_after_seconds":2}'

# A transient failure that stops on its own — otherwise you are not testing a
# retry, you are testing a failure.
curl -H "$A" -X POST $BASE/__admin/inject \
  -d '{"kind":"status","scope":"/api/external/v2/customers","status":503,"times":1}'

# The most common real-world integration failure: a token regenerated with one
# checkbox missing.
curl -H "$A" -X POST $BASE/__admin/inject \
  -d '{"kind":"scope_reject","scope":"/api/external/v2/transactions","missing_scope":"transactions:readonly"}'
```

Kinds: `rate_limit`, `status`, `latency`, `page_drift`, `auth_reject`,
`scope_reject`, `cursor_reject`. Each takes a glob `scope` and an optional
`times` — that is the difference between a transient failure a retry must
absorb and a persistent one that must fail the run with a non-zero exit code.

## Control plane

Closed by default; when disabled the surface **does not exist** (it is not
"mounted then forbidden").

| Route | Purpose |
|---|---|
| `POST /__admin/reset` | rebuild the dataset (`{"seed": 7}`), re-applying the environment's injection baseline — not an empty one |
| `GET /__admin/state` | seed, totals, `request_counts_by_path`, **`last_query_params_by_path`**, injections, clock offset, evolution log |
| `POST /__admin/inject` · `DELETE /__admin/inject/{id}` · `POST /__admin/inject/clear` | failure rules |
| `POST /__admin/clock` | `{"advance_seconds": 3600}` — time windows **without `sleep`** |
| `POST /__admin/evolve` | `{"steps": 5}` — force N evolution events, clock untouched |
| `POST /__admin/mutate` | edit an entity and push its `updated_at` above every other |
| `POST /__admin/scopes` | redefine the token's scopes — the 403 lever |

`last_query_params_by_path` is the keystone for downstream tests: it is what
lets a consumer **prove** it actually sent its `cursor`, `filter` or
`start_date`. Without that proof, a pipeline that forgot its cursor would pass
every test — it would simply reload page one each time, and no assertion about
content would notice.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `PENNYLANE_MOCK_TOKEN` | `mock-pennylane-token` | the accepted bearer token |
| `PENNYLANE_MOCK_SCOPES` | all 24 read scopes | comma-separated; remove one to get a 403 |
| `PENNYLANE_MOCK_SEED` | `42` | dataset seed — the same seed, the same world |
| `PENNYLANE_MOCK_ADMIN_ENABLED` | `false` | mounts `/__admin` |
| `PENNYLANE_MOCK_ADMIN_TOKEN` | `mock-admin-token` | `X-Mock-Admin-Token` |
| `PENNYLANE_MOCK_EVOLUTION_ENABLED` | `true` | let the world live |
| `PENNYLANE_MOCK_EVOLUTION_INTERVAL` | `60` | seconds between events |
| `PENNYLANE_MOCK_DEFAULT_LIMIT` | `20` | vendor default page size |
| `PENNYLANE_MOCK_MAX_LIMIT` | `100` | list cap; out of bounds → 400 |
| `PENNYLANE_MOCK_MAX_LIMIT_CHANGELOG` | `1000` | changelog cap |
| `PENNYLANE_MOCK_RATE_LIMIT` / `_RATE_WINDOW` | `25` / `5` | advertised in `ratelimit-*` |
| `PENNYLANE_MOCK_CHANGELOG_RETENTION_DAYS` | `28` | the 4-week window |
| `PENNYLANE_MOCK_OPTIONAL_FIELDS` | `false` | serve `analytical_code`, `product` and `ledger_account` — the vendor never fills them |
| `PENNYLANE_MOCK_COMPANY` / `_COMPANY_ID` / `_COMPANY_REG_NO` | `Boréal Conseil` / … | what `/me` reports |
| `PENNYLANE_MOCK_RATE_LIMIT_AFTER` / `_RETRY_AFTER` | unset | a baseline injection rule re-applied on every reset |
| `PENNYLANE_MOCK_HOST` / `_PORT` | `0.0.0.0` / `8000` | uvicorn bind |

There is deliberately **no `.env.example`** here: it lives with the consumer
(insights360), which is where the five sources have to be wired together.

## Development

```bash
make bootstrap   # uv sync
make test        # pytest
make lint        # ruff check + ruff format --check + strict mypy
make format
make contract    # regenerate contracts/pennylane.openapi.yaml — REVIEW the diff
```

The pydantic models are the **source** of the published contract. `make
contract` regenerates `contracts/pennylane.openapi.yaml`, and a test fails if it
drifts — that file is what insights360 copies and pins, so if it lies, it lies
for everyone downstream. `/__admin` and `/health` are stripped from it: they are
mock affordances, and `/__admin` is mounted conditionally, so publishing it
would make the contract depend on the environment that generated it.

Anything not attested by the vendor's OpenAPI is marked
`x-pennylane-confidence` in the contract **and** listed in
[`docs/UNVERIFIED-FIELDS.md`](docs/UNVERIFIED-FIELDS.md), with what it would
take to settle each doubt. A test enforces it: honesty is a build constraint.
The most structural doubt is the **error body** — the vendor's own guide and its
OpenAPI disagree, and this mock follows the OpenAPI.

### Probing a real instance

```bash
PENNYLANE_TOKEN=xxx uv run python scripts/compare_real.py
```

GET-only, writes nothing, and copies **no data** into its report — only field
names and types (which is exactly where the string-amounts trap shows). Any
difference is a difference of the *mock*: the vendor is right.

## The envelope is not uniform, and the OpenAPI does not say so

Four collections out of sixteen — `journals`, `ledger_accounts`,
`ledger_entries` and `fiscal_years` — return **four extra keys** next to the
cursor: `current_page`, `per_page`, `total_items`, `total_pages`. Not
`ledger_entry_lines`, which belongs to the same accounting family as three of
them. There is no rule to infer here, only an observation to reproduce.

**And all four are `null`.** Present, and empty. A consumer that tests for
their *presence* to pick a pagination mode will find them, switch to offset,
and read `null` everywhere — with no error.

Found on 2026-09-04 by `scripts/compare_real.py` against a real instance. The
first attempt at this fix *computed* the four values, which was more useful and
therefore more wrong: a mock that returns a total where the provider returns
`null` validates code that breaks in production. The comparison script caught
it on the very next run.

The cursor stays the safe path: it is the one present on all sixteen
collections, and the one the provider's own guide documents.
