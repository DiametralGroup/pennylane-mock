---
type: reference
sources_of_truth:
  - "https://pennylane.readme.io/llms.txt (official index, captured 2026-09-02)"
  - "The OpenAPI embedded in each of the 163 pages of https://pennylane.readme.io/reference/*.md,
     merged into a single spec — 123 paths, 158 operations, of which 91 GET (see EXTRACTION.md)"
  - "The guides: Error Handling & Status Codes, Use Cursor-Based Pagination,
     Rate Limiting in API v2, Understand Scopes, Filter API Data,
     Track Data Changes with the API"
review_triggers:
  - "A probe campaign against a REAL Pennylane instance (sandbox account)"
  - "An update to the readme.io reference that adds an enumeration where there wasn't one"
  - "Any discrepancy a consumer observes between the mock and production"
update_policy: >-
  Any field or behavior marked `x-pennylane-confidence: unverified` or
  `invented` in the contract MUST appear in this file —
  `tests/test_contract_is_current.py` fails otherwise. Removing a line here
  requires removing the marker from the schema, and therefore having
  resolved the doubt.
last_verified: 2026-09-02
---

# What is not attested

This mock is built on a **machine-readable, public** source: the OpenAPI
that Pennylane embeds in each of its reference pages. Almost everything it
serves comes from there. This file lists what does not — and what it would
take to resolve each doubt.

The distinction is the same as in the four sibling mocks:

| Marker | Meaning |
|---|---|
| *attested* | comes from the official OpenAPI or a provider guide. No marker. |
| `unverified` | the name, shape, or values are **plausible**, not proven. |
| `invented` | does **not** exist at Pennylane — it's an affordance of the mock. |

## Schema fields

| Field | Resource | What is uncertain | To resolve the doubt |
|---|---|---|---|
| `type` | `journals` | The OpenAPI declares `type: string` **with no enumeration**. The values served (`sale`, `purchase`, `bank`, `miscellaneous`, `new_year`, `payroll`) follow the usual French nomenclature. | `GET /journals` on a real instance: the six codes of a French bookkeeping file should all be there. |
| `type` | `ledger_accounts` | Same thing, `type: string` with no enumeration. Values served: `customer`, `supplier`, `bank`, `tax`, `income`, `expense`, `equity`, `suspense`. | `GET /ledger_accounts?limit=100` on a real instance, then dedupe the field. |
| `job_title`, and the entire item shape | `customers/{id}/contacts` | The `getcustomercontacts` reference **does not detail** the rendered item's schema. The fields served are plausible. | `GET /customers/{id}/contacts` on a real instance that has contacts. |
| numbering of sub-accounts (`411LUMIN`, `401FIVET`) | `ledger_accounts` | The provider documents **no** composition rule: every firm has its own. | Read the `number`s of a real instance whose chart of accounts carries sub-accounts. |
| `reg_no`, `vat_number`, `establishment_no` | `customers`, `suppliers` | Values are **derived from the seed**, so syntactically plausible but not real: they aren't real SIREN numbers. The FIELDS themselves are attested. | Not applicable — this is a property of the fixture dataset, not of the dialect. |
| shape of the sub-account trial balance | `trial_balance` | `formatted_number` is the number padded to eight characters for a general account. For a sub-account (`411LUMIN`), the provider's formatting rule is not documented: the mock renders it as-is. | `GET /trial_balance?is_auxiliary=true` on a real instance. |
| content of peripheral resources | `quotes`, `commercial_documents`, `billing_subscriptions`, `purchase_requests`, `sepa_mandates`, `gocardless_mandates`, `pro_account/*`, `exports/*`, `customer_invoice_templates`, `pa_registrations` | **Deliberately graded fidelity**: these resources are served with the OpenAPI fields, but their model is `GenericElement` (`id` + guaranteed timestamps, everything else passes through as-is) and their dataset is thin. They don't carry the flow the consumer actually exercises. | Type them field by field the day a consumer actually reads them. The SHAPE (envelope, pagination, errors) is already accurate. |

## Behaviors

