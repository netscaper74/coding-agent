# ACME Secure Coding Standard (v2026.1)

Placeholder standard for the demo. Replace it with the customer's approved standard
(e.g. their ISO-aligned secure coding policy) once the exact document and version are
confirmed and cleared for indexing.

## SCS-1 General

### SCS-1.1 Scope
This standard applies to all production code in ACME repositories, in every language.
Deviations require a documented exception approved by the platform security team.

### SCS-1.2 Code review
Every change is reviewed by at least one engineer who did not write it. AI-generated
suggestions are treated exactly like human-written code: the author is accountable for
reviewing, testing and understanding them before merge.

## SCS-2 Input validation

### SCS-2.1 Validate external input at the boundary
All input that crosses a trust boundary (HTTP requests, queue messages, files, partner
APIs) MUST be validated at the boundary before use: type, length, range and format.
Validation failures MUST return a client error and MUST NOT reach business logic.

### SCS-2.2 Allow-lists over deny-lists
Validate against an explicit allow-list (permitted currencies, enum values, character
classes) rather than trying to block known-bad values.

## SCS-3 Error handling and logging

### SCS-3.1 No sensitive data in logs
Logs MUST NOT contain card numbers (PAN), CVV, passwords, access tokens, API keys or full
request bodies of payment endpoints. Mask identifiers to the last 4 characters when they
are needed for support. Use structured logging with explicit fields.

### SCS-3.2 Fail closed with typed errors
On unexpected errors, operations MUST fail closed (no partial writes, no default-allow).
Raise domain-specific error types (e.g. `ValidationError`, `ConflictError`) instead of
generic exceptions, and never swallow exceptions silently.

## SCS-4 Money and numeric handling

### SCS-4.1 Never use binary floating point for money
Monetary amounts MUST be represented as integer minor units (cents) or a decimal type
(`Decimal` in Python, `BigDecimal` in Java, integer minor units in TypeScript/Go).
`float`/`double`/JavaScript `number` arithmetic on money is forbidden.

### SCS-4.2 Explicit rounding
Every rounding step MUST use an explicit rounding mode. The default for currency
conversion is half-even (banker's rounding) to the currency's minor-unit precision.

### SCS-4.3 Double-entry invariants
A ledger entry MUST be balanced: the sum of debits equals the sum of credits per currency.
Entries are immutable once posted; corrections are made with reversing entries.

### SCS-4.4 Idempotency
Operations that move money MUST accept an idempotency key and return the original result
when the same key is replayed.

## SCS-5 Data access

### SCS-5.1 Parameterised queries only
SQL MUST use parameterised queries or a query builder. String concatenation or
interpolation (f-strings, template literals, `String.format`) into SQL is forbidden.

### SCS-5.2 Least privilege and scoping
Services connect with the least-privileged database role they need. Every query on
multi-tenant tables MUST filter by tenant/account.

## SCS-6 Testing

### SCS-6.1 Tests for changed behaviour
Every public function that is added or changed MUST have unit tests covering the happy
path, boundary values and at least one failure case.

### SCS-6.2 Test naming and structure
Test names describe behaviour: `test_<unit>_<condition>_<expected>`. Use arrange / act /
assert structure and avoid shared mutable fixtures.

## SCS-7 Secrets

### SCS-7.1 No secrets in source code
Secrets MUST NOT appear in source code, config files committed to Git, or container
images. Load them from the environment or the secret manager at runtime.

## SCS-8 Frontend

### SCS-8.1 Escape output
Never insert untrusted data into the DOM as HTML (`innerHTML`, `dangerouslySetInnerHTML`,
`v-html`). Render text, or sanitise with the approved sanitiser.

### SCS-8.2 Accessible forms
Every form control has an associated label, and validation errors are announced to
assistive technology (`aria-describedby`, `role="alert"`).
