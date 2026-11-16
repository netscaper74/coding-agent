-- Ledger schema. Amounts are NUMERIC (never FLOAT) per SCS-4.1.
CREATE TABLE journal_entries (
    entry_id        UUID PRIMARY KEY,
    reference       TEXT        NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,
    payload_hash    CHAR(64)    NOT NULL,
    description     TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE postings (
    posting_id  BIGSERIAL PRIMARY KEY,
    entry_id    UUID          NOT NULL REFERENCES journal_entries (entry_id),
    account_id  TEXT          NOT NULL,
    side        TEXT          NOT NULL CHECK (side IN ('debit', 'credit')),
    amount      NUMERIC(20,4) NOT NULL CHECK (amount > 0),
    currency    CHAR(3)       NOT NULL
);

CREATE INDEX postings_account_currency ON postings (account_id, currency);
