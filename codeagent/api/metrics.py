"""Aggregation pipelines behind the monitoring dashboard."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .. import db as D
from ..settings import get_settings
from ..users import all_users
from . import events

_TZ = re.compile(r"^[A-Za-z0-9_+\-/:]{1,40}$")


class Params:
    def __init__(self, days: int = 14, tz: str = "UTC", team: str | None = None, synthetic: bool = True):
        self.days = max(1, min(int(days), 90))
        self.tz = tz if tz and _TZ.match(tz) else "UTC"
        self.team = team or None
        self.synthetic = synthetic
        self.end = datetime.now(timezone.utc)
        self.start = self.end - timedelta(days=self.days)
        self.prev_start = self.start - timedelta(days=self.days)

    @classmethod
    def from_query(cls, q) -> "Params":
        try:
            days = int(q.get("days", 14))
        except ValueError:
            days = 14
        return cls(days=days, tz=q.get("tz", "UTC"), team=q.get("team") or None,
                   synthetic=q.get("synthetic", "true").lower() != "false")

    def rollup_match(self, start=None, end=None, **extra) -> dict:
        m = {"hour": {"$gte": start or self.start, "$lt": end or self.end}}
        if self.team:
            m["team"] = self.team
        if not self.synthetic:
            m["synthetic"] = False
        m.update(extra)
        return m

    def event_match(self, start=None, **extra) -> dict:
        m = {"ts": {"$gte": start or self.start, "$lt": self.end}, "meta.channel": {"$ne": "eval"}}
        if self.team:
            m["meta.team"] = self.team
        if not self.synthetic:
            m["meta.synthetic"] = {"$ne": True}
        m.update(extra)
        return m

    def outcome_match(self, start=None) -> dict:
        m = {"ts": {"$gte": start or self.start, "$lt": self.end}}
        if self.team:
            m["team"] = self.team
        if not self.synthetic:
            m["synthetic"] = {"$ne": True}
        return m

    def day(self, field: str) -> dict:
        return {"$dateToString": {"date": field, "format": "%Y-%m-%d", "timezone": self.tz}}

    def day_labels(self) -> list[str]:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(self.tz)
        except Exception:
            zone = timezone.utc
        first = self.start.astimezone(zone).date()
        last = self.end.astimezone(zone).date()
        out, d = [], first
        while d <= last:
            out.append(d.isoformat())
            d += timedelta(days=1)
        return out


SUMS = ["requests", "errors", "with_sources", "no_context", "cited_answers", "invalid_citations", "citations",
        "filtered_by_acl", "acl_affected", "openai_in", "openai_out", "voyage_tokens", "cost_usd", "cost_openai",
        "cost_voyage", "latency_sum"]


def _sum_fields() -> dict:
    return {f: {"$sum": f"${f}"} for f in SUMS}


def _ratio(a, b) -> float | None:
    return round(a / b, 4) if b else None


def _clean(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, float):
            out[k] = round(v, 6)
        else:
            out[k] = v
    return out


def _totals(db, p: Params, start, end) -> dict:
    rows = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match(start, end)},
        {"$group": {"_id": None, "users": {"$addToSet": "$user"}, **_sum_fields()}},
    ]))
    if not rows:
        return {f: 0 for f in SUMS} | {"active_users": 0}
    r = rows[0]
    r["active_users"] = len(r.pop("users"))
    r.pop("_id", None)
    return r


def _acceptance(db, p: Params, start=None) -> dict:
    rows = list(db[D.OUTCOMES].aggregate([
        {"$match": {**p.outcome_match(start), "accepted": {"$in": [True, False]}}},
        {"$group": {"_id": "$accepted", "n": {"$sum": 1}}},
    ]))
    counts = {r["_id"]: r["n"] for r in rows}
    acc, rej = counts.get(True, 0), counts.get(False, 0)
    return {"accepted": acc, "rejected": rej, "rate": _ratio(acc, acc + rej)}


def _percentiles(db, p: Params, field: str, start=None) -> dict:
    rows = list(db[D.EVENTS].aggregate([
        {"$match": {**p.event_match(start), field: {"$type": "number"}}},
        {"$group": {"_id": None, "pct": {"$percentile": {"input": f"${field}", "p": [0.5, 0.95], "method": "approximate"}}}},
    ]))
    if not rows:
        return {"p50": None, "p95": None}
    return {"p50": round(rows[0]["pct"][0]), "p95": round(rows[0]["pct"][1])}


def _distinct_users(db, p: Params, since: datetime) -> int:
    m = p.rollup_match(since, p.end)
    return len(db[D.ROLLUPS].distinct("user", m))


def overview(db, p: Params) -> dict:
    cur = _totals(db, p, p.start, p.end)
    prev = _totals(db, p, p.prev_start, p.start)
    acc = _acceptance(db, p)
    acc_prev_only = _acceptance_between(db, p, p.prev_start, p.start)
    lat = _percentiles(db, p, "latency_ms.total")
    now = p.end
    days = p.day_labels()

    by_day_action = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": {"day": p.day("$hour"), "action": "$action"}, "n": {"$sum": "$requests"}}},
    ]))
    actions = sorted({r["_id"]["action"] for r in by_day_action})
    series = {a: {d: 0 for d in days} for a in actions}
    for r in by_day_action:
        if r["_id"]["day"] in series[r["_id"]["action"]]:
            series[r["_id"]["action"]][r["_id"]["day"]] = r["n"]

    dau_rows = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": p.day("$hour"), "users": {"$addToSet": "$user"}}},
        {"$project": {"n": {"$size": "$users"}}},
    ]))
    dau = {d: 0 for d in days}
    for r in dau_rows:
        if r["_id"] in dau:
            dau[r["_id"]] = r["n"]

    teams = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": "$team", "users": {"$addToSet": "$user"}, "requests": {"$sum": "$requests"},
                    "cost_usd": {"$sum": "$cost_usd"}, "errors": {"$sum": "$errors"}}},
        {"$project": {"team": "$_id", "_id": 0, "users": {"$size": "$users"}, "requests": 1, "cost_usd": 1, "errors": 1}},
        {"$sort": {"requests": -1}},
    ]))
    synthetic = db[D.ROLLUPS].count_documents({"hour": {"$gte": p.start}, "synthetic": True}, limit=1) > 0

    return {
        "range": {"days": p.days, "start": p.start.isoformat(), "end": p.end.isoformat(), "tz": p.tz},
        "has_synthetic": synthetic,
        "tiles": {
            "active_users": {"value": cur["active_users"], "prev": prev["active_users"]},
            "dau": _distinct_users(db, p, now - timedelta(days=1)),
            "wau": _distinct_users(db, p, now - timedelta(days=7)),
            "requests": {"value": cur["requests"], "prev": prev["requests"]},
            "acceptance": {"value": acc["rate"], "prev": acc_prev_only["rate"], "accepted": acc["accepted"],
                           "rejected": acc["rejected"]},
            "grounded": {"value": _ratio(cur["with_sources"], cur["requests"]),
                         "prev": _ratio(prev["with_sources"], prev["requests"])},
            "latency_p95": lat["p95"],
            "latency_p50": lat["p50"],
            "cost_usd": {"value": round(cur["cost_usd"], 4), "prev": round(prev["cost_usd"], 4)},
            "error_rate": {"value": _ratio(cur["errors"], cur["requests"]), "prev": _ratio(prev["errors"], prev["requests"])},
        },
        "days": days,
        "requests_by_action": series,
        "daily_active_users": [dau[d] for d in days],
        "teams": [_clean(t) for t in teams],
    }


def _acceptance_between(db, p: Params, start, end) -> dict:
    m = {"ts": {"$gte": start, "$lt": end}, "accepted": {"$in": [True, False]}}
    if p.team:
        m["team"] = p.team
    rows = list(db[D.OUTCOMES].aggregate([{"$match": m}, {"$group": {"_id": "$accepted", "n": {"$sum": 1}}}]))
    counts = {r["_id"]: r["n"] for r in rows}
    acc, rej = counts.get(True, 0), counts.get(False, 0)
    return {"accepted": acc, "rejected": rej, "rate": _ratio(acc, acc + rej)}


def users(db, p: Params) -> dict:
    rows = {r["_id"]: r for r in db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": "$user", "team": {"$first": "$team"}, "name": {"$first": "$name"}, **_sum_fields()}},
    ])}
    actions: dict[str, dict[str, int]] = {}
    for r in db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": {"user": "$user", "action": "$action"}, "n": {"$sum": "$requests"}}},
    ]):
        actions.setdefault(r["_id"]["user"], {})[r["_id"]["action"]] = r["n"]
    last = {r["_id"]: r for r in db[D.EVENTS].aggregate([
        {"$match": p.event_match(p.end - timedelta(days=90))},
        {"$group": {"_id": "$meta.user", "last_active": {"$max": "$ts"},
                    "ide": {"$top": {"sortBy": {"ts": -1}, "output": "$meta.ide"}}}},
    ])}
    acc: dict[str, dict] = {}
    for r in db[D.OUTCOMES].aggregate([
        {"$match": {**p.outcome_match(), "accepted": {"$in": [True, False]}}},
        {"$group": {"_id": {"user": "$user", "accepted": "$accepted"}, "n": {"$sum": 1}}},
    ]):
        acc.setdefault(r["_id"]["user"], {})[r["_id"]["accepted"]] = r["n"]

    directory = {u.user: u for u in all_users()}
    names = set(rows) | {u for u in directory if not p.team or directory[u].team == p.team}
    out = []
    for name in sorted(names):
        r = rows.get(name, {})
        u = directory.get(name)
        a = acc.get(name, {})
        n_acc, n_rej = a.get(True, 0), a.get(False, 0)
        act = actions.get(name, {})
        out.append({
            "user": name,
            "name": (u.name if u else None) or r.get("name") or name,
            "team": (u.team if u else None) or r.get("team"),
            "role": u.role if u else "developer",
            "requests": r.get("requests", 0),
            "errors": r.get("errors", 0),
            "error_rate": _ratio(r.get("errors", 0), r.get("requests", 0)),
            "grounded_rate": _ratio(r.get("with_sources", 0), r.get("requests", 0)),
            "cost_usd": round(r.get("cost_usd", 0), 4),
            "avg_latency_ms": round(r["latency_sum"] / r["requests"]) if r.get("requests") else None,
            "acceptance": _ratio(n_acc, n_acc + n_rej),
            "accepted": n_acc,
            "top_action": max(act, key=act.get) if act else None,
            "actions": act,
            "last_active": last[name]["last_active"].isoformat() if name in last else None,
            "ide": last[name]["ide"] if name in last else None,
        })
    out.sort(key=lambda x: x["requests"], reverse=True)
    return {"users": out}


def user_detail(db, p: Params, user: str) -> dict:
    days = p.day_labels()
    daily = {d: {} for d in days}
    for r in db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match(user=user)},
        {"$group": {"_id": {"day": p.day("$hour"), "action": "$action"}, "n": {"$sum": "$requests"}}},
    ]):
        if r["_id"]["day"] in daily:
            daily[r["_id"]["day"]][r["_id"]["action"]] = r["n"]
    repos = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match(user=user, repo={"$ne": None})},
        {"$group": {"_id": "$repo", "requests": {"$sum": "$requests"}}},
        {"$sort": {"requests": -1}}, {"$limit": 10},
    ]))
    recent = [_event_row(e) for e in db[D.EVENTS].find(p.event_match(**{"meta.user": user})).sort("ts", -1).limit(50)]
    outcomes = [_clean({k: v for k, v in o.items() if k in ("ts", "event", "accepted", "feedback", "ide", "source")})
                for o in db[D.OUTCOMES].find({**p.outcome_match(), "user": user}).sort("ts", -1).limit(20)]
    summary = list(db[D.ROLLUPS].aggregate([{"$match": p.rollup_match(user=user)},
                                            {"$group": {"_id": None, **_sum_fields()}}]))
    s = summary[0] if summary else {f: 0 for f in SUMS}
    s.pop("_id", None)
    actions = sorted({a for v in daily.values() for a in v})
    return {
        "user": user,
        "days": days,
        "daily": {a: [daily[d].get(a, 0) for d in days] for a in actions},
        "repos": [{"repo": r["_id"], "requests": r["requests"]} for r in repos],
        "recent": recent,
        "outcomes": outcomes,
        "summary": _clean(s),
        "acceptance": _acceptance_for(db, p, user),
    }


def _acceptance_for(db, p: Params, user: str) -> dict:
    rows = list(db[D.OUTCOMES].aggregate([
        {"$match": {**p.outcome_match(), "user": user, "accepted": {"$in": [True, False]}}},
        {"$group": {"_id": "$accepted", "n": {"$sum": 1}}},
    ]))
    c = {r["_id"]: r["n"] for r in rows}
    return {"accepted": c.get(True, 0), "rejected": c.get(False, 0), "rate": _ratio(c.get(True, 0), c.get(True, 0) + c.get(False, 0))}


def _event_row(e: dict) -> dict:
    m = e.get("meta", {})
    lat = e.get("latency_ms", {}) or {}
    return {
        "ts": e["ts"].isoformat(), "request_id": e.get("request_id"), "user": m.get("user"), "team": m.get("team"),
        "action": m.get("action"), "repo": m.get("repo"), "ide": m.get("ide"), "channel": m.get("channel"),
        "status": e.get("status"), "error": e.get("error"), "latency_ms": lat.get("total"),
        "sources": e.get("sources", 0), "cited": e.get("cited", 0), "no_context": e.get("no_context", False),
        "filtered_by_acl": e.get("filtered_by_acl", 0), "cost_usd": e.get("cost_usd", 0),
        "standards": e.get("standards_used", []), "repos": e.get("repos", []), "synthetic": m.get("synthetic", False),
        "query": e.get("query"),
    }


def quality(db, p: Params) -> dict:
    days = p.day_labels()
    daily_rows = {r["_id"]: r for r in db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match(action={"$nin": ["apply"]}, channel={"$in": ["ide", "playground"]})},
        {"$group": {"_id": p.day("$hour"), **_sum_fields()}},
    ])}

    def series(num, den):
        return [_ratio(daily_rows[d][num], daily_rows[d][den]) if d in daily_rows else None for d in days]

    by_event = list(db[D.OUTCOMES].aggregate([
        {"$match": {**p.outcome_match(), "accepted": {"$in": [True, False]}}},
        {"$group": {"_id": "$event", "accepted": {"$sum": {"$cond": ["$accepted", 1, 0]}}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]))
    feedback = list(db[D.OUTCOMES].aggregate([
        {"$match": {**p.outcome_match(), "feedback": {"$in": [True, False]}}},
        {"$group": {"_id": "$feedback", "n": {"$sum": 1}}},
    ]))
    no_ctx_by_team = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match(action={"$nin": ["apply"]})},
        {"$group": {"_id": "$team", "no_context": {"$sum": "$no_context"}, "requests": {"$sum": "$requests"}}},
        {"$sort": {"no_context": -1}},
    ]))
    by_action = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": "$action", "requests": {"$sum": "$requests"}, "with_sources": {"$sum": "$with_sources"},
                    "cited_answers": {"$sum": "$cited_answers"}, "invalid": {"$sum": "$invalid_citations"},
                    "citations": {"$sum": "$citations"}}},
        {"$sort": {"requests": -1}},
    ]))
    evals = [_eval_summary(r) for r in db[D.EVAL_RUNS].find({}, {"results": 0}).sort("started_at", -1).limit(12)]
    latest = db[D.EVAL_RUNS].find_one(sort=[("started_at", -1)])
    no_context_queries = []
    if get_settings().log_prompts:
        no_context_queries = [_event_row(e) for e in db[D.EVENTS].find(
            p.event_match(no_context=True, query={"$exists": True})).sort("ts", -1).limit(15)]
    fb = {r["_id"]: r["n"] for r in feedback}
    return {
        "days": days,
        "grounded_rate": series("with_sources", "requests"),
        "no_context_rate": series("no_context", "requests"),
        "cited_rate": series("cited_answers", "requests"),
        "acceptance_by_event": [{"event": r["_id"], "accepted": r["accepted"], "total": r["n"],
                                 "rate": _ratio(r["accepted"], r["n"])} for r in by_event],
        "feedback": {"helpful": fb.get(True, 0), "not_helpful": fb.get(False, 0)},
        "no_context_by_team": [{"team": r["_id"], "no_context": r["no_context"], "requests": r["requests"],
                                "rate": _ratio(r["no_context"], r["requests"])} for r in no_ctx_by_team],
        "by_action": [{"action": r["_id"], "requests": r["requests"],
                       "grounded_rate": _ratio(r["with_sources"], r["requests"]),
                       "cited_rate": _ratio(r["cited_answers"], r["requests"]),
                       "invalid_citation_rate": _ratio(r["invalid"], r["citations"])} for r in by_action],
        "eval_runs": evals,
        "latest_eval": _eval_detail(latest) if latest else None,
        "no_context_queries": no_context_queries,
        "prompt_logging": get_settings().log_prompts,
    }


def _eval_summary(r: dict) -> dict:
    return {"id": str(r["_id"]), "started_at": r["started_at"].isoformat(), "model": r.get("model"),
            **(r.get("summary") or {})}


def _eval_detail(r: dict) -> dict:
    out = _eval_summary(r)
    out["results"] = [{k: v for k, v in x.items() if k != "answer"} for x in r.get("results", [])]
    return out


def performance(db, p: Params) -> dict:
    days = p.day_labels()
    fields = {"total": "latency_ms.total", "llm": "latency_ms.llm", "ttft": "latency_ms.ttft",
              "embed": "latency_ms.embed", "search": "latency_ms.search", "rerank": "latency_ms.rerank"}
    group = {name: {"$percentile": {"input": f"${path}", "p": [0.5, 0.95], "method": "approximate"}}
             for name, path in fields.items()}
    rows = {r["_id"]: r for r in db[D.EVENTS].aggregate([
        {"$match": p.event_match()},
        {"$group": {"_id": p.day("$ts"), "n": {"$sum": 1},
                    "errors": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}}, **group}},
    ])}

    def pick(name, idx):
        out = []
        for d in days:
            v = rows.get(d, {}).get(name)
            out.append(round(v[idx]) if v and v[idx] is not None else None)
        return out

    overall = {name: _percentiles(db, p, path) for name, path in fields.items()}
    slowest = [_event_row(e) for e in db[D.EVENTS].find(p.event_match()).sort("latency_ms.total", -1).limit(10)]
    errors = [_event_row(e) for e in db[D.EVENTS].find(p.event_match(status="error")).sort("ts", -1).limit(10)]
    return {
        "days": days,
        "p50": {name: pick(name, 0) for name in fields},
        "p95": {name: pick(name, 1) for name in fields},
        "error_rate": [_ratio(rows[d]["errors"], rows[d]["n"]) if d in rows else None for d in days],
        "requests": [rows[d]["n"] if d in rows else 0 for d in days],
        "overall": overall,
        "slowest": slowest,
        "recent_errors": errors,
    }


def cost(db, p: Params) -> dict:
    days = p.day_labels()
    daily = {r["_id"]: r for r in db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": p.day("$hour"), "openai": {"$sum": "$cost_openai"}, "voyage": {"$sum": "$cost_voyage"}}},
    ])}
    by_team = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": "$team", "cost_usd": {"$sum": "$cost_usd"}, "requests": {"$sum": "$requests"},
                    "users": {"$addToSet": "$user"}}},
        {"$project": {"team": "$_id", "_id": 0, "cost_usd": 1, "requests": 1, "users": {"$size": "$users"}}},
        {"$sort": {"cost_usd": -1}},
    ]))
    by_user = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match()},
        {"$group": {"_id": "$user", "team": {"$first": "$team"}, "cost_usd": {"$sum": "$cost_usd"},
                    "requests": {"$sum": "$requests"}, "openai_in": {"$sum": "$openai_in"},
                    "openai_out": {"$sum": "$openai_out"}}},
        {"$sort": {"cost_usd": -1}}, {"$limit": 15},
    ]))
    totals = _totals(db, p, p.start, p.end)
    acc = _acceptance(db, p)
    runs = list(db[D.INGEST_RUNS].find({"started_at": {"$gte": p.start}}, {"embed_tokens": 1, "embed_model": 1}))
    index_tokens = sum(r.get("embed_tokens", 0) for r in runs)
    vprice = (events.pricing().get("voyage", {}) or {}).get(get_settings().embed_model, 0)
    index_cost = index_tokens * vprice / 1_000_000
    return {
        "days": days,
        "daily": {"openai": [round(daily.get(d, {}).get("openai", 0), 4) for d in days],
                  "voyage": [round(daily.get(d, {}).get("voyage", 0), 4) for d in days]},
        "totals": {"cost_usd": round(totals["cost_usd"], 4), "openai": round(totals["cost_openai"], 4),
                   "voyage_queries": round(totals["cost_voyage"], 4), "indexing": round(index_cost, 4),
                   "openai_in": totals["openai_in"], "openai_out": totals["openai_out"],
                   "voyage_tokens": totals["voyage_tokens"], "index_tokens": index_tokens,
                   "requests": totals["requests"],
                   "per_request": _ratio(totals["cost_usd"], totals["requests"]),
                   "per_accepted": _ratio(totals["cost_usd"], acc["accepted"]),
                   "per_active_user": _ratio(totals["cost_usd"], totals["active_users"])},
        "by_team": [_clean(t) for t in by_team],
        "by_user": [_clean({"user": r["_id"], **{k: v for k, v in r.items() if k != "_id"}}) for r in by_user],
        "pricing": events.pricing(),
        "model": get_settings().openai_model,
    }


def knowledge(db, p: Params) -> dict:
    repos = list(db[D.FILES].aggregate([
        {"$group": {"_id": "$repo", "kind": {"$first": "$kind"}, "files": {"$sum": 1}, "chunks": {"$sum": "$chunks"},
                    "indexed_at": {"$max": "$indexed_at"}, "access_scope": {"$first": "$access_scope"},
                    "languages": {"$addToSet": "$language"}}},
        {"$sort": {"_id": 1}},
    ]))
    runs = list(db[D.INGEST_RUNS].find().sort("started_at", -1).limit(15))
    last_by_repo: dict[str, dict] = {}
    for run in runs:
        for r in run.get("repos", []):
            last_by_repo.setdefault(r["name"], {**r, "run_at": run["started_at"]})
    now = datetime.now(timezone.utc)
    acl_users = list(db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match(filtered_by_acl={"$gt": 0})},
        {"$group": {"_id": {"user": "$user", "team": "$team"}, "hidden": {"$sum": "$filtered_by_acl"},
                    "requests": {"$sum": "$acl_affected"}}},
        {"$sort": {"hidden": -1}}, {"$limit": 15},
    ]))
    usage_by_repo = {r["_id"]: r["n"] for r in db[D.ROLLUPS].aggregate([
        {"$match": p.rollup_match(repo={"$ne": None})},
        {"$group": {"_id": "$repo", "n": {"$sum": "$requests"}}},
    ])}
    out_repos = []
    for r in repos:
        last = last_by_repo.get(r["_id"], {})
        indexed = r.get("indexed_at")
        run_at = last.get("run_at")
        out_repos.append({
            "repo": r["_id"], "kind": r.get("kind") or "code", "files": r["files"], "chunks": r["chunks"],
            "languages": sorted(x for x in r.get("languages", []) if x),
            "access_scope": r.get("access_scope") or [],
            "last_change_indexed": indexed.isoformat() if indexed else None,
            "last_checked": run_at.isoformat() if run_at else None,
            "minutes_since_check": int((now - run_at).total_seconds() // 60) if run_at else None,
            "commit": last.get("commit"), "errors": last.get("errors", []),
            "requests": usage_by_repo.get(r["_id"], 0),
        })
    return {
        "repos": out_repos,
        "total_chunks": db[D.CHUNKS].estimated_document_count(),
        "ingest_runs": [{
            "started_at": r["started_at"].isoformat(),
            "seconds": round((r.get("finished_at", r["started_at"]) - r["started_at"]).total_seconds(), 1),
            "ok": r.get("ok"), "embed_tokens": r.get("embed_tokens", 0),
            "files_changed": sum(x.get("files_changed", 0) for x in r.get("repos", [])),
            "chunks_written": sum(x.get("chunks_written", 0) for x in r.get("repos", [])),
            "errors": [e for x in r.get("repos", []) for e in x.get("errors", [])][:5],
        } for r in runs],
        "acl": [{"user": r["_id"]["user"], "team": r["_id"]["team"], "hidden": r["hidden"],
                 "requests": r["requests"]} for r in acl_users],
    }


def audit(db, q) -> dict:
    p = Params.from_query(q)
    extra = {}
    for key, field in (("user", "meta.user"), ("action", "meta.action"), ("repo", "meta.repo"),
                       ("channel", "meta.channel"), ("status", "status")):
        if q.get(key):
            extra[field] = q[key]
    if q.get("before"):
        try:
            extra["ts"] = {"$gte": p.start, "$lt": datetime.fromisoformat(q["before"])}
        except ValueError:
            pass
    limit = max(1, min(int(q.get("limit", 100)), 500))
    rows = [_event_row(e) for e in db[D.EVENTS].find({**p.event_match(), **extra}).sort("ts", -1).limit(limit)]
    admin = [_clean({"ts": a["ts"], "actor": a["actor"], "action": a["action"], **(a.get("detail") or {})})
             for a in db[D.AUDIT].find({"ts": {"$gte": p.start}}).sort("ts", -1).limit(50)]
    return {"events": rows, "admin_actions": admin, "next_before": rows[-1]["ts"] if len(rows) == limit else None}
