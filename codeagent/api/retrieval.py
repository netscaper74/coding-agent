"""Permission-aware hybrid retrieval over MongoDB Atlas.

1. Embed the query with Voyage (`input_type=query`).
2. In parallel: `$vectorSearch` and Atlas Search (`$search`), both pre-filtered on the
   developer's access scopes. A third, small vector query counts relevant chunks the
   developer is *not* allowed to see (governance metric; nothing from it is returned).
3. Fuse with reciprocal-rank fusion, rerank with Voyage, keep a few standards clauses.
"""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .. import db as D
from .. import voyage
from ..ingest.chunking import identifier_tokens
from ..settings import get_settings
from ..users import User

RRF_K = 60
MAX_STANDARDS = 3
PROJECTION = {
    "_id": 1, "kind": 1, "repo": 1, "branch": 1, "commit": 1, "path": 1, "language": 1, "symbol": 1,
    "unit": 1, "start_line": 1, "end_line": 1, "text": 1, "web_url": 1, "clause": 1, "title": 1,
    "version": 1, "standard_name": 1,
}

_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="retrieval")


@dataclass
class Retrieval:
    hits: list[dict]
    timings: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    filtered_by_acl: int = 0
    query: str = ""

    @property
    def repos(self) -> list[str]:
        seen: dict[str, None] = {}
        for h in self.hits:
            if h["kind"] == "code":
                seen.setdefault(h["repo"], None)
        return list(seen)


def _scope_filter(user: User, kinds: list[str], repos: list[str] | None) -> dict:
    clauses = [{"access_scope": {"$in": user.scopes}}, {"kind": {"$in": kinds}}]
    if repos:
        clauses.append({"$or": [{"repo": {"$in": repos}}, {"kind": "standard"}]})
    return {"$and": clauses}


def _vector(coll, vector, flt: dict, limit: int) -> list[dict]:
    pipeline = [
        {"$vectorSearch": {"index": D.VECTOR_INDEX, "path": "embedding", "queryVector": vector,
                           "numCandidates": max(limit * 10, 100), "limit": limit, "filter": flt}},
        {"$project": {**PROJECTION, "score": {"$meta": "vectorSearchScore"}}},
    ]
    return list(coll.aggregate(pipeline))


def _text(coll, query: str, user: User, kinds: list[str], repos: list[str] | None, limit: int) -> list[dict]:
    terms = (identifier_tokens(query, limit=60) or query)[:500]
    if not terms.strip():
        return []
    filters = [{"in": {"path": "access_scope", "value": user.scopes}},
               {"in": {"path": "kind", "value": kinds}}]
    compound = {
        "should": [
            {"text": {"query": terms, "path": ["text", "ident_tokens"]}},
            {"text": {"query": terms, "path": "path", "score": {"boost": {"value": 1.5}}}},
        ],
        "filter": filters,
        "minimumShouldMatch": 1,
    }
    if repos:
        compound["filter"].append({"compound": {"should": [
            {"in": {"path": "repo", "value": repos}},
            {"equals": {"path": "kind", "value": "standard"}},
        ], "minimumShouldMatch": 1}})
    pipeline = [
        {"$search": {"index": D.TEXT_INDEX, "compound": compound}},
        {"$limit": limit},
        {"$project": {**PROJECTION, "score": {"$meta": "searchScore"}}},
    ]
    return list(coll.aggregate(pipeline))


def _hidden_count(coll, vector, user: User, threshold: float) -> int:
    """How many strongly relevant chunks exist outside the user's scopes."""
    pipeline = [
        {"$vectorSearch": {"index": D.VECTOR_INDEX, "path": "embedding", "queryVector": vector,
                           "numCandidates": 100, "limit": 10,
                           "filter": {"access_scope": {"$nin": user.scopes}}}},
        {"$project": {"_id": 1, "score": {"$meta": "vectorSearchScore"}}},
    ]
    return sum(1 for d in coll.aggregate(pipeline) if d["score"] >= threshold)


def rrf_fuse(*ranked_lists: list[dict]) -> list[dict]:
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, doc in enumerate(ranked):
            key = doc["_id"]
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            docs.setdefault(key, doc)
    fused = sorted(docs.values(), key=lambda d: scores[d["_id"]], reverse=True)
    for d in fused:
        d["rrf"] = round(scores[d["_id"]], 5)
    return fused


