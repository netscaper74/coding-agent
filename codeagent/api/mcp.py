"""Minimal MCP server (Streamable HTTP transport, stateless, JSON responses).

Exposes the same permission-aware knowledge as read-only tools, so any MCP-capable
editor or agent (Continue agent mode, others) can search the indexed repos.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .. import db as D
from ..users import User
from . import events
from .retrieval import retrieve
from .prompting import source_location

SERVER_INFO = {"name": "repo-knowledge", "version": "1.0.0"}
SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = ("Read-only access to the organisation's indexed GitLab repositories and coding standards. "
                "Results are filtered to what the calling developer is allowed to see. Cite results as repo/path:lines.")

TOOLS = [
    {
        "name": "search_code",
        "description": "Semantic + keyword search over indexed repository code the developer can access. "
                       "Returns the best-matching functions/classes with repo, path and line ranges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you are looking for, in natural language or identifiers"},
                "repo": {"type": "string", "description": "Optional repository name to restrict the search"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "search_standards",
        "description": "Search the approved coding standards and return the most relevant clauses.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 4},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_file",
        "description": "Return the indexed content of a file (optionally a line range) from a repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["repo", "path"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "list_repos",
        "description": "List the indexed repositories the developer can access.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]

MAX_FILE_LINES = 400


class ToolError(Exception):
    pass


def _format_hits(hits: list[dict]) -> str:
    if not hits:
        return "No matching results in the repositories you can access."
    blocks = []
    for h in hits:
        loc = source_location(h)
        if h["kind"] == "standard":
            blocks.append(f"### {loc} — {h.get('title')} (v{h.get('version', '')})\n{h['text']}")
        else:
            blocks.append(f"### {loc} ({h.get('unit')} {h.get('symbol')})\n```{h['language']}\n{h['text']}\n```")
    return "\n\n".join(blocks)


def call_tool(name: str, args: dict, user: User, database=None) -> tuple[str, dict]:
    database = database if database is not None else D.get_db()
    if name == "search_code":
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("query is required")
        repos = [args["repo"]] if args.get("repo") else None
        r = retrieve(query, user, kinds=["code"], repos=repos, k=int(args.get("limit") or 8), database=database)
        return _format_hits(r.hits), {"sources": len(r.hits), "repos": r.repos, "filtered_by_acl": r.filtered_by_acl,
                                      "tokens": r.tokens, "timings": r.timings}
    if name == "search_standards":
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("query is required")
        r = retrieve(query, user, kinds=["standard"], k=int(args.get("limit") or 4), database=database)
        return _format_hits(r.hits), {"sources": len(r.hits), "repos": [], "filtered_by_acl": r.filtered_by_acl,
                                      "tokens": r.tokens, "timings": r.timings}
    if name == "get_file":
        doc = database[D.FILES].find_one({"repo": args.get("repo"), "path": args.get("path"),
                                         "access_scope": {"$in": user.scopes}})
        if not doc or doc.get("content") is None:
            raise ToolError(f"File not found or not accessible: {args.get('repo')}/{args.get('path')}")
        lines = doc["content"].splitlines()
        start = max(1, int(args.get("start_line") or 1))
        end = min(len(lines), int(args.get("end_line") or start + MAX_FILE_LINES - 1), start + MAX_FILE_LINES - 1)
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n… {len(lines) - end} more lines" if end < len(lines) else ""
        header = f"{args['repo']}/{args['path']}:{start}-{end} ({doc.get('language')}, {len(lines)} lines)"
        return f"{header}\n```\n{body}\n```{more}", {"sources": 1, "repos": [args["repo"]], "filtered_by_acl": 0}
    if name == "list_repos":
        rows = list(database[D.FILES].aggregate([
            {"$match": {"kind": "code", "access_scope": {"$in": user.scopes}}},
            {"$group": {"_id": "$repo", "files": {"$sum": 1}, "chunks": {"$sum": "$chunks"},
                        "indexed_at": {"$max": "$indexed_at"}}},
            {"$sort": {"_id": 1}},
        ]))
        if not rows:
            return "No repositories are indexed for your access scopes.", {"sources": 0, "repos": []}
        text = "\n".join(f"- {r['_id']}: {r['files']} files, {r['chunks']} chunks, indexed {r['indexed_at']:%Y-%m-%d %H:%M} UTC"
                         for r in rows)
        return text, {"sources": 0, "repos": [r["_id"] for r in rows]}
    raise ToolError(f"Unknown tool: {name}")


def _result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(message: dict, user: User, ide: str = "unknown", database=None) -> dict | None:
    """Handles one JSON-RPC message. Returns None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(req_id, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method and method.startswith("notifications/"):
        return None
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        started = time.perf_counter()
        meta: dict = {}
        error = None
        try:
            text, meta = call_tool(name, args, user, database)
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except ToolError as exc:
            error = str(exc)
            result = {"content": [{"type": "text", "text": error}], "isError": True}
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            result = {"content": [{"type": "text", "text": f"Tool failed: {error}"}], "isError": True}
        _record(user, name, ide, started, meta, error, database)
        return _result(req_id, result)
    if is_notification:
        return None
    return _error(req_id, -32601, f"Method not found: {method}")


def _record(user: User, tool: str, ide: str, started: float, meta: dict, error: str | None, database) -> None:
    tokens = meta.get("tokens") or {}
    timings = meta.get("timings") or {}
    repos = meta.get("repos") or []
    embed_t, rerank_t = tokens.get("voyage_embed", 0), tokens.get("voyage_rerank", 0)
    events.record({
        "ts": datetime.now(timezone.utc),
        "meta": {"user": user.user, "name": user.name, "team": user.team, "action": f"mcp:{tool}", "repo": repos[0] if repos else None,
                 "ide": ide, "channel": "mcp", "model": None, "synthetic": False},
        "status": "error" if error else "ok",
        "error": error,
        "latency_ms": {"total": int((time.perf_counter() - started) * 1000), "embed": timings.get("embed", 0),
                       "search": timings.get("search", 0), "rerank": timings.get("rerank", 0), "llm": 0, "ttft": None},
        "tokens": {"openai_in": 0, "openai_out": 0, "voyage_embed": embed_t, "voyage_rerank": rerank_t},
        "cost_usd": round(events.voyage_cost(embed_t, rerank_t), 6),
        "cost": {"openai": 0.0, "voyage": round(events.voyage_cost(embed_t, rerank_t), 6)},
        "sources": meta.get("sources", 0),
        "repos": repos,
        "no_context": tool.startswith("search") and meta.get("sources", 0) == 0,
        "filtered_by_acl": meta.get("filtered_by_acl", 0),
        "cited": 0, "invalid_citations": 0, "tool_calls": False, "response_chars": 0,
    }, database)


def handle_body(raw: bytes, user: User, ide: str = "unknown", database=None):
    """Returns (status, payload). Supports single messages and (legacy) batches."""
    try:
        body = json.loads(raw or b"null")
    except json.JSONDecodeError:
        return 400, _error(None, -32700, "Parse error")
    if isinstance(body, list):
        out = [r for r in (handle(m, user, ide, database) for m in body) if r is not None]
        return (200, out) if out else (202, None)
    response = handle(body, user, ide, database)
    return (202, None) if response is None else (200, response)
