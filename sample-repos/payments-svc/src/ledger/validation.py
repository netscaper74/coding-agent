"""Validation rules applied to every journal entry before it is posted (SCS-2.1, SCS-4.3)."""
from __future__ import annotations

from decimal import Decimal

from .models import MINOR_UNITS, JournalEntry, ValidationError

MAX_POSTINGS = 50
MAX_AMOUNT = Decimal("10000000")


def validate_entry(entry: JournalEntry) -> None:
    """Raises ValidationError if the entry must not be posted.

    Rules:
    - 2..MAX_POSTINGS postings, idempotency key and reference present
    - currency is on the allow-list and amounts use its minor-unit precision
    - every amount is positive and below MAX_AMOUNT
    - debits equal credits per currency (double-entry invariant)
    """
    if not entry.idempotency_key or len(entry.idempotency_key) > 64:
        raise ValidationError("idempotency_key is required (max 64 chars)")
    if not entry.reference:
        raise ValidationError("reference is required")
    if not 2 <= len(entry.postings) <= MAX_POSTINGS:
        raise ValidationError(f"an entry needs between 2 and {MAX_POSTINGS} postings")

    for posting in entry.postings:
        _validate_amount(posting.amount, posting.currency)

    for currency, (debits, credits) in entry.totals().items():
        if debits != credits:
            raise ValidationError(f"entry is not balanced in {currency}: debits {debits} != credits {credits}")


def _validate_amount(amount: Decimal, currency: str) -> None:
    if currency not in MINOR_UNITS:
        raise ValidationError(f"unsupported currency {currency}")
    if not isinstance(amount, Decimal):
        raise ValidationError("amounts must be Decimal")
    if amount <= 0 or amount > MAX_AMOUNT:
        raise ValidationError(f"amount {amount} out of range")
    exponent = -amount.as_tuple().exponent
    if exponent > MINOR_UNITS[currency]:
        raise ValidationError(f"{currency} supports {MINOR_UNITS[currency]} decimal places, got {amount}")
