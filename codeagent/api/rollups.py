"""Hourly rollups of usage events into `events_rollup` via $merge (fast dashboard queries)."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import db as D
from ..settings import get_settings

log = logging.getLogger(__name__)

ROLLUP_WINDOW_HOURS = 48  # recompute the last two days on every run (late events, restarts)


def _flag(expr) -> dict:
    return {"$sum": {"$cond": [expr, 1, 0]}}


def rollup_pipeline(since: datetime) -> list[dict]:
    return [
        {"$match": {"ts": {"$gte": since}, "meta.channel": {"$ne": "eval"}}},
        {"$group": {
            "_id": {
                "hour": {"$dateTrunc": {"date": "$ts", "unit": "hour"}},
                "user": "$meta.user", "team": "$meta.team", "action": "$meta.action",
                "repo": "$meta.repo", "channel": "$meta.channel", "ide": "$meta.ide",
                "synthetic": {"$ifNull": ["$meta.synthetic", False]},
            },
            "name": {"$first": "$meta.name"},
            "requests": {"$sum": 1},
            "errors": _flag({"$eq": ["$status", "error"]}),
            "with_sources": _flag({"$gt": ["$sources", 0]}),
            "no_context": _flag({"$eq": ["$no_context", True]}),
            "cited_answers": _flag({"$gt": ["$cited", 0]}),
            "invalid_citations": {"$sum": {"$ifNull": ["$invalid_citations", 0]}},
            "citations": {"$sum": {"$ifNull": ["$cited", 0]}},
            "filtered_by_acl": {"$sum": {"$ifNull": ["$filtered_by_acl", 0]}},
            "acl_affected": _flag({"$gt": ["$filtered_by_acl", 0]}),
            "openai_in": {"$sum": {"$ifNull": ["$tokens.openai_in", 0]}},
            "openai_out": {"$sum": {"$ifNull": ["$tokens.openai_out", 0]}},
            "voyage_tokens": {"$sum": {"$add": [{"$ifNull": ["$tokens.voyage_embed", 0]},
                                                {"$ifNull": ["$tokens.voyage_rerank", 0]}]}},
            "cost_usd": {"$sum": {"$ifNull": ["$cost_usd", 0]}},
            "cost_openai": {"$sum": {"$ifNull": ["$cost.openai", 0]}},
            "cost_voyage": {"$sum": {"$ifNull": ["$cost.voyage", 0]}},
            "latency_sum": {"$sum": {"$ifNull": ["$latency_ms.total", 0]}},
        }},
        {"$set": {
            "hour": "$_id.hour", "user": "$_id.user", "team": "$_id.team", "action": "$_id.action",
            "repo": "$_id.repo", "channel": "$_id.channel", "ide": "$_id.ide", "synthetic": "$_id.synthetic",
            "updated_at": "$$NOW",
        }},
        {"$merge": {"into": D.ROLLUPS, "on": "_id", "whenMatched": "replace", "whenNotMatched": "insert"}},
    ]


def refresh(database=None, hours: int = ROLLUP_WINDOW_HOURS) -> None:
    database = database if database is not None else D.get_db()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    database[D.EVENTS].aggregate(rollup_pipeline(since))


def start_background(database_factory=D.get_db) -> threading.Thread:
    interval = get_settings().rollup_interval_seconds

    def loop():
        first = True
        while True:
            try:
                # First run after start-up covers the full retention period (e.g. after seeding).
                refresh(database_factory(), hours=24 * get_settings().events_ttl_days if first else ROLLUP_WINDOW_HOURS)
                first = False
            except Exception as exc:
                log.warning("Rollup refresh failed: %s", exc)
            time.sleep(max(interval, 30))

    thread = threading.Thread(target=loop, name="rollups", daemon=True)
    thread.start()
    return thread
