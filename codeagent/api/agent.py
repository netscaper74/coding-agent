"""The coding agent: OpenAI-compatible chat completions with repository grounding.

Continue.dev (or any OpenAI-compatible client) talks to this as if it were the model.
Each request: classify -> retrieve (permission-aware) -> augment prompt -> OpenAI ->
stream back (model name rewritten) -> record a usage event.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator

import requests

from ..settings import get_settings
from ..users import User
from . import events, prompting
from .retrieval import Retrieval, retrieve_cached

log = logging.getLogger(__name__)

AGENT_MODEL = "repo-agent"          # retrieval + grounding
APPLY_MODEL = "repo-agent-apply"    # plain pass-through, used by Continue's "apply" role
MODELS = [AGENT_MODEL, APPLY_MODEL]

PASSTHROUGH_FIELDS = {
    "messages", "tools", "tool_choice", "stream", "stop", "max_tokens", "max_completion_tokens",
    "temperature", "top_p", "response_format", "parallel_tool_calls", "seed", "presence_penalty",
    "frequency_penalty", "reasoning_effort", "logit_bias", "user",
}
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
FOOTER_ACTIONS = {"chat", "explain"}


class UpstreamError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(body)
        self.status, self.body = status, body


@dataclass
class Prepared:
    request_id: str
    user: User
    requested_model: str
    action: str
    channel: str
    ide: str
    stream: bool
    payload: dict
    retrieval: Retrieval | None
    cache_hit: bool = False
    retrieval_error: str | None = None
    started: float = field(default_factory=time.perf_counter)
    repo_hint: list[str] | None = None


def upstream_payload(body: dict, messages: list[dict], model: str, stream: bool) -> dict:
    payload = {k: v for k, v in body.items() if k in PASSTHROUGH_FIELDS}
    payload["messages"] = messages
    payload["model"] = model
    payload["stream"] = stream
    if "max_tokens" in payload:
        payload.setdefault("max_completion_tokens", payload.pop("max_tokens"))
    if model.startswith(REASONING_PREFIXES):
        for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty", "logit_bias", "stop"):
            payload.pop(key, None)
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if not payload.get("tools"):
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload.pop("parallel_tool_calls", None)
    return payload


def prepare(user: User, body: dict, *, channel: str = "ide", ide: str = "unknown",
            repos: list[str] | None = None) -> Prepared:
    settings = get_settings()
    requested = str(body.get("model") or AGENT_MODEL)
    messages = list(body.get("messages") or [])
    stream = bool(body.get("stream", False))
    action = prompting.classify(messages, requested)
    retrieval, cache_hit, error = None, False, None

    if action != "apply":
        query = prompting.retrieval_query(messages)
        try:
            retrieval, cache_hit = retrieve_cached(query, user, repos=repos)
        except Exception as exc:  # Atlas/Voyage trouble: still answer, but say so
            log.warning("Retrieval failed: %s", exc)
            error = f"retrieval: {exc.__class__.__name__}: {str(exc)[:200]}"
        hits = retrieval.hits if retrieval else []
        messages = prompting.augment(messages, hits, settings.max_context_chars)

    return Prepared(
        request_id=f"ra-{uuid.uuid4().hex[:16]}",
        user=user, requested_model=requested, action=action, channel=channel, ide=ide,
        stream=stream, payload=upstream_payload(body, messages, settings.openai_model, stream),
        retrieval=retrieval, cache_hit=cache_hit, retrieval_error=error, repo_hint=repos,
    )


def _post(payload: dict, stream: bool) -> requests.Response:
    settings = get_settings()
    if not settings.openai_api_key:
        raise UpstreamError(500, json.dumps({"error": {"message": "OPENAI_API_KEY is not set on the agent-api"}}))
    resp = requests.post(
        f"{settings.openai_base_url}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        stream=stream,
        timeout=(10, 600),
    )
    if resp.status_code >= 400:
        body = resp.text
        resp.close()
        raise UpstreamError(resp.status_code, body)
    return resp


def _event(prep: Prepared, *, answer: str, usage: dict, llm_ms: int, ttft_ms: int | None,
           tool_calls: bool, error: str | None) -> dict:
    settings = get_settings()
    r = prep.retrieval
    hits = r.hits if r else []
    n_cited, n_invalid, _ = prompting.citation_stats(answer, len(hits))
    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    voyage_embed = 0 if prep.cache_hit or not r else r.tokens.get("voyage_embed", 0)
    voyage_rerank = 0 if prep.cache_hit or not r else r.tokens.get("voyage_rerank", 0)
    cost_openai = events.openai_cost(settings.openai_model, tokens_in, tokens_out)
    cost_voyage = events.voyage_cost(voyage_embed, voyage_rerank)
    repos = r.repos if r else []
    event = {
        "meta": {
            "user": prep.user.user, "name": prep.user.name, "team": prep.user.team, "action": prep.action,
            "repo": repos[0] if repos else None, "ide": prep.ide, "channel": prep.channel,
            "model": settings.openai_model, "synthetic": False,
        },
        "request_id": prep.request_id,
        "status": "error" if error else "ok",
        "error": error or prep.retrieval_error,
        "latency_ms": {
            "total": int((time.perf_counter() - prep.started) * 1000),
            "embed": 0 if prep.cache_hit or not r else r.timings.get("embed", 0),
            "search": 0 if prep.cache_hit or not r else r.timings.get("search", 0),
            "rerank": 0 if prep.cache_hit or not r else r.timings.get("rerank", 0),
            "llm": llm_ms,
            "ttft": ttft_ms,
        },
        "tokens": {"openai_in": tokens_in, "openai_out": tokens_out,
                   "voyage_embed": voyage_embed, "voyage_rerank": voyage_rerank},
        "cost_usd": round(cost_openai + cost_voyage, 6),
        "cost": {"openai": round(cost_openai, 6), "voyage": round(cost_voyage, 6)},
        "retrieval_cache_hit": prep.cache_hit,
        "sources": len(hits),
        "source_ids": [h["_id"] for h in hits],
        "repos": repos,
        "standards_used": sorted({h.get("clause") for h in hits if h["kind"] == "standard"} - {None}),
        "no_context": prep.action != "apply" and not hits,
        "filtered_by_acl": r.filtered_by_acl if r else 0,
        "cited": n_cited,
        "invalid_citations": n_invalid,
        "tool_calls": tool_calls,
        "response_chars": len(answer),
    }
    if settings.log_prompts and r:
        event["query"] = r.query[:2000]
    return event


def _footer_wanted(prep: Prepared, finish_reason: str | None) -> bool:
    return (finish_reason == "stop" and prep.action in FOOTER_ACTIONS and prep.channel == "ide"
            and bool(prep.retrieval and prep.retrieval.hits)
            and get_settings().sources_footer)


def stream(prep: Prepared) -> Iterator[bytes]:
    """Opens the upstream stream first (so HTTP errors surface as errors), then yields SSE."""
    t0 = time.perf_counter()
    resp = _post(prep.payload, stream=True)

    def generate() -> Iterator[bytes]:
        answer: list[str] = []
        usage: dict = {}
        ttft = None
        tool_calls = False
        error = None
        try:
            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                obj["model"] = prep.requested_model
                if obj.get("usage"):
                    usage = obj["usage"]
                for choice in obj.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        if ttft is None:
                            ttft = int((time.perf_counter() - t0) * 1000)
                        answer.append(delta["content"])
                    if delta.get("tool_calls"):
                        tool_calls = True
                        if ttft is None:
                            ttft = int((time.perf_counter() - t0) * 1000)
                    if _footer_wanted(prep, choice.get("finish_reason")):
                        footer = prompting.sources_footer(prep.retrieval.hits)  # not counted as citations
                        extra = {"id": obj.get("id"), "object": "chat.completion.chunk", "created": obj.get("created"),
                                 "model": prep.requested_model,
                                 "choices": [{"index": choice.get("index", 0), "delta": {"content": footer},
                                              "finish_reason": None}]}
                        yield f"data: {json.dumps(extra)}\n\n".encode()
                yield f"data: {json.dumps(obj)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as exc:
            error = f"stream: {exc.__class__.__name__}: {str(exc)[:200]}"
            log.warning("Stream interrupted: %s", exc)
        finally:
            resp.close()
            events.record(_event(prep, answer="".join(answer), usage=usage,
                                 llm_ms=int((time.perf_counter() - t0) * 1000), ttft_ms=ttft,
                                 tool_calls=tool_calls, error=error))

    return generate()


def complete(prep: Prepared, record: bool = True) -> tuple[dict, dict]:
    """Non-streaming call. Returns (openai-shaped response, event)."""
    t0 = time.perf_counter()
    try:
        resp = _post(prep.payload, stream=False)
    except UpstreamError as exc:
        if record:
            events.record(_event(prep, answer="", usage={}, llm_ms=int((time.perf_counter() - t0) * 1000),
                                 ttft_ms=None, tool_calls=False, error=f"openai {exc.status}"))
        raise
    data = resp.json()
    llm_ms = int((time.perf_counter() - t0) * 1000)
    data["model"] = prep.requested_model
    answer, tool_calls = "", False
    for choice in data.get("choices") or []:
        msg = choice.get("message") or {}
        if msg.get("tool_calls"):
            tool_calls = True
        if isinstance(msg.get("content"), str):
            answer += msg["content"]
        if _footer_wanted(prep, choice.get("finish_reason")) and isinstance(msg.get("content"), str):
            msg["content"] += prompting.sources_footer(prep.retrieval.hits)
    event = _event(prep, answer=answer, usage=data.get("usage") or {}, llm_ms=llm_ms, ttft_ms=None,
                   tool_calls=tool_calls, error=None)
    if record:
        events.record(dict(event))
    return data, event
