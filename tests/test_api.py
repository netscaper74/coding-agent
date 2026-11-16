"""End-to-end tests of agent-api against a fake OpenAI server (Atlas and Voyage are stubbed)."""
import json
import socket
import threading
import time

import pytest
import requests
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

ALICE = {"Authorization": "Bearer dev-alice-7f3k2"}
ADMIN = {"Authorization": "Bearer admin-demo-4x8p6"}
UPSTREAM = {"calls": [], "fail": None}


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def fake_openai(request: Request):
    body = await request.json()
    UPSTREAM["calls"].append(body)
    if UPSTREAM["fail"]:
        return JSONResponse({"error": {"message": "rate limited", "type": "rate_limit"}}, status_code=UPSTREAM["fail"])
    text = "Entries are validated in validate_entry [S1 src/ledger/validation.py:14-40]."
    if body.get("stream"):
        def gen():
            base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": body["model"]}
            yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
            for piece in (text[:20], text[20:]):
                yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {'content': piece}, 'finish_reason': None}]})}\n\n"
            yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield f"data: {json.dumps({**base, 'choices': [], 'usage': {'prompt_tokens': 1200, 'completion_tokens': 80}})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse({"id": "c2", "object": "chat.completion", "created": 1, "model": body["model"],
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 50}})


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/health", timeout=0.2)
            return server
        except requests.RequestException:
            time.sleep(0.05)
    raise RuntimeError("server did not start")


def H(code="x", path="src/ledger/validation.py", kind="code"):
    return {"_id": f"{path}#{code}", "kind": kind, "repo": "payments-svc" if kind == "code" else "ACME-SCS",
            "path": path, "start_line": 14, "end_line": 40, "language": "python", "symbol": "validate_entry",
            "unit": "function", "branch": "main", "text": "def validate_entry(entry): ...", "clause": "SCS-4.3",
            "title": "Double-entry invariants", "version": "2026.1", "web_url": None}


@pytest.fixture(scope="module")
def servers():
    import os
    up_port, api_port = _port(), _port()
    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{up_port}/v1"
    up_app = Starlette(routes=[Route("/v1/chat/completions", fake_openai, methods=["POST"]),
                               Route("/health", lambda r: JSONResponse({}))])
    _serve(up_app, up_port)
    from codeagent.api.app import app
    _serve(app, api_port)
    yield f"http://127.0.0.1:{api_port}"


@pytest.fixture
def stubs(monkeypatch, fake_db, servers):
    from codeagent import db as D
    from codeagent.api import agent, events, mcp, retrieval

    recorded = []
    calls = {"retrieve": 0}

    def fake_retrieve(query, user, **kw):
        calls["retrieve"] += 1
        return retrieval.Retrieval([H("a"), H("b", kind="standard")],
                                   {"embed": 5, "search": 9, "rerank": 4}, {"voyage_embed": 10, "voyage_rerank": 200}, 2, query)

    monkeypatch.setattr(agent, "retrieve_cached", lambda q, u, **kw: (fake_retrieve(q, u, **kw), False))
    monkeypatch.setattr(mcp, "retrieve", fake_retrieve)
    monkeypatch.setattr(events, "record", lambda e, database=None: recorded.append(e))
    monkeypatch.setattr(events, "audit", lambda *a, **k: recorded.append({"audit": a}))
    monkeypatch.setattr(D, "get_db", lambda: fake_db)
    UPSTREAM["calls"].clear()
    UPSTREAM["fail"] = None
    return {"events": recorded, "calls": calls, "db": fake_db}


def sse(resp):
    out = []
    for line in resp.iter_lines(decode_unicode=True):
        if line.startswith("data: "):
            out.append(line[6:])
    return out


def test_auth_and_models(servers, stubs):
    assert requests.get(f"{servers}/v1/models").status_code == 401
    r = requests.get(f"{servers}/v1/models", headers=ALICE)
    assert [m["id"] for m in r.json()["data"]] == ["repo-agent", "repo-agent-apply"]


def test_streaming_chat_is_grounded_and_logged(servers, stubs):
    body = {"model": "repo-agent", "stream": True, "max_tokens": 900, "temperature": 0.2,
            "messages": [{"role": "system", "content": "continue-system"},
                         {"role": "user", "content": "Explain where ledger entries are validated"}]}
    r = requests.post(f"{servers}/v1/chat/completions", json=body, headers={**ALICE, "X-Client-IDE": "vscode"}, stream=True)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    chunks = sse(r)
    assert chunks[-1] == "[DONE]"
    parsed = [json.loads(c) for c in chunks[:-1]]
    assert all(p["model"] == "repo-agent" for p in parsed)
    content = "".join(ch["delta"].get("content", "") for p in parsed for ch in p["choices"])
    assert "validate_entry [S1" in content and "**Sources**" in content
    # footer comes before the finish chunk
    finish_idx = next(i for i, p in enumerate(parsed) if p["choices"] and p["choices"][0]["finish_reason"] == "stop")
    assert "**Sources**" in parsed[finish_idx - 1]["choices"][0]["delta"]["content"]

    up = UPSTREAM["calls"][-1]
    assert up["model"] == "gpt-5-mini" and up["stream_options"] == {"include_usage": True}
    assert "temperature" not in up and up["max_completion_tokens"] == 900 and "max_tokens" not in up
    assert up["messages"][0]["content"] == "continue-system"
    assert "[S1] payments-svc/src/ledger/validation.py:14-40" in up["messages"][1]["content"]

    time.sleep(0.1)
    ev = stubs["events"][-1]
    assert ev["meta"]["user"] == "alice" and ev["meta"]["action"] == "explain" and ev["meta"]["ide"] == "vscode"
    assert ev["sources"] == 2 and ev["cited"] == 1 and ev["invalid_citations"] == 0
    assert ev["tokens"]["openai_in"] == 1200 and ev["tokens"]["voyage_rerank"] == 200
    assert ev["filtered_by_acl"] == 2 and ev["cost_usd"] > 0 and ev["status"] == "ok"