| Behavior | What is uncertain | To resolve the doubt |
|---|---|---|
| **Error body shape** | Two provider sources **contradict each other**. The "Error Handling & Status Codes" guide (2026-02-06) shows `{"error": "<machine code>", "message": "...", "details": {...}}`. The OpenAPI declares, uniformly across the 91 GET operations, `{"error": "<readable message>", "status": <integer>}`. **The mock follows the OpenAPI**: it is machine-readable, versioned with the endpoints, and it's what the provider publishes as the contract. | Trigger a 403 and a 422 on a real instance and read the exact body. This is the most consequential doubt in this file: a consumer reading `error["message"]` will find nothing with the shape served here. |
| **Cursor content** | The documentation gives **three incompatible encodings**: `eyJpZCI6MTAwfQ==` → `{"id":100}` (pagination guide), `dXBkYXRlZF9hdDoxNjc0MTIzNDU2` → `updated_at:1674123456` (`/bank_accounts` example), `MjAyNS0wMS0wOVQwODoyNDozOC44MTI0NTha` → a timestamp (changelog example). The mock emits base64url of JSON, the pagination guide's shape. | Not applicable, and that's the point: the docs themselves state the cursor is **opaque**. A consumer that decodes it is relying on a detail that has already changed three times. The mock **does not sign** the cursor — that would invent a strictness the provider doesn't have. |
| **`exports:gl` missing from the scopes page** | The "Understand Scopes" page (2026-03-31) only lists `exports:fec` and `exports:agl`. The `exportGeneralLedger` reference explicitly requires `exports:gl`. So the guide page is **incomplete**; the mock follows the reference. | Generate a token in the UI and read the list of offered checkboxes. |
| **Validation of `filter` fields** | The OpenAPI declares, per endpoint, which fields are filterable and with which operators. The mock **accepts any field present in the item**: copying 40 allow-lists that the docs themselves admit move around would break the mock exactly where the provider had added the field. An unknown **operator**, however, does return 400 — the list of nine is short and stable. | Send a `filter` on an undeclared field to a real instance and see whether it returns 400 or ignores it. |
| **Sorting: available fields** | The `-id` default is attested (the OpenAPI declares it as `default`). The list of sortable fields varies by endpoint and is only given in prose. The mock accepts **any field present**. | Same probe as above, on `sort`. |
| **Changelog retention** | "Changes are retained for 4 weeks" is attested; the mock turns this into a **purge** (the older event is not rendered at all) rather than a simple rejection via `start_date`. This is the strictest reading, and the only one that stops a consumer from believing a full changelog resync is possible. | Query a real changelog with no `start_date` on a file several months old and see how far back it goes. |
| **Changelog `delete` operation** | The `insert | update | delete` enumeration is attested. The dataset **produces none of them**: nothing is ever deleted in Boréal Conseil's history. | Not applicable to the dialect. A consumer that needs to handle deletions can inject one via `/__admin` — or the mock will need to script one in `evolution.py`. |
| **`GET /me`: shape of `user`** | The OpenAPI declares `user` as **nullable** without saying when. The mock always renders a user. | Query `/me` with both a company token AND a firm token: the nullability likely comes from there. |
| **`ratelimit-*` headers on a 429** | The guide gives the example `ratelimit-remaining: 0` on a 429. The mock forces it to `0` by definition. On healthy responses, the value served is a simple per-path count — the provider, meanwhile, counts **per token, across all routes**. | Hammer a real instance on two different endpoints and see whether the counter is shared. |

## Deliberate deviations from the provider

These are not doubts: they are decisions, listed here so they aren't
mistaken for bugs.

| Deviation | Why |
|---|---|
| **No writes (POST/PUT/DELETE)** | This mock's consumer — insights360's extraction pipeline — reads and does not write. Write methods return 404 in the Pennylane dialect. Serving them would mean reproducing business validation (journal-entry balancing, VAT consistency, reference uniqueness), a second project in itself. |
| **No webhooks** | Like the four sibling mocks, the pattern is strictly *pull*. The equivalent of "push" is `evolution.py` + `/__admin/clock`. Pennylane, for its part, offers real webhooks. |
| **No OAuth 2.0** | Only the company token (static Bearer) is served. The authorization flow adds nothing for an extraction connector, which uses a long-lived token. |
| **Dataset amounts** | They do NOT reproduce those of `boondmanager-mock` to the euro. See the note in `src/pennylane_mock/dataset/realiste.py`: the company names, the anchor, the seed, and the reference format are shared — the euros are not. |

## Offset pagination keys under `page`/`per_page` parameters

`journals`, `ledger_accounts`, `ledger_entries` and `fiscal_years` return
`current_page`, `per_page`, `total_items` and `total_pages` alongside the
cursor envelope. Observed on 2026-09-04: **all four are `null`** when paginating
by cursor, which is the only mode this mock serves.

What is **not** known: whether the provider fills them when the caller paginates
by `page`/`per_page` query parameters instead. The mock does not accept those
parameters, so the question never arises here — and inventing an answer would
create a third dialect that exists nowhere.

To settle it: call one of those four collections on a real instance with
`?page=2&per_page=10` and record what the four keys carry. Until then the mock
serves `null`, which is what has actually been seen.
