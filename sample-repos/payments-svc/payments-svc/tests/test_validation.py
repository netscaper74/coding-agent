import pytest
from decimal import Decimal
from payments_svc.src.ledger.validation import validate_entry, ValidationError, MAX_POSTINGS, MAX_AMOUNT
from payments_svc.src.ledger.models import JournalEntry, Posting

def make_posting(amount, currency="USD"):
    return Posting(amount=amount, currency=currency)

def make_entry(postings, idempotency_key="validkey", reference="ref123"):
    return JournalEntry(postings=postings, idempotency_key=idempotency_key, reference=reference)

def test_validate_entry_happy_path():
    postings = [
        make_posting(Decimal("100.00")),
        make_posting(Decimal("-100.00")),
    ]
    entry = make_entry(postings)
    # Should not raise
    validate_entry(entry)

def test_validate_entry_idempotency_key_missing_or_long():
    postings = [
        make_posting(Decimal("50")),
        make_posting(Decimal("-50")),
    ]
    # missing
    entry = make_entry(postings, idempotency_key="")
    with pytest.raises(ValidationError):
        validate_entry(entry)
    # too long
    entry = make_entry(postings, idempotency_key="x"*65)
    with pytest.raises(ValidationError):
        validate_entry(entry)

def test_validate_entry_missing_reference():
    postings = [
        make_posting(Decimal("50")),
        make_posting(Decimal("-50")),
    ]
    entry = make_entry(postings, reference="")
    with pytest.raises(ValidationError):
        validate_entry(entry)

def test_validate_entry_posting_count():
    # too few postings
    entry = make_entry([make_posting(Decimal("50"))])
    with pytest.raises(ValidationError):
        validate_entry(entry)
    # too many postings
    postings = [make_posting(Decimal("10"))]* (MAX_POSTINGS+1)
    entry = make_entry(postings)
    with pytest.raises(ValidationError):
        validate_entry(entry)

def test_validate_entry_unsupported_currency():
    postings = [
        make_posting(Decimal("50"), currency="XYZ"),
        make_posting(Decimal("-50"), currency="XYZ"),
    ]
    entry = make_entry(postings)
    with pytest.raises(ValidationError):
        validate_entry(entry)

def test_validate_entry_amount_not_decimal():
    postings = [
        make_posting(50),  # int, not Decimal
        make_posting(-50),
    ]
    entry = make_entry(postings)
    with pytest.raises(ValidationError):
        validate_entry(entry)

def test_validate_entry_amount_range():
    postings = [
        make_posting(Decimal("0")),  # zero amount
        make_posting(Decimal("0")),
    ]
    entry = make_entry(postings)
    with pytest.raises(ValidationError):
        validate_entry(entry)

    postings = [
        make_posting(MAX_AMOUNT + Decimal("1")),
        make_posting(-(MAX_AMOUNT + Decimal("1"))),
    ]
    entry = make_entry(postings)
    with pytest.raises(ValidationError):
        validate_entry(entry)

def test_validate_entry_amount_precision_exceeded():
    # Assuming USD minor units = 2, test 3 decimal places
    postings = [
        make_posting(Decimal("10.123")),
        make_posting(Decimal("-10.123")),
    ]
    entry = make_entry(postings)
    with pytest.raises(ValidationError):
        validate_entry(entry)

def test_validate_entry_not_balanced():
    postings = [
        make_posting(Decimal("50")),
        make_posting(Decimal("-40")),
    ]
    entry = make_entry(postings)
    with pytest.raises(ValidationError):
        validate_entry(entry)