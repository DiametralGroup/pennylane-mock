#!/usr/bin/env python3
"""Compares the mock against a REAL Pennylane instance. Read-only, never writes.

The mock is built against the official OpenAPI spec, which is a good source —
but a declarative one. This script checks what the instance ACTUALLY DOES, not
what the documentation says it does. That's where the expensive discrepancies
hide: an amount serialized as a number where the schema promises a string, a
key missing from the real response, a different error dialect.

    PENNYLANE_TOKEN=xxx uv run python scripts/compare_real.py

┌─ WHAT THIS SCRIPT DOES NOT DO ──────────────────────────────────────────────┐
│ No request other than GET. No POST, no PUT, no DELETE. It runs against a    │
│ company's REAL accounting: a write there would be an actual accounting      │
│ entry, and a sandbox is not guaranteed.                                     │
│                                                                              │
│ It never copies any data into its report: no id, no company name, no        │
│ amount. Only FIELD NAMES and TYPES.                                         │
└──────────────────────────────────────────────────────────────────────────────┘

Every discrepancy is a discrepancy in the MOCK. The vendor is right.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

REAL_BASE = "https://app.pennylane.com/api/external/v2"

#: The collections compared. Lists only: a detail endpoint would require a
#: real id, meaning reading data just to ask for it again.
RESOURCES = (
    "journals",
    "ledger_accounts",
    "ledger_entries",
    "ledger_entry_lines",
    "fiscal_years",
    "categories",
    "category_groups",
    "customers",
    "suppliers",
    "products",
    "customer_invoices",
    "supplier_invoices",
    "quotes",
    "bank_accounts",
    "bank_establishments",
    "transactions",
)

CHANGELOGS = ("customer_invoices", "customers", "transactions")


def _get(url: str, token: str) -> tuple[int, dict[str, str], Any]:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, dict(error.headers), json.loads(body)
        except ValueError:
            return error.code, dict(error.headers), body.decode("utf-8", "replace")


def _shape(value: Any) -> Any:
    """The SHAPE of a value, never its content.

    This is what makes the report publishable: it only carries field names
    and types. `"230.32"` becomes `str`, which is exactly the information
    that matters — the v2 dialect's amount trap shows up right here.
    """
    if isinstance(value, dict):
        return {key: _shape(v) for key, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if value is None:
        return "null"
    return type(value).__name__


def _keys(body: Any) -> Any:
    """The keys of an error body — or its type if it isn't JSON.

    A real 429 returns TEXT: the report must be able to say so without raising.
    """
    return sorted(body) if isinstance(body, dict) else type(body).__name__


def _compare(name: str, real: Any, mock: Any) -> list[str]:
    discrepancies: list[str] = []
    if isinstance(real, dict) and isinstance(mock, dict):
        for key in sorted(set(real) - set(mock)):
            discrepancies.append(f"{name}.{key}: present in REAL, missing from mock ({real[key]})")
        for key in sorted(set(mock) - set(real)):
            discrepancies.append(f"{name}.{key}: served by the mock, absent from REAL")
        for key in sorted(set(real) & set(mock)):
            discrepancies += _compare(f"{name}.{key}", real[key], mock[key])
    elif isinstance(real, list) and isinstance(mock, list):
        if real and mock:
            discrepancies += _compare(f"{name}[]", real[0], mock[0])
    elif real != mock:
        discrepancies.append(f"{name}: REAL={real}, mock={mock}")
    return discrepancies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=REAL_BASE)
    parser.add_argument("--out", type=Path, default=Path("docs/comparisons"))
    args = parser.parse_args()

    token = os.environ.get("PENNYLANE_TOKEN")
    if not token:
        print("PENNYLANE_TOKEN is required (read-only company token).", file=sys.stderr)
        return 2

    from fastapi.testclient import TestClient

    import pennylane_mock as mock_module

    mock_client = TestClient(mock_module.app)
    mock_headers = {"Authorization": "Bearer mock-pennylane-token"}

    lines: list[str] = [
        f"# Mock ↔ real instance comparison — {date.today().isoformat()}",
        "",
        "Shapes only: field names and types. No data copied.",
        "",
    ]
    total = 0

    for resource in RESOURCES:
        status, _, real = _get(f"{args.base}/{resource}?limit=1", token)
        if status != 200:
            lines += [f"## {resource}", "", f"⚠️ REAL returned {status} — not compared.", ""]
            continue
        mock_body = mock_client.get(
            f"/api/external/v2/{resource}?limit=1", headers=mock_headers
        ).json()
        discrepancies = _compare(resource, _shape(real), _shape(mock_body))
        total += len(discrepancies)
        lines += [f"## {resource}", ""]
        lines += [f"- {e}" for e in discrepancies] if discrepancies else ["No shape discrepancy."]
        lines.append("")

    for family in CHANGELOGS:
        status, _, real = _get(f"{args.base}/changelogs/{family}?limit=1", token)
        if status != 200:
            lines += [f"## changelogs/{family}", "", f"⚠️ REAL returned {status}.", ""]
            continue
        mock_body = mock_client.get(
            f"/api/external/v2/changelogs/{family}?limit=1", headers=mock_headers
        ).json()
        discrepancies = _compare(f"changelogs/{family}", _shape(real), _shape(mock_body))
        total += len(discrepancies)
        lines += [f"## changelogs/{family}", ""]
        lines += [f"- {e}" for e in discrepancies] if discrepancies else ["No shape discrepancy."]
        lines.append("")

    # The ERROR dialect — the most structurally significant doubt in the
    # registry (docs/UNVERIFIED-FIELDS.md): the guide and the OpenAPI spec
    # contradict each other.
    lines += ["## Error dialect", ""]
    status, _, body = _get(f"{args.base}/customers", "clearly-invalid-token")
    lines.append(f"- real 401: `{status}` → keys `{_keys(body)}`")
    status, headers, body = _get(f"{args.base}/does-not-exist", token)
    lines.append(f"- real 404: `{status}` → keys `{_keys(body)}`")
    lines.append(
        "- rate-limit headers on a healthy response: "
        f"`{sorted(k for k in headers if k.lower().startswith('ratelimit'))}`"
    )
    lines.append("")
    lines.append(f"**Total: {total} shape discrepancy(ies).**")

    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / f"{date.today().isoformat()}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"→ {report} ({total} discrepancy(ies))")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
