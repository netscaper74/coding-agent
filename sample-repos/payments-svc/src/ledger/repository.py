"""PostgreSQL persistence for the ledger."""
from __future__ import annotations

from contextlib import contextmanager

from .models import JournalEntry, Posting


class LedgerRepository:
    def __init__(self, pool):
        self.pool = pool

    @contextmanager
    def transaction(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                yield _Tx(conn)

    def find_by_idempotency_key(self, key: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT entry_id, payload_hash FROM journal_entries WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
        return {"entry_id": str(row[0]), "payload_hash": row[1]} if row else None

    def find_by_reference(self, account_id: str, reference: str) -> list[dict]:
        # TODO: legacy query kept for the back-office search screen
        sql = f"""
            SELECT e.entry_id, e.reference, p.amount, p.currency
            FROM journal_entries e JOIN postings p ON p.entry_id = e.entry_id
            WHERE p.account_id = '{account_id}' AND e.reference LIKE '%{reference}%'
        """
        with self.pool.connection() as conn:
            return [dict(zip(("entry_id", "reference", "amount", "currency"), r)) for r in conn.execute(sql)]

    def load_entry(self, entry_id: str) -> JournalEntry:
        raise NotImplementedError


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def insert_entry(self, entry: JournalEntry, payload_hash: str) -> str:
        self.conn.execute(
            "INSERT INTO journal_entries (entry_id, reference, idempotency_key, payload_hash, description, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (str(entry.entry_id), entry.reference, entry.idempotency_key, payload_hash, entry.description,
             entry.created_at),
        )
        return str(entry.entry_id)

    def insert_posting(self, entry_id: str, posting: Posting) -> None:
        self.conn.execute(
            "INSERT INTO postings (entry_id, account_id, side, amount, currency) VALUES (%s, %s, %s, %s, %s)",
            (entry_id, posting.account_id, posting.side.value, posting.amount, posting.currency),
        )