def test_edit_requests_get_no_footer_and_apply_skips_retrieval(servers, stubs):
    edit = {"model": "repo-agent", "messages": [{"role": "user", "content": "Refactor daily_interest to use Decimal"}]}
    r = requests.post(f"{servers}/v1/chat/completions", json=edit, headers=ALICE)
    assert r.status_code == 200 and "**Sources**" not in r.json()["choices"][0]["message"]["content"]
    assert r.json()["model"] == "repo-agent"
    before = stubs["calls"]["retrieve"]
    apply = {"model": "repo-agent-apply", "messages": [{"role": "user", "content": "merge this"}]}
    r = requests.post(f"{servers}/v1/chat/completions", json=apply, headers=ALICE)
    assert r.status_code == 200 and stubs["calls"]["retrieve"] == before
    assert len(UPSTREAM["calls"][-1]["messages"]) == 1  # untouched
    time.sleep(0.1)
    assert stubs["events"][-1]["meta"]["action"] == "apply"


def test_upstream_errors_are_passed_through(servers, stubs):
    UPSTREAM["fail"] = 429
    body = {"model": "repo-agent", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
    r = requests.post(f"{servers}/v1/chat/completions", json=body, headers=ALICE)
    assert r.status_code == 429 and r.json()["error"]["message"] == "rate limited"
    time.sleep(0.1)
    assert stubs["events"][-1]["status"] == "error"


def test_mcp_protocol(servers, stubs):
    url = f"{servers}/mcp"
    assert requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).status_code == 401
    init = requests.post(url, headers=ALICE, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                   "params": {"protocolVersion": "2025-03-26", "capabilities": {}}}).json()
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    assert requests.post(url, headers=ALICE, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    tools = requests.post(url, headers=ALICE, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
    assert {t["name"] for t in tools["result"]["tools"]} == {"search_code", "search_standards", "get_file", "list_repos"}
    res = requests.post(url, headers=ALICE, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                                  "params": {"name": "search_code", "arguments": {"query": "validate"}}}).json()
    assert res["result"]["isError"] is False and "payments-svc/src/ledger/validation.py:14-40" in res["result"]["content"][0]["text"]

    stubs["db"]["files"].docs.append({"repo": "payments-svc", "path": "a.py", "access_scope": ["group:finance"],
                                      "content": "l1\nl2\nl3", "language": "python"})
    res = requests.post(url, headers=ALICE, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                                  "params": {"name": "get_file", "arguments": {"repo": "payments-svc", "path": "a.py", "start_line": 2}}}).json()
    assert "    2  l2" in res["result"]["content"][0]["text"]
    bob = {"Authorization": "Bearer dev-bob-9q1m8"}
    res = requests.post(url, headers=bob, json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                                "params": {"name": "get_file", "arguments": {"repo": "payments-svc", "path": "a.py"}}}).json()
    assert res["result"]["isError"] is True  # bob has no finance scope
    unknown = requests.post(url, headers=ALICE, json={"jsonrpc": "2.0", "id": 6, "method": "nope"}).json()
    assert unknown["error"]["code"] == -32601
    assert requests.get(url, headers=ALICE).status_code == 405


def test_continue_events_and_feedback(servers, stubs):
    payload = [{"name": "editOutcome", "data": {"accepted": True, "modelTitle": "Repo Agent", "codeToEdit": "SECRET"}},
               {"eventName": "chatFeedback", "feedback": False}]
    r = requests.post(f"{servers}/continue/events", json=payload, headers=ALICE)
    assert r.json() == {"ok": True, "stored": 2}
    docs = stubs["db"]["outcomes"].docs
    assert docs[0]["event"] == "editOutcome" and docs[0]["accepted"] is True and "codeToEdit" not in docs[0]["attrs"]
    assert docs[1]["event"] == "chatFeedback" and docs[1]["feedback"] is False
    r = requests.post(f"{servers}/v1/feedback", json={"request_id": "ra-1", "helpful": True}, headers=ALICE)
    assert r.json()["ok"] and docs[-1]["feedback"] is True


def test_admin_requires_admin_and_playground_works(servers, stubs):
    assert requests.get(f"{servers}/admin/me", headers=ALICE).status_code == 403
    assert requests.get(f"{servers}/admin/me", headers=ADMIN).json()["role"] == "admin"
    r = requests.post(f"{servers}/admin/playground", headers=ADMIN,
                      json={"as_user": "bob", "question": "Where are entries validated?"})
    data = r.json()
    assert r.status_code == 200, data
    assert data["as_user"]["user"] == "bob" and data["sources"][0]["label"] == "S1"
    assert data["cited"] == 1 and "validate_entry" in data["answer"]
    assert any("audit" in e for e in stubs["events"])  # impersonation audited
