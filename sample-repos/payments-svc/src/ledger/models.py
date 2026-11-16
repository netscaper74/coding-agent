"""Ledger domain model. Amounts are Decimal in major units, quantised per currency."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

MINOR_UNITS = {"AED": 2, "USD": 2, "EUR": 2, "GBP": 2, "JPY": 0, "KWD": 3}


class Side(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass(frozen=True)
class Posting:
    account_id: str
    side: Side
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class JournalEntry:
    reference: str
    idempotency_key: str
    postings: tuple[Posting, ...]
    description: str = ""
    entry_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def totals(self) -> dict[str, tuple[Decimal, Decimal]]:
        """Per currency: (sum of debits, sum of credits)."""
        out: dict[str, tuple[Decimal, Decimal]] = {}
        for p in self.postings:
            debit, credit = out.get(p.currency, (Decimal(0), Decimal(0)))
            if p.side is Side.DEBIT:
                debit += p.amount
            else:
                credit += p.amount
            out[p.currency] = (debit, credit)
        return out


class LedgerError(Exception):
    """Base class for ledger errors."""


class ValidationError(LedgerError):
    pass


class ConflictError(LedgerError):
    """Raised when an idempotency key is replayed with a different payload."""
