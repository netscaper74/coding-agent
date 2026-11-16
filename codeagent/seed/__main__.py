"""Synthetic usage so the dashboard has something to show in a demo.

    python -m codeagent.seed            # 14 days of activity for ~14 fictional developers
    python -m codeagent.seed --purge    # remove all synthetic data

Every generated document is flagged `synthetic: true`; the dashboard can hide it.
"""
from __future__ import annotations

import argparse
import logging
import math
import random
import uuid
from datetime import datetime, timedelta, timezone

from .. import db as D
from ..indexes import ensure_collections
from ..api import events, rollups

DEVS = [
    ("lina", "Lina Farouk", "payments", "vscode"), ("omar", "Omar Saleh", "payments", "jetbrains"),
    ("priya", "Priya Nair", "payments", "vscode"), ("tom", "Tom Becker", "web", "vscode"),
    ("sara", "Sara Haddad", "web", "vscode"), ("jun", "Jun Park", "web", "jetbrains"),
    ("maya", "Maya Cohen", "web", "vscode"), ("ali", "Ali Rahimi", "platform", "jetbrains"),
    ("nina", "Nina Petrova", "platform", "vscode"), ("david", "David Mensah", "platform", "vscode"),
    ("hana", "Hana Yousef", "data", "jetbrains"), ("leo", "Leo Martins", "data", "vscode"),
    ("zara", "Zara Ahmed", "payments", "vscode"), ("ravi", "Ravi Kumar", "platform", "jetbrains"),
]
REPOS = {"payments": ["payments-svc"], "web": ["web-portal"], "platform": ["payments-svc", "web-portal"],
         "data": ["payments-svc"]}
ACTIONS = [("explain", 0.34), ("chat", 0.18), ("edit", 0.22), ("tests", 0.12), ("apply", 0.08),
           ("mcp:search_code", 0.06)]
STANDARD_CLAUSES = ["SCS-2.1", "SCS-3.2", "SCS-4.1", "SCS-4.3", "SCS-5.2", "SCS-6.1"]
WEEKEND = {5, 6}  # Saturday, Sunday


def _pick(rng, weighted):
    r, acc = rng.random(), 0.0
    for value, w in weighted:
        acc += w
        if r <= acc:
            return value
    return weighted[-1][0]


def generate(days: int = 14, seed: int = 7):
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    evs, outs = [], []
    joined = {d[0]: rng.randint(0, days // 2) for d in DEVS}  # staggered adoption
    for day in range(days + 1):
        date = start + timedelta(days=day)
        weekday = date.weekday()
        for user, name, team, ide in DEVS:
            if day < joined[user] or rng.random() < (0.85 if weekday in WEEKEND else 0.12):
                continue
            ramp = min(1.0, 0.4 + 0.08 * (day - joined[user]))
            n = int(rng.gauss(14, 5) * ramp)
            for _ in range(max(n, 1)):
                hour = min(23, max(4, int(rng.gauss(9.5, 2.5))))  # UTC, i.e. Dubai working hours
                ts = date.replace(hour=hour) + timedelta(minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
                if ts > now:
                    continue
                action = _pick(rng, ACTIONS)
                repo = rng.choice(REPOS[team])
                mcp = action.startswith("mcp:")
                no_ctx = action != "apply" and rng.random() < (0.05 if team != "data" else 0.18)
                sources = 0 if (action == "apply" or no_ctx) else rng.randint(4, 8)
                embed_ms = int(rng.gauss(120, 30)) if action != "apply" else 0
                search_ms = int(rng.gauss(160, 50)) if action != "apply" else 0
                rerank_ms = int(rng.gauss(90, 25)) if action != "apply" else 0
                llm_ms = 0 if mcp else int(abs(rng.gauss(4200 if action in ("edit", "tests") else 2600, 1200)))
                tokens_in = 0 if mcp else int(rng.gauss(6500 if sources else 2500, 1500))
                tokens_out = 0 if mcp else int(abs(rng.gauss(700 if action in ("edit", "tests") else 380, 200)))
                v_embed = 0 if action == "apply" else rng.randint(30, 400)
                v_rerank = 0 if action == "apply" else rng.randint(2500, 9000)
                c_openai = events.openai_cost("gpt-5-mini", max(tokens_in, 0), tokens_out)
                c_voyage = events.voyage_cost(v_embed, v_rerank)
                error = rng.random() < 0.015
                hidden = rng.randint(1, 4) if (team == "web" and rng.random() < 0.15) else 0
                cited = 0 if (mcp or not sources) else rng.randint(1, min(4, sources))
                evs.append({
                    "ts": ts,
                    "meta": {"user": user, "name": name, "team": team, "action": action,
                             "repo": None if no_ctx else repo, "ide": ide, "channel": "mcp" if mcp else "ide",
                             "model": None if mcp else "gpt-5-mini", "synthetic": True},
                    "request_id": f"ra-{uuid.uuid4().hex[:16]}",
                    "status": "error" if error else "ok",
                    "error": "openai 429" if error else None,
                    "latency_ms": {"total": embed_ms + search_ms + rerank_ms + llm_ms + rng.randint(20, 80),
                                   "embed": embed_ms, "search": search_ms, "rerank": rerank_ms, "llm": llm_ms,
                                   "ttft": None if mcp else int(abs(rng.gauss(900, 250)))},
                    "tokens": {"openai_in": max(tokens_in, 0), "openai_out": tokens_out,
                               "voyage_embed": v_embed, "voyage_rerank": v_rerank},
                    "cost_usd": round(c_openai + c_voyage, 6),
                    "cost": {"openai": round(c_openai, 6), "voyage": round(c_voyage, 6)},
                    "sources": sources, "repos": [] if no_ctx else [repo],
                    "standards_used": rng.sample(STANDARD_CLAUSES, k=min(2, sources)) if sources else [],
                    "no_context": no_ctx, "filtered_by_acl": hidden,
                    "cited": cited, "invalid_citations": 1 if (cited and rng.random() < 0.03) else 0,
                    "tool_calls": False, "response_chars": tokens_out * 4,
                })
                if action in ("edit", "tests") and not error:
                    p_accept = 0.55 + 0.25 * math.tanh((day - joined[user]) / 6)
                    outs.append({"ts": ts + timedelta(seconds=rng.randint(20, 240)), "user": user, "team": team,
                                 "ide": ide, "source": "continue", "event": "editOutcome",
                                 "accepted": rng.random() < p_accept, "synthetic": True})
                elif action in ("explain", "chat") and rng.random() < 0.12:
                    outs.append({"ts": ts + timedelta(seconds=rng.randint(10, 120)), "user": user, "team": team,
                                 "ide": ide, "source": "continue", "event": "chatFeedback",
                                 "feedback": rng.random() < 0.82, "synthetic": True})
    return evs, outs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s seed %(message)s")
    db = D.get_db()
    ensure_collections(db)
    db[D.EVENTS].delete_many({"meta.synthetic": True})
    db[D.OUTCOMES].delete_many({"synthetic": True})
    db[D.ROLLUPS].delete_many({"synthetic": True})
    if not args.purge:
        evs, outs = generate(args.days)
        for i in range(0, len(evs), 2000):
            db[D.EVENTS].insert_many(evs[i:i + 2000], ordered=False)
        if outs:
            db[D.OUTCOMES].insert_many(outs, ordered=False)
        logging.info("Inserted %d synthetic events and %d outcomes", len(evs), len(outs))
    else:
        logging.info("Removed synthetic data")
    rollups.refresh(db, hours=24 * (args.days + 2))
    logging.info("Rollups refreshed")


if __name__ == "__main__":
    main()
