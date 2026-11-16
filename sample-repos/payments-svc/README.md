# payments-svc

Double-entry ledger service for ACME Pay. Posts balanced journal entries, exposes balances
and runs a nightly reconciliation job against the card processor's settlement files.

- `src/ledger` — domain model, validation, posting, balances, persistence
- `src/fx` — currency conversion
- `src/api` — HTTP handlers
- `jobs/reconcile` — nightly reconciliation (Java)
- `db/migrations` — PostgreSQL schema
