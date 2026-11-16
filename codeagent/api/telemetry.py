"""Developer outcomes: Continue dev-data events and explicit feedback.

Continue applies diffs locally, so the agent never sees whether a suggestion was
accepted. Continue's `data` config block can POST dev-data events (editOutcome,
chatFeedback, ...) to an HTTP endpoint; we accept them here. The parser is tolerant:
it looks for an event name and `accepted` / `feedback` flags wherever they appear,
and never stores code (configure the destination with `level: noCode`).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import db as D
from ..users import User

NAME_KEYS = ("name", "eventName", "event", "type")
DROP_KEYS = {"prefix", "suffix", "completion", "prompt", "code", "diff", "fileContents", "newCode", "previousCode",
             "codeToEdit", "userInput", "messages", "content", "selection", "filepath", "fileUri"}
ACCEPT_EVENTS = {"editOutcome", "autocomplete", "nextEditOutcome", "quickEdit", "applyOutcome"}


def _flags(data: dict) -> dict:
    out = {}
    for key in ("accepted", "feedback", "accept"):
        if isinstance(data.get(key), bool):
            out["accepted" if key in ("accepted", "accept") else "feedback"] = data[key]
    outcome = data.get("outcome") or data.get("status")
    if isinstance(outcome, str) and outcome.lower() in {"accepted", "accept", "rejected", "reject"}:
        out["accepted"] = outcome.lower().startswith("accept")
    return out


def _clean(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in DROP_KEYS and not isinstance(v, (dict, list))}


def parse(body) -> list[dict]:
    items = body if isinstance(body, list) else [body]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = next((item[k] for k in NAME_KEYS if isinstance(item.get(k), str)), None)
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        if name is None:
            name = next((data[k] for k in NAME_KEYS if isinstance(data.get(k), str)), "unknown")
        flags = _flags(data) or _flags(item)
        out.append({"event": name, **flags, "attrs": _clean(data), "model": data.get("modelTitle") or data.get("model")})
    return out


def store_continue_events(body, user: User, ide: str, database=None) -> int:
    rows = parse(body)
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    docs = [{"ts": now, "user": user.user, "team": user.team, "ide": ide, "source": "continue", **r} for r in rows]
    (database if database is not None else D.get_db())[D.OUTCOMES].insert_many(docs)
    return len(docs)


def store_feedback(request_id: str, helpful: bool | None, accepted: bool | None, user: User, ide: str,
                   action: str | None = None, database=None) -> None:
    doc = {"ts": datetime.now(timezone.utc), "user": user.user, "team": user.team, "ide": ide,
           "source": "agent", "event": "feedback", "request_id": request_id, "action": action}
    if helpful is not None:
        doc["feedback"] = bool(helpful)
    if accepted is not None:
        doc["accepted"] = bool(accepted)
    (database if database is not None else D.get_db())[D.OUTCOMES].insert_one(doc)
