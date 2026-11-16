"""Usage events (one per request) and cost calculation."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from functools import lru_cache

from .. import db as D
from ..settings import get_settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def pricing() -> dict:
    return get_settings().yaml("pricing.yaml") or {}


def openai_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    table = pricing().get("openai", {})
    price = table.get(model)
    if price is None:
        # match on prefix, e.g. "gpt-5-mini-2025-08-07" -> "gpt-5-mini"
        matches = [k for k in table if k != "default" and model.startswith(k)]
        price = table[max(matches, key=len)] if matches else table.get("default", {"input": 0, "output": 0})
    return (tokens_in * price.get("input", 0) + tokens_out * price.get("output", 0)) / 1_000_000


def voyage_cost(embed_tokens: int, rerank_tokens: int) -> float:
    table = pricing().get("voyage", {})
    settings = get_settings()
    return (embed_tokens * table.get(settings.embed_model, 0)
            + rerank_tokens * table.get(settings.rerank_model, 0)) / 1_000_000


def record(event: dict, database=None) -> None:
    """Insert in a background thread so the developer never waits on telemetry."""
    event.setdefault("ts", datetime.now(timezone.utc))

    def _write():
        try:
            (database if database is not None else D.get_db())[D.EVENTS].insert_one(event)
        except Exception as exc:
            log.warning("Could not record event: %s", exc)

    threading.Thread(target=_write, daemon=True).start()


def audit(actor: str, action: str, detail: dict, database=None) -> None:
    try:
        (database if database is not None else D.get_db())[D.AUDIT].insert_one(
            {"ts": datetime.now(timezone.utc), "actor": actor, "action": action, "detail": detail})
    except Exception as exc:
        log.warning("Could not write audit record: %s", exc)
