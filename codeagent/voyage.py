"""Minimal Voyage AI client (embeddings + rerank) over HTTP.

Works with keys issued by voyageai.com (https://api.voyageai.com/v1) and keys created
in the Atlas UI (set VOYAGE_BASE_URL=https://ai.mongodb.com/v1).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .settings import get_settings

MAX_BATCH = 64
MAX_CHARS_PER_TEXT = 24000  # keeps each input well inside the model context


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    tokens: int


class VoyageError(RuntimeError):
    pass


def _post(path: str, payload: dict, retries: int = 4) -> dict:
    settings = get_settings()
    if not settings.voyage_api_key:
        raise VoyageError("VOYAGE_API_KEY is not set")
    url = f"{settings.voyage_base_url}/{path}"
    headers = {"Authorization": f"Bearer {settings.voyage_api_key}", "Content-Type": "application/json"}
    delay = 2.0
    for attempt in range(retries + 1):
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == retries:
                break
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code >= 400:
            raise VoyageError(f"Voyage {path} failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()
    raise VoyageError(f"Voyage {path} failed after retries ({resp.status_code}): {resp.text[:300]}")


def embed(texts: list[str], input_type: str) -> EmbedResult:
    """input_type is 'document' at index time and 'query' at query time."""
    settings = get_settings()
    vectors: list[list[float]] = []
    tokens = 0
    for start in range(0, len(texts), MAX_BATCH):
        batch = [t[:MAX_CHARS_PER_TEXT] for t in texts[start:start + MAX_BATCH]]
        data = _post("embeddings", {
            "input": batch,
            "model": settings.embed_model,
            "input_type": input_type,
            "output_dimension": settings.embed_dimensions,
        })
        ordered = sorted(data["data"], key=lambda d: d["index"])
        vectors.extend(d["embedding"] for d in ordered)
        tokens += int(data.get("usage", {}).get("total_tokens", 0))
    return EmbedResult(vectors=vectors, tokens=tokens)


def rerank(query: str, documents: list[str], top_k: int) -> tuple[list[tuple[int, float]], int]:
    """Returns ([(original_index, score)], tokens). Empty list when reranking is disabled."""
    settings = get_settings()
    if not settings.rerank_model or not documents:
        return [], 0
    data = _post("rerank", {
        "query": query,
        "documents": [d[:8000] for d in documents],
        "model": settings.rerank_model,
        "top_k": min(top_k, len(documents)),
    })
    results = [(int(r["index"]), float(r["relevance_score"])) for r in data.get("data", [])]
    return results, int(data.get("usage", {}).get("total_tokens", 0))