def select_final(ranked: list[dict], k: int) -> list[dict]:
    """Top-k overall, but keep at most MAX_STANDARDS standards and at least one if any matched."""
    standards = [d for d in ranked if d["kind"] == "standard"][:MAX_STANDARDS]
    code = [d for d in ranked if d["kind"] != "standard"]
    n_std = min(len(standards), max(1 if standards else 0, sum(1 for d in ranked[:k] if d["kind"] == "standard")))
    chosen = code[: max(k - n_std, 0)] + standards[:n_std]
    order = {d["_id"]: i for i, d in enumerate(ranked)}
    return sorted(chosen, key=lambda d: order[d["_id"]])


def _rerank_text(d: dict) -> str:
    if d["kind"] == "standard":
        return f"{d.get('clause')} {d.get('title')}\n{d['text']}"
    return f"{d['repo']}/{d['path']} {d.get('symbol')}\n{d['text']}"


def retrieve(query: str, user: User, *, kinds: list[str] | None = None, repos: list[str] | None = None,
             k: int | None = None, database=None) -> Retrieval:
    settings = get_settings()
    database = database if database is not None else D.get_db()
    coll = database[D.CHUNKS]
    kinds = kinds or ["code", "standard"]
    k = k or settings.top_k
    cand = settings.candidates
    timings: dict[str, int] = {}
    tokens = {"voyage_embed": 0, "voyage_rerank": 0}
    query = query.strip()[:4000]
    if not query:
        return Retrieval([], timings, tokens, 0, query)

    t0 = time.perf_counter()
    emb = voyage.embed([query], input_type="query")
    vector = emb.vectors[0]
    tokens["voyage_embed"] = emb.tokens
    timings["embed"] = int((time.perf_counter() - t0) * 1000)

    t1 = time.perf_counter()
    flt = _scope_filter(user, kinds, repos)
    f_vec = _pool.submit(_vector, coll, vector, flt, cand)
    f_txt = _pool.submit(_text, coll, query, user, kinds, repos, cand)
    vec_hits = f_vec.result()
    try:
        txt_hits = f_txt.result()
    except Exception:  # text index still building, etc. Vector results alone are fine.
        txt_hits = []
    timings["search"] = int((time.perf_counter() - t1) * 1000)

    # Relevance bar for the governance count: the k-th best permitted vector score.
    threshold = sorted((h["score"] for h in vec_hits), reverse=True)[min(k, len(vec_hits)) - 1] if vec_hits else 1.0
    f_hidden = _pool.submit(_hidden_count, coll, vector, user, threshold)

    fused = rrf_fuse(vec_hits, txt_hits)[:cand]
    t2 = time.perf_counter()
    ranked = fused
    try:
        order, rerank_tokens = voyage.rerank(query, [_rerank_text(d) for d in fused], top_k=len(fused))
        if order:
            ranked = []
            for idx, score in order:
                fused[idx]["rerank"] = round(score, 4)
                ranked.append(fused[idx])
            tokens["voyage_rerank"] = rerank_tokens
    except voyage.VoyageError:
        ranked = fused
    timings["rerank"] = int((time.perf_counter() - t2) * 1000)

    try:
        hidden = f_hidden.result(timeout=5)
    except Exception:
        hidden = 0
    return Retrieval(select_final(ranked, k), timings, tokens, hidden, query)


# ----------------------------------------------------------------- small TTL cache
# Continue's agent mode re-sends the conversation after every tool call; we reuse the
# retrieval for the same question instead of embedding and searching again.

_cache: dict[str, tuple[float, Retrieval]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 600


def retrieve_cached(query: str, user: User, **kwargs) -> tuple[Retrieval, bool]:
    key = hashlib.sha1(f"{user.user}|{sorted(kwargs.items())}|{query}".encode()).hexdigest()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1], True
    result = retrieve(query, user, **kwargs)
    with _cache_lock:
        if len(_cache) > 2000:
            for old in sorted(_cache, key=lambda x: _cache[x][0])[:500]:
                _cache.pop(old, None)
        _cache[key] = (now, result)
    return result, False
