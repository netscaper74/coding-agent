"""agent-api: OpenAI-compatible endpoint for Continue, MCP server, telemetry and admin metrics."""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .. import db as D
from ..indexes import ensure_collections
from ..settings import get_settings
from ..users import User, bearer, get_user, resolve_token
from . import agent, events, mcp, metrics, rollups, telemetry

log = logging.getLogger("agent-api")


class JSON(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, default=str, ensure_ascii=False).encode("utf-8")


def openai_error(status: int, message: str, kind: str = "invalid_request_error") -> JSON:
    return JSON({"error": {"message": message, "type": kind}}, status_code=status)


def detect_ide(request: Request) -> str:
    explicit = request.headers.get("x-client-ide")
    if explicit:
        return explicit.lower()[:30]
    ua = (request.headers.get("user-agent") or "").lower()
    for needle, name in (("jetbrains", "jetbrains"), ("intellij", "jetbrains"), ("pycharm", "jetbrains"),
                         ("vscode", "vscode"), ("visual studio code", "vscode"), ("cursor", "cursor"), ("zed", "zed")):
        if needle in ua:
            return name
    return "unknown"


def authenticate(request: Request) -> User | None:
    return resolve_token(bearer(request.headers))


def unauthorized() -> JSON:
    return openai_error(401, "Missing or invalid API key. Use your personal Repo Agent token.", "authentication_error")


# ------------------------------------------------------------------ OpenAI-compatible

async def models(request: Request):
    if not authenticate(request):
        return unauthorized()
    now = int(time.time())
    return JSON({"object": "list", "data": [
        {"id": m, "object": "model", "created": now, "owned_by": "repo-agent"} for m in agent.MODELS]})


async def chat_completions(request: Request):
    user = authenticate(request)
    if not user:
        return unauthorized()
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return openai_error(400, "Request body must be JSON")
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return openai_error(400, "'messages' is required")
    repos = [r.strip() for r in (request.headers.get("x-repo-agent-repos") or "").split(",") if r.strip()] or None
    prep = await run_in_threadpool(agent.prepare, user, body, channel="ide", ide=detect_ide(request), repos=repos)
    try:
        if prep.stream:
            gen = await run_in_threadpool(agent.stream, prep)
            return StreamingResponse(gen, media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                              "X-Request-Id": prep.request_id})
        data, _ = await run_in_threadpool(agent.complete, prep)
        return JSON(data, headers={"X-Request-Id": prep.request_id})
    except agent.UpstreamError as exc:
        if prep.stream:  # complete() records its own errors; stream() fails before its generator exists
            events.record(agent._event(prep, answer="", usage={}, llm_ms=0, ttft_ms=None, tool_calls=False,
                                       error=f"openai {exc.status}"))
        try:
            payload = json.loads(exc.body)
        except json.JSONDecodeError:
            payload = {"error": {"message": exc.body[:500], "type": "upstream_error"}}
        return JSON(payload, status_code=exc.status)


async def feedback(request: Request):
    user = authenticate(request)
    if not user:
        return unauthorized()
    body = await request.json()
    await run_in_threadpool(telemetry.store_feedback, str(body.get("request_id", "")), body.get("helpful"),
                            body.get("accepted"), user, detect_ide(request), body.get("action"))
    return JSON({"ok": True})


async def continue_events(request: Request):
    user = authenticate(request)
    if not user:
        return unauthorized()
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSON({"ok": False, "error": "body must be JSON"}, status_code=400)
    n = await run_in_threadpool(telemetry.store_continue_events, body, user, detect_ide(request))
    return JSON({"ok": True, "stored": n})


# ------------------------------------------------------------------ MCP

