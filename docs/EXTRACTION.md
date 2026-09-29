# Where every shape comes from

This mock was not written from memory. It is anchored to a
**machine-readable, public** source, captured on **2026-09-02**, and this file
says which one, how to replay it, and what it contains.

## The source: the OpenAPI embedded in the reference

Pennylane publishes an index for agents at
[`https://pennylane.readme.io/llms.txt`](https://pennylane.readme.io/llms.txt),
and **every documentation page is available in Markdown** by appending
`.md` to it. The decisive point: every page in the "API Reference" section
embeds, under a `# OpenAPI definition` heading, the **complete OpenAPI
fragment for its endpoint** — parameters, response schemas, enumerations,
examples.

So there was no guessing involved: it was enough to download the 163
reference pages and merge their fragments.

```bash
# 1. The index
curl -sS https://pennylane.readme.io/llms.txt -o llms.txt

# 2. The 163 reference pages, in markdown
grep -o 'https://pennylane.readme.io/reference/[a-z0-9-]*\.md' llms.txt \
  | sort -u \
  | xargs -P 8 -I{} sh -c 'curl -sS "{}" -o "ref/$(basename {})"'

# 3. The guides that carry the cross-cutting dialect
for p in error-handling-status-codes using-cursor-based-pagination \
         rate-limiting-1 v2-scopes setting-up-filters \
         tracking-data-changes-with-pennylane-api api-v2-vs-v1; do
  curl -sS "https://pennylane.readme.io/docs/$p.md" -o "docs/$p.md"
done
```

Merging the fragments (extracting the ```json block that follows
`# OpenAPI definition`, then taking the union of `paths` and `components`)
gives:

| | |
|---|---|
| paths | **123** |
| operations | **158** |
| of which **GET** | **91** ← the surface this mock serves |
| pages without a fragment | 5, all `PUT .../categories` (writes, out of scope) |

The mock serves **the 91**. `tests/test_endpoints.py` turns this into an
assertion: if the route factory drops one, the test says so before a
consumer finds out.

## What the survey settled

| Question | Answer found | Where |
|---|---|---|
| Base URL | `https://app.pennylane.com/api/external/v2` | `servers` of every fragment |
| Authentication | `Authorization: Bearer <TOKEN>`. No refresh, no expiry on the company-side token. | "Create a Company API Token" guide |
| Authorization | `resource:readonly` / `resource:all` scopes, granular per domain. 403 on a missing scope, **with the scope name in the message**. | "Understand Scopes" guide + `security` of every operation |
| List envelope | `{"items": [...], "has_more": bool, "next_cursor": str|null}`, `additionalProperties: false` | Pagination guide + 200 response schema |
| Pagination | Opaque cursor. `limit`: default **20**, cap **100** on lists, **1000** on changelogs. Out of bounds → **400**, no clamping. | `parameters` of every operation |
| Cursor pitfall | It **does not encode filters**: "Omitting the filters on page 2+ will return unfiltered results from the cursor position." | Pagination guide |
| Sorting | `sort=field` / `sort=-field`. Declared default: **`-id`** (descending). | `parameters`, `default` field |
| Filtering | `filter` = JSON array of `{field, operator, value}`, combined with AND. Nine operators: `eq`, `not_eq`, `lt`, `lteq`, `gt`, `gteq`, `in`, `not_in`, `start_with`. | "Filter API Data" guide |
| Amounts | **Strings** (`"230.32"`), everywhere, including `quantity`, `weight`, `vat_rate`, `debit`/`credit`. | `type: string` on every monetary field |
| Nested collections | **Links** `{"url": "…"}`, never arrays — the most structuring v1 → v2 difference. | "Migrate from API v1 to v2" guide + schemas |
| Error body | `{"error": "<message>", "status": <integer>}`, uniformly across the 91 GETs. ⚠️ **The error guide describes a different one** — see `UNVERIFIED-FIELDS.md`. | `responses` 400/401/403/404/422 |
| Rate limit | **25 requests / 5 s per token**. 429 with a **plain-text** body, `retry-after` header. The `ratelimit-limit`/`-remaining`/`-reset` headers are served on **all** responses. | "Rate Limiting in API v2" guide |
| Incrementality | Ten `/changelogs/*` endpoints. **Ascending chronological** order, **4-week** retention (beyond that: 422), `start_date` and `cursor` are **mutually exclusive** (otherwise 400). | "Track Data Changes" guide + references |
| Balance | `period_start` and `period_end` are **mandatory**. `is_auxiliary` aggregates or breaks out third-party accounts. | `parameters` of `getTrialBalance` |

## What the mock does not serve, and why

- **Writes (POST/PUT/DELETE).** The consumer reads. Serving them would mean
  reproducing Pennylane's business validation — journal-entry balancing, VAT
  consistency, reference uniqueness — a second project in itself. A write
  method returns **404 in the Pennylane dialect**, never FastAPI's 405.
- **Webhooks.** Like the four sibling mocks, the pattern is strictly *pull*.
  The equivalent of "push" is `evolution.py` + `/__admin/clock`.
- **OAuth 2.0.** An extraction connector uses a long-lived company token; the
  authorization flow adds nothing to what needs to be exercised.

## Replaying the survey

`scripts/compare_real.py` checks the committed contract against a **real**
instance, read-only, on the list endpoints. It requires a token and writes
nothing:

```bash
PENNYLANE_TOKEN=xxx uv run python scripts/compare_real.py --out docs/comparisons/
```

It compares, resource by resource: the envelope keys, the item keys, the
type of each value (string vs number — that is where the amounts pitfall
lives), and the shape of 401/403/404 errors. Any discrepancy is a
discrepancy in the MOCK, never in the provider: the provider is always
right.
