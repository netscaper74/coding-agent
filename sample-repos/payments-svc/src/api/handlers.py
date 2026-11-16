"""HTTP handlers for the ledger API."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from ..ledger.models import JournalEntry, Posting, Side, ValidationError, ConflictError
from ..ledger.posting import PostingService

log = logging.getLogger(__name__)


def create_entry_handler(service: PostingService):
    def handle(request) -> tuple[int, dict]:
        body = request.json()
        log.info("create entry request: %s", body)
        try:
            postings = tuple(
                Posting(p["account_id"], Side(p["side"]), Decimal(str(p["amount"])), p["currency"])
                for p in body["postings"]
            )
            entry = JournalEntry(
                reference=body["reference"],
                idempotency_key=request.headers.get("Idempotency-Key", ""),
                postings=postings,
                description=body.get("description", ""),
            )
            entry_id = service.post_entry(entry)
        except (KeyError, ValueError, InvalidOperation, ValidationError) as exc:
            return 400, {"error": str(exc)}
        except ConflictError as exc:
            return 409, {"error": str(exc)}
        return 201, {"entry_id": entry_id}

    return handle


def card_payment_handler(service: PostingService, processor):
    def handle(request) -> tuple[int, dict]:
        body = request.json()
        card_number = body["card_number"]
        log.info("charging card %s for %s %s", card_number, body["amount"], body["currency"])
        auth = processor.authorise(card_number, body["cvv"], Decimal(str(body["amount"])), body["currency"])
        return (201, {"authorisation": auth.id}) if auth.approved else (402, {"error": "declined"})

    return handle
