"""Posting journal entries with idempotency (SCS-4.4) and fail-closed behaviour (SCS-3.2)."""
from __future__ import annotations

import hashlib
import json
import logging

from .models import ConflictError, JournalEntry
from .repository import LedgerRepository
from .validation import validate_entry

log = logging.getLogger(__name__)


def payload_hash(entry: JournalEntry) -> str:
    body = [(p.account_id, p.side.value, str(p.amount), p.currency) for p in entry.postings]
    return hashlib.sha256(json.dumps([entry.reference, body], sort_keys=True).encode()).hexdigest()


class PostingService:
    def __init__(self, repo: LedgerRepository):
        self.repo = repo

    def post_entry(self, entry: JournalEntry) -> str:
        """Validates and posts an entry. Replaying the same idempotency key returns the original id."""
        validate_entry(entry)
        digest = payload_hash(entry)

        existing = self.repo.find_by_idempotency_key(entry.idempotency_key)
        if existing:
            if existing["payload_hash"] != digest:
                raise ConflictError("idempotency key reused with a different payload")
            return existing["entry_id"]

        with self.repo.transaction() as tx:
            entry_id = tx.insert_entry(entry, digest)
            for posting in entry.postings:
                tx.insert_posting(entry_id, posting)
        log.info("ledger.entry_posted", extra={"entry_id": entry_id, "postings": len(entry.postings)})
        return entry_id

    def reverse_entry(self, entry_id: str, idempotency_key: str) -> str:
        """Entries are immutable; corrections are posted as reversing entries (SCS-4.3)."""
        original = self.repo.load_entry(entry_id)
        reversed_entry = original.reversed(idempotency_key)
        return self.post_entry(reversed_entry)