async def mcp_endpoint(request: Request):
    if request.method != "POST":
        # Stateless server: no server-initiated SSE stream, no sessions to delete.
        return Response(status_code=405, headers={"Allow": "POST"})
    user = authenticate(request)
    if not user:
        return JSON({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Unauthorized"}},
                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
    raw = await request.body()
    status, payload = await run_in_threadpool(mcp.handle_body, raw, user, detect_ide(request))
    if payload is None:
        return Response(status_code=status)
    return JSON(payload, status_code=status)


# ------------------------------------------------------------------ admin

def admin(handler):
    async def wrapped(request: Request):
        user = authenticate(request)
        if not user or not user.is_admin:
            return JSON({"error": "admin token required"}, status_code=401 if not user else 403)
        try:
            return await handler(request, user)
        except Exception as exc:
            log.exception("Admin endpoint failed")
            return JSON({"error": f"{exc.__class__.__name__}: {exc}"}, status_code=500)
    return wrapped


def _metric(fn):
    async def handler(request: Request, _user: User):
        p = metrics.Params.from_query(request.query_params)
        return JSON(await run_in_threadpool(fn, D.get_db(), p))
    return admin(handler)


@admin
async def admin_me(request: Request, user: User):
    return JSON({"user": user.user, "name": user.name, "team": user.team, "role": user.role,
                 "model": get_settings().openai_model, "embed_model": get_settings().embed_model,
                 "rerank_model": get_settings().rerank_model or None, "prompt_logging": get_settings().log_prompts})


@admin
async def admin_user_detail(request: Request, _user: User):
    p = metrics.Params.from_query(request.query_params)
    return JSON(await run_in_threadpool(metrics.user_detail, D.get_db(), p, request.path_params["user"]))


@admin
async def admin_audit(request: Request, _user: User):
    return JSON(await run_in_threadpool(metrics.audit, D.get_db(), request.query_params))


@admin
async def admin_refresh(request: Request, _user: User):
    await run_in_threadpool(rollups.refresh, D.get_db(), 24 * get_settings().events_ttl_days)
    return JSON({"ok": True})


@admin
async def admin_playground(request: Request, actor: User):
    body = await request.json()
    as_user = get_user(body.get("as_user") or actor.user)
    question = str(body.get("question") or "").strip()
    if not as_user or not question:
        return JSON({"error": "as_user (known user) and question are required"}, status_code=400)
    repos = [body["repo"]] if body.get("repo") else None
    req = {"model": agent.AGENT_MODEL, "stream": False, "messages": [{"role": "user", "content": question}]}
    if as_user.user != actor.user:
        events.audit(actor.user, "playground_impersonation", {"as_user": as_user.user, "repo": body.get("repo")})

    def run():
        prep = agent.prepare(as_user, req, channel="playground", ide="web", repos=repos)
        data, event = agent.complete(prep)
        answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        hits = prep.retrieval.hits if prep.retrieval else []
        return {
            "answer": answer,
            "request_id": prep.request_id,
            "action": prep.action,
            "as_user": {"user": as_user.user, "team": as_user.team, "scopes": as_user.scopes},
            "sources": [{
                "label": f"S{i}", "kind": h["kind"], "repo": h["repo"], "path": h["path"],
                "start_line": h.get("start_line"), "end_line": h.get("end_line"), "symbol": h.get("symbol"),
                "clause": h.get("clause"), "title": h.get("title"), "language": h.get("language"),
                "score": h.get("rerank", h.get("rrf")), "web_url": h.get("web_url"), "preview": h["text"][:900],
            } for i, h in enumerate(hits, start=1)],
            "filtered_by_acl": prep.retrieval.filtered_by_acl if prep.retrieval else 0,
            "retrieval_error": prep.retrieval_error,
            "latency_ms": event["latency_ms"], "tokens": event["tokens"], "cost_usd": event["cost_usd"],
            "cited": event["cited"], "invalid_citations": event["invalid_citations"],
        }

    try:
        return JSON(await run_in_threadpool(run))
    except agent.UpstreamError as exc:
        return JSON({"error": f"OpenAI error {exc.status}: {exc.body[:300]}"}, status_code=502)


_eval_lock = threading.Lock()


@admin
async def admin_eval(request: Request, _user: User):
    from ..eval.runner import run_eval

    if not _eval_lock.acquire(blocking=False):
        return JSON({"ok": False, "error": "an eval run is already in progress"}, status_code=409)

    def job():
        try:
            run_eval()
        except Exception:
            log.exception("Eval run failed")
        finally:
            _eval_lock.release()

    threading.Thread(target=job, daemon=True).start()
    return JSON({"ok": True, "started": True}, status_code=202)


async def health(request: Request):
    return JSON({"ok": True})


@contextlib.asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s agent-api %(levelname)s %(message)s")
    try:
        await run_in_threadpool(ensure_collections)
    except Exception as exc:
        log.error("Could not prepare Atlas collections (check MONGODB_URI / network access list): %s", exc)
    rollups.start_background()
    yield


routes = [
    Route("/health", health),
    Route("/v1/models", models),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/v1/feedback", feedback, methods=["POST"]),
    Route("/continue/events", continue_events, methods=["POST"]),
    Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
    Route("/admin/me", admin_me),
    Route("/admin/overview", _metric(metrics.overview)),
    Route("/admin/users", _metric(metrics.users)),
    Route("/admin/users/{user}", admin_user_detail),
    Route("/admin/quality", _metric(metrics.quality)),
    Route("/admin/performance", _metric(metrics.performance)),
    Route("/admin/cost", _metric(metrics.cost)),
    Route("/admin/knowledge", _metric(metrics.knowledge)),
    Route("/admin/audit", admin_audit),
    Route("/admin/playground", admin_playground, methods=["POST"]),
    Route("/admin/rollups/refresh", admin_refresh, methods=["POST"]),
    Route("/admin/eval/run", admin_eval, methods=["POST"]),
]

app = Starlette(
    routes=routes,
    lifespan=lifespan,
    middleware=[Middleware(CORSMiddleware, allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
                           allow_methods=["*"], allow_headers=["*"])],
)
