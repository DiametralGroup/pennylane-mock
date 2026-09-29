"""Dataset evolution over time — for incremental extraction.

The world LIVES: a scripted event per interval (60 s by default). This is
what makes the one property that matters for an incremental connector
testable — "a second extraction only reloads what changed" — and lets us
verify that it really sends its `start_date` instead of reloading the whole
universe on every pass.

┌─ DETERMINISM ────────────────────────────────────────────────────────────────┐
│ Event k draws its randomness from `Random(f"{seed}:{k}")` and its           │
│ timestamp is ALWAYS `EPOCH + (k+1) x interval`. Two runs of the same mock,  │
│ advanced by the same number of events, produce the same world — without    │
│ which an incrementality test wouldn't be replayable.                       │
│                                                                              │
│ `advance()` is idempotent and lock-protected: FastAPI handlers run in a     │
│ thread pool, and two simultaneous requests would otherwise advance the      │
│ timeline twice for the same step.                                          │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ THE ACCOUNTING INVARIANT ALSO HOLDS DURING EVOLUTION ──────────────────────┐
│ Every event that creates a flow posts a BALANCED entry. The trial balance   │
│ of a mock left running for an hour must always balance — otherwise the     │
│ mock ends up serving false accounting data, which is worse than serving     │
│ none at all.                                                                │
└──────────────────────────────────────────────────────────────────────────────┘

To freeze the world — insights360's idempotence gate compares `raw` between
two runs and can't live with a moving dataset — set
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

#: The origin of the timeline. Later than the base dataset's `DERNIERE_MAJ`
#: (2026-07-12): a cursor set on the base dataset returns zero rows, and the
#: FIRST evolution event is the first change it will see.
EPOQUE = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)

#: The event cycle. Six steps, then it starts over — but the affected
#: entities keep advancing: the seventh event isn't the first one again.
CYCLE: tuple[str, ...] = (
    "customer_invoice_update",
    "customer_payment",
    "new_customer_invoice",
    "customer_update",
    "supplier_invoice",
    "orphan_transaction",
)


def _timestamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond:06d}Z"


class Evolution:
    """The timeline. `advance()` moves it forward to the given instant."""

    def __init__(self, seed: int, start: float) -> None:
        self.seed = seed
        self.start = start
        self.rank = 0
        self.log: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ── Progression ──────────────────────────────────────────────────────────

    def steps_due(self, now: float) -> int:
        if not settings.evolution_enabled or settings.evolution_interval <= 0:
            return 0
        elapsed = max(0.0, now - self.start)
        return int(elapsed // settings.evolution_interval)

    def advance(self, data: dict[str, Any], now: float) -> bool:
        """Applies the events that are due. Returns True if the world moved."""
        with self._lock:
            target = self.steps_due(now)
            if target <= self.rank:
                return False
            for k in range(self.rank, target):
                self._apply(data, k)
            self.rank = target
            return True

    def force(self, data: dict[str, Any], steps: int = 1) -> None:
        """Advances by `steps` events, regardless of the clock — the lever
        behind `/__admin/evolve`, for a test that doesn't want to manipulate
        time."""
        with self._lock:
            for k in range(self.rank, self.rank + steps):
                self._apply(data, k)
            self.rank += steps

    # ── The events ───────────────────────────────────────────────────────────

    def _apply(self, data: dict[str, Any], k: int) -> None:
        kind = CYCLE[k % len(CYCLE)]
        rng = random.Random(f"{self.seed}:{k}")
        when = EPOQUE + timedelta(seconds=settings.evolution_interval * (k + 1))
        timestamp = _timestamp(when)
        day = when.date()

        apply_event = getattr(self, f"_evt_{kind}")
        detail = apply_event(data, rng, timestamp, day)
        self.log.append({"rank": k, "kind": kind, "at": timestamp, "detail": detail})

    # -- A plain `updated_at` bump: the most frequent case in reality --------

    def _evt_customer_invoice_update(
        self, data: dict[str, Any], rng: random.Random, timestamp: str, day: date
    ) -> str:
        del day
        candidates = [f for f in data["customer_invoices"] if not f["draft"]]
        invoice = candidates[rng.randrange(len(candidates))]
        invoice["updated_at"] = timestamp
        invoice["pdf_description"] = "Mention de relance ajoutée."
        _change(data, "customer_invoices", invoice, "update", timestamp)
        return f"customer_invoice:{invoice['id']}"

    # -- A payment: the invoice becomes `paid`, a transaction is born --------

    def _evt_customer_payment(
        self, data: dict[str, Any], rng: random.Random, timestamp: str, day: date
    ) -> str:
        unpaid = [
            f
            for f in data["customer_invoices"]
            if not f["paid"] and not f["draft"] and f["status"] != "credit_note"
        ]
        if not unpaid:
            return "no unpaid invoice"
        invoice = unpaid[rng.randrange(len(unpaid))]
        customer = next(c for c in data["customers"] if c["id"] == invoice["customer"]["id"])
        total = round(float(invoice["amount"]) * 100)
        ledger = _current_ledger(data)
        mark = len(ledger.lignes)
        counterparty_account = _customer_account(customer)
        label = f"VIR SEPA {customer['name'].upper()} {invoice['invoice_number']}"
        entry = ledger.passer(
            journal="BQ",
            jour=day,
            libelle=label,
            numero_piece=f"BQ-EVO-{next_ledger_entry(data):04d}",
            mouvements=[("512000", total, label), (counterparty_account, -total, "Règlement")],
            maj=timestamp,
        )
        _absorb(data, ledger, timestamp, mark)
        if invoice["ledger_entry"]:
            ledger.lettrer(invoice["ledger_entry"]["id"], entry["id"], "")

        invoice["paid"] = True
        invoice["status"] = "paid"
        invoice["remaining_amount_with_tax"] = "0.00"
        invoice["remaining_amount_without_tax"] = "0.00"
        invoice["updated_at"] = timestamp
        _change(data, "customer_invoices", invoice, "update", timestamp)

        transaction = _transaction(
            _next_id(data["transactions"]),
            jour=day,
            libelle=label,
            montant=total,
            compte=data["bank_accounts"][0],
            journal=next(j for j in data["journals"] if j["code"] == "BQ"),
            tiers_client=_ref(customer["id"], "/customers"),
            tiers_fournisseur=None,
            reste=0,
            categories=[],
            rng=rng,
        )
        transaction["created_at"] = transaction["updated_at"] = timestamp
        data["transactions"].append(transaction)
        data["matched_transactions_par_facture_client"].setdefault(invoice["id"], []).append(
            transaction["id"]
        )
        _change(data, "transactions", transaction, "insert", timestamp)
        _rebalance_cash(data)
        return f"customer_invoice:{invoice['id']} → paid"

    # -- A new invoice: the case a cursor MUST report -------------------------

    def _evt_new_customer_invoice(
        self, data: dict[str, Any], rng: random.Random, timestamp: str, day: date
    ) -> str:
        customers = [
            c for c in data["customers"] if c["customer_type"] == "company" and c["notes"] is None
        ]
        customer = customers[rng.randrange(len(customers))]
        product = data["products"][rng.randrange(len(data["products"]))]
        quantity = rng.randint(3, 12)
        unit_price = round(float(product["price_before_tax"]) * 100)
        before_tax = unit_price * quantity
        tax = before_tax * TVA // 100
        total = before_tax + tax
        ident = _next_id(data["customer_invoices"])
        number = f"FAC-2026-{ident:04d}"

        ledger = _current_ledger(data)
        mark = len(ledger.lignes)
        entry = ledger.passer(
            journal="VE",
            jour=day,
            libelle=f"Facture {number} — {customer['name']}",
            numero_piece=number,
            numero_facture=number,
            echeance=day + timedelta(days=30),
            categories=_categorie(data["categories"], "Paris"),
            mouvements=[
                (_customer_account(customer), total, f"{customer['name']} — {number}"),
                ("706000", -before_tax, "Prestations"),
                ("445710", -tax, f"TVA collectée {TVA}%"),
            ],
            maj=timestamp,
        )
        _absorb(data, ledger, timestamp, mark)

        template = data["customer_invoices"][0]
        invoice = {
            **template,
            "id": ident,
            "label": f"Prestations complémentaires — {product['label']}",
            "invoice_number": number,
            "amount": _euros(total),
            "currency_amount": _euros(total),
            "currency_amount_before_tax": _euros(before_tax),
            "currency_tax": _euros(tax),
            "tax": _euros(tax),
            "date": _d(day),
            "deadline": _d(day + timedelta(days=30)),
            "paid": False,
            "status": "upcoming",
            "draft": False,
            "ledger_entry": {"id": entry["id"]},
            "remaining_amount_with_tax": _euros(total),
            "remaining_amount_without_tax": _euros(before_tax),
            "customer": _ref(customer["id"], "/customers"),
            "credited_invoice": None,
            "filename": f"{number}.pdf",
            "public_file_url": f"https://files.example/{number}.pdf",
            "external_reference": f"evolution:invoice:{ident}",
            "created_at": timestamp,
            "updated_at": timestamp,
            **{
                key: _lien(f"/customer_invoices/{ident}/{key}")
                for key in (
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
        data["customer_invoices"].append(invoice)
        data["customer_invoice_lines"][ident] = [
            {
                "id": ident * 100 + 1,
                "label": product["label"],
                "unit": "jour",
                "quantity": str(quantity),
                "amount": _euros(total),
                "currency_amount": _euros(total),
                "description": product["description"],
                # Same scrubbing as at dataset build time: what the mock
                # SERVES follows the provider, which never fills in this
                # field. Cf. `Settings.optional_fields_served`.
                "product": (
                    _ref(product["id"], "/products") if settings.optional_fields_served else None
                ),
                "vat_rate": product["vat_rate"],
                "currency_amount_before_tax": _euros(before_tax),
                "currency_tax": _euros(tax),
                "tax": _euros(tax),
                "raw_currency_unit_price": _euros(unit_price),
                "discount": {"type": "relative", "value": "0"},
                "section_rank": 1,
                "imputation_dates": {"start_date": _d(day), "end_date": _d(day)},
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        ]
        _change(data, "customer_invoices", invoice, "insert", timestamp)
        return f"customer_invoice:{ident} created"

    def _evt_customer_update(
        self, data: dict[str, Any], rng: random.Random, timestamp: str, day: date
    ) -> str:
        del day
        customer = data["customers"][rng.randrange(len(data["customers"]))]
        customer["phone"] = f"+331{rng.randint(10_000_000, 99_999_999)}"
        customer["updated_at"] = timestamp
        _change(data, "customers", customer, "update", timestamp)
        return f"customer:{customer['id']}"

    def _evt_supplier_invoice(
        self, data: dict[str, Any], rng: random.Random, timestamp: str, day: date
    ) -> str:
        supplier = data["suppliers"][rng.randrange(len(data["suppliers"]))]
        expense_account = dict(
            (name, account) for name, _, _, _, account, _ in _SUPPLIER_EXPENSE_ACCOUNTS
        )[supplier["name"]]
        before_tax = rng.randrange(20_000, 400_000, 1_000)
        tax = before_tax * TVA // 100
        total = before_tax + tax
        ident = _next_id(data["supplier_invoices"])
        number = f"{supplier['name'][:3].upper()}-2026-EVO{ident:03d}"

        ledger = _current_ledger(data)
        mark = len(ledger.lignes)
        entry = ledger.passer(
            journal="AC",
            jour=day,
            libelle=f"{supplier['name']} — facture {number}",
            numero_piece=number,
            numero_facture=number,
            echeance=day + timedelta(days=30),
            statut="validation_needed",
            mouvements=[
                (expense_account, before_tax, "Achat"),
                ("445660", tax, f"TVA déductible {TVA}%"),
                (_aux("401", supplier["name"]), -total, f"{supplier['name']} — {number}"),
            ],
            maj=timestamp,
        )
        _absorb(data, ledger, timestamp, mark)

        template = data["supplier_invoices"][0]
        invoice = {
            **template,
            "id": ident,
            "label": "Achat complémentaire",
            "invoice_number": number,
            "amount": _euros(total),
            "currency_amount": _euros(total),
            "currency_amount_before_tax": _euros(before_tax),
            "currency_tax": _euros(tax),
            "tax": _euros(tax),
            "date": _d(day),
            "deadline": _d(day + timedelta(days=30)),
            "reconciled": False,
            "accounting_status": "validation_needed",
            "payment_status": "to_be_processed",
            "paid": False,
            "remaining_amount_with_tax": _euros(total),
            "remaining_amount_without_tax": _euros(before_tax),
            "ledger_entry": {"id": entry["id"]},
            "supplier": _ref(supplier["id"], "/suppliers"),
            "filename": f"{number}.pdf",
            "public_file_url": f"https://files.example/{number}.pdf",
            "external_reference": f"evolution:purchase:{ident}",
            "invoice_lines": _lien(f"/supplier_invoices/{ident}/invoice_lines"),
            "categories": _lien(f"/supplier_invoices/{ident}/categories"),
            "payments": _lien(f"/supplier_invoices/{ident}/payments"),
            "matched_transactions": _lien(f"/supplier_invoices/{ident}/matched_transactions"),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        data["supplier_invoices"].append(invoice)
        data["supplier_invoice_lines"][ident] = [
            {
                "id": ident * 100 + 1,
                "label": "Achat complémentaire",
                "quantity": "1",
                "unit": "forfait",
                "amount": _euros(total),
                "currency_amount": _euros(total),
                "currency_amount_before_tax": _euros(before_tax),
                "currency_tax": _euros(tax),
                "vat_rate": "FR_200",
                "raw_currency_unit_price": _euros(before_tax),
                "description": "Ligne unique.",
                "ledger_account": (
                    {"id": ledger.compte(expense_account)["id"]}
                    if settings.optional_fields_served
                    else None
                ),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        ]
        _change(data, "supplier_invoices", invoice, "insert", timestamp)
        return f"supplier_invoice:{ident} created"

    def _evt_orphan_transaction(
        self, data: dict[str, Any], rng: random.Random, timestamp: str, day: date
    ) -> str:
        """A payment WITHOUT an invoice — the real-life work of a firm.

        It would unbalance the books if left unposted: the counterpart
        therefore goes to suspense account 471, exactly what any accountant
        does facing an unidentified movement.
        """
        amount = rng.randrange(15_000, 90_000, 500)
        ledger = _current_ledger(data)
        mark = len(ledger.lignes)
        ledger.passer(
            journal="BQ",
            jour=day,
            libelle="Encaissement non identifié",
            numero_piece=f"BQ-ATT-{_next_id(data['transactions']):04d}",
            statut="waiting_details",
            mouvements=[
                ("512000", amount, "Encaissement non identifié"),
                ("471000", -amount, "Compte d'attente"),
            ],
            maj=timestamp,
        )
        _absorb(data, ledger, timestamp, mark)
        transaction = _transaction(
            _next_id(data["transactions"]),
            jour=day,
            libelle="VIR RECU TIERS NON IDENTIFIE",
            montant=amount,
            compte=data["bank_accounts"][0],
            journal=next(j for j in data["journals"] if j["code"] == "BQ"),
            tiers_client=None,
            tiers_fournisseur=None,
            reste=amount,
            categories=[],
            rng=rng,
        )
        transaction["created_at"] = transaction["updated_at"] = timestamp
        data["transactions"].append(transaction)
        _change(data, "transactions", transaction, "insert", timestamp)
        _rebalance_cash(data)
        return f"transaction:{transaction['id']} orphaned"


# ── Utilities shared by the events ────────────────────────────────────────────

#: Each supplier's expense account — copied from the dataset's catalog to
#: avoid a circular import at module load time.
_SUPPLIER_EXPENSE_ACCOUNTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("Fivetech Partners", "", "", "", "604000", ""),
    ("Softalliance", "", "", "", "651600", ""),
    ("Foncière Beaumont", "", "", "", "613200", ""),
    ("Nordnet Télécom", "", "", "", "626000", ""),
    ("Bureau & Cie", "", "", "", "606300", ""),
)


def _next_id(elements: list[dict[str, Any]]) -> int:
    return max((e["id"] for e in elements), default=0) + 1


def next_ledger_entry(data: dict[str, Any]) -> int:
    return _next_id(data["ledger_entries"])


def _customer_account(customer: dict[str, Any]) -> str:
    if customer["customer_type"] == "company":
        return _aux("411", customer["name"])
    return _aux("411", f"{customer['last_name']}{customer['first_name']}")


def _current_ledger(data: dict[str, Any]) -> Grand:
    """An accumulator repositioned onto the ledger's current state.

    It carries the SAME lists as the dataset (not copies): what it posts
    lands directly in `ledger_entries` / `ledger_entry_lines`, so there's no
    window where the two would diverge.
    """
    ledger = Grand(data["journals"], data["ledger_accounts"])
    ledger.ecritures = data["ledger_entries"]
    ledger.lignes = data["ledger_entry_lines"]
    ledger._id_ecriture = _next_id(data["ledger_entries"])
    ledger._id_ligne = _next_id(data["ledger_entry_lines"])
    return ledger


def _absorb(data: dict[str, Any], ledger: Grand, timestamp: str, mark: int) -> None:
    """Logs to the changelog the entry lines created since `mark`.

    The mark is a POSITION in the list, recorded before calling `passer()`.
    It's the only reliable way to designate "what was just added": filtering
    on the timestamp would miss two entries posted within the same second,
    which happens as soon as a test forces several steps at once.
    """
    for line in ledger.lignes[mark:]:
        line["created_at"] = line["updated_at"] = timestamp
        _change(data, "ledger_entry_lines", line, "insert", timestamp)


def _change(
    data: dict[str, Any],
    family: str,
    element: dict[str, Any],
    operation: str,
    timestamp: str,
) -> None:
    data["changelogs"].setdefault(family, []).append(
        {
            "id": element["id"],
            "operation": operation,
            "processed_at": timestamp,
            "created_at": element["created_at"],
            "updated_at": element["updated_at"],
        }
    )


def _rebalance_cash(data: dict[str, Any]) -> None:
    from .dataset.realiste import _recaler_soldes_bancaires

    _recaler_soldes_bancaires(
        data["bank_accounts"], data["ledger_entry_lines"], data["ledger_accounts"]
    )
