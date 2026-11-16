"""Eval job: runs a fixed question set through the real agent and scores it.

Scores per question (only the checks a question defines are applied):
- retrieval_hit   an expected file was among the retrieved sources
- cited_expected  the answer cites (valid label) a source from an expected file
- standards_hit   the expected coding-standard clauses were retrieved
- acl_ok          nothing was retrieved from repositories the user must not see
- citations_valid every [S#] label in the answer exists
"""
from __future__ import annotations

import logging
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import db as D
from ..settings import get_settings
from ..users import get_user
from ..api import agent, prompting

log = logging.getLogger("eval")


def load_questions(path: str | None = None) -> list[dict]:
    path = path or os.environ.get("EVAL_QUESTIONS", "/eval/questions.yaml")
    with Path(path).open() as fh:
        return (yaml.safe_load(fh) or {}).get("questions", [])


def _matches(hit: dict, expected: str) -> bool:
    return f"{hit['repo']}/{hit['path']}" == expected or hit["path"] == expected


def score(question: dict, hits: list[dict], answer: str) -> dict:
    _, invalid, valid_labels = prompting.citation_stats(answer, len(hits))
    checks: dict[str, bool] = {}
    if question.get("expect_paths"):
        exp = question["expect_paths"]
        checks["retrieval_hit"] = any(_matches(h, e) for h in hits for e in exp)
        checks["cited_expected"] = any(_matches(hits[i - 1], e) for i in valid_labels for e in exp)
    if question.get("expect_clauses"):
        got = {h.get("clause") for h in hits if h["kind"] == "standard"}
        checks["standards_hit"] = all(c in got for c in question["expect_clauses"])
    if question.get("forbid_repos"):
        checks["acl_ok"] = not any(h["repo"] in question["forbid_repos"] for h in hits if h["kind"] == "code")
    checks["citations_valid"] = invalid == 0
    return checks


def _rate(results: list[dict], key: str) -> float | None:
    vals = [r["checks"][key] for r in results if key in r["checks"]]
    return round(sum(vals) / len(vals), 4) if vals else None


def run_eval(path: str | None = None, database=None) -> dict:
    settings = get_settings()
    database = database if database is not None else D.get_db()
    started = datetime.now(timezone.utc)
    results = []
    for q in load_questions(path):
        user = get_user(q.get("as_user", ""))
        if not user:
            log.warning("Skipping %s: unknown user %s", q.get("id"), q.get("as_user"))
            continue
        body = {"model": agent.AGENT_MODEL, "stream": False, "messages": [{"role": "user", "content": q["question"]}]}
        try:
            prep = agent.prepare(user, body, channel="eval", ide="eval", repos=q.get("repos"))
            data, event = agent.complete(prep)
            answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            hits = prep.retrieval.hits if prep.retrieval else []
            checks = score(q, hits, answer)
            results.append({
                "id": q.get("id"), "as_user": user.user, "question": q["question"],
                "checks": checks, "passed": all(checks.values()),
                "sources": [prompting.source_location(h) for h in hits],
                "latency_ms": event["latency_ms"]["total"], "cost_usd": event["cost_usd"],
                "filtered_by_acl": event["filtered_by_acl"], "answer": answer[:4000],
            })
            log.info("%-32s %s %s", q.get("id"), "PASS" if all(checks.values()) else "FAIL", checks)
        except Exception as exc:
            log.exception("Question %s failed", q.get("id"))
            results.append({"id": q.get("id"), "as_user": user.user, "question": q["question"], "checks": {},
                            "passed": False, "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"})
    latencies = [r["latency_ms"] for r in results if "latency_ms" in r]
    summary = {
        "questions": len(results),
        "pass_rate": round(sum(r["passed"] for r in results) / len(results), 4) if results else None,
        "retrieval_hit_rate": _rate(results, "retrieval_hit"),
        "citation_accuracy": _rate(results, "cited_expected"),
        "standards_hit_rate": _rate(results, "standards_hit"),
        "acl_pass_rate": _rate(results, "acl_ok"),
        "citations_valid_rate": _rate(results, "citations_valid"),
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else None,
        "latency_max_ms": max(latencies) if latencies else None,
        "cost_usd": round(sum(r.get("cost_usd", 0) for r in results), 4),
    }
    run = {"started_at": started, "finished_at": datetime.now(timezone.utc), "model": settings.openai_model,
           "embed_model": settings.embed_model, "rerank_model": settings.rerank_model, "summary": summary,
           "results": results}
    database[D.EVAL_RUNS].insert_one(run)
    log.info("Eval summary: %s", summary)
    return run
