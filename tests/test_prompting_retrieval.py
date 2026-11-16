from codeagent.api import prompting, retrieval
from codeagent.users import get_user, resolve_token


def hit(i, kind="code", repo="payments-svc", path="src/a.py", clause=None):
    return {"_id": f"id{i}", "kind": kind, "repo": repo, "path": path, "start_line": 1, "end_line": 9,
            "language": "python", "symbol": f"f{i}", "unit": "function", "branch": "main",
            "text": f"def f{i}(): pass", "clause": clause, "title": "T", "version": "1", "score": 1.0 - i / 100}


def test_users_and_scopes():
    alice = resolve_token("dev-alice-7f3k2")
    assert alice.user == "alice" and "group:finance" in alice.scopes and "public" in alice.scopes
    assert resolve_token("nope") is None
    assert get_user("admin").is_admin


def test_classify():
    m = lambda t: [{"role": "user", "content": t}]
    assert prompting.classify(m("Explain how posting works"), "repo-agent") == "explain"
    assert prompting.classify(m("Where is the FX rate loaded?"), "repo-agent") == "explain"
    assert prompting.classify(m("Write unit tests for validate_entry"), "repo-agent") == "tests"
    assert prompting.classify(m("Refactor this to use Decimal"), "repo-agent") == "edit"
    assert prompting.classify(m("hello"), "repo-agent") == "chat"
    assert prompting.classify(m("anything"), "repo-agent-apply") == "apply"
    parts = [{"role": "user", "content": [{"type": "text", "text": "explain this"}]}]
    assert prompting.classify(parts, "repo-agent") == "explain"


def test_retrieval_query_keeps_prose_and_bounded_code():
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "Why does this fail?\n```python\n" + "x = 1\n" * 1000 + "```"}]
    q = prompting.retrieval_query(msgs)
    assert q.startswith("Why does this fail?")
    assert len(q) < 2000 + 1600


def test_augment_keeps_client_system_first_and_labels_sources():
    msgs = [{"role": "system", "content": "continue system"}, {"role": "user", "content": "q"}]
    out = prompting.augment(msgs, [hit(1), hit(2, kind="standard", repo="ACME-SCS", clause="SCS-4.1")], 10000)
    assert out[0]["content"] == "continue system"
    assert out[1]["role"] == "system" and "[S1] payments-svc/src/a.py:1-9" in out[1]["content"]
    assert "[S2] Coding standard ACME-SCS v1 — clause SCS-4.1" in out[1]["content"]
    assert out[2] == msgs[1]
    empty = prompting.augment(msgs, [], 10000)
    assert "none of the repositories" in empty[1]["content"]


def test_context_is_truncated_to_budget():
    big = hit(1)
    big["text"] = "x" * 50000
    ctx = prompting.render_context([big, hit(2)], 5000)
    assert len(ctx) < 5200


def test_citation_stats():
    n, invalid, valid = prompting.citation_stats("See [S1 a.py:1-2] and [S3] and [S9 x]", 3)
    assert (n, invalid, valid) == (3, 1, {1, 3})


def test_rrf_and_final_selection():
    vec = [hit(1), hit(2), hit(3)]
    txt = [hit(3), hit(4, kind="standard", repo="ACME-SCS", clause="SCS-5.1")]
    fused = retrieval.rrf_fuse(vec, txt)
    assert fused[0]["_id"] == "id3"  # in both lists
    final = retrieval.select_final(fused, 2)
    assert len(final) == 2
    assert any(h["kind"] == "standard" for h in final)  # at least one standard kept


def test_retrieve_applies_permission_filters(monkeypatch, fake_db):
    from codeagent import voyage

    monkeypatch.setattr(voyage, "embed", lambda texts, input_type: voyage.EmbedResult([[0.1] * 4], 7))
    monkeypatch.setattr(voyage, "rerank", lambda q, docs, top_k: ([(i, 1 - i / 10) for i in range(len(docs))], 11))
    seen = []

    def handler(name, pipeline):
        seen.append(pipeline[0])
        stage = pipeline[0]
        if "$vectorSearch" in stage and "$nin" in str(stage["$vectorSearch"]["filter"]):
            return [{"_id": "hidden1", "score": 0.99}]
        if "$vectorSearch" in stage:
            return [hit(1), hit(2)]
        return [hit(2), hit(5, kind="standard", repo="ACME-SCS", clause="SCS-4.1")]

    fake_db.aggregate_handler = handler
    bob = get_user("bob")
    r = retrieval.retrieve("how are balances computed", bob, database=fake_db, k=3)
    vec_stage = next(s for s in seen if "$vectorSearch" in s and "$and" in s["$vectorSearch"]["filter"])
    assert vec_stage["$vectorSearch"]["filter"]["$and"][0] == {"access_scope": {"$in": bob.scopes}}
    text_stage = next(s for s in seen if "$search" in s)
    assert {"in": {"path": "access_scope", "value": bob.scopes}} in text_stage["$search"]["compound"]["filter"]
    hidden_stage = next(s for s in seen if "$vectorSearch" in s and "$nin" in str(s["$vectorSearch"]["filter"]))
    assert hidden_stage["$vectorSearch"]["filter"] == {"access_scope": {"$nin": bob.scopes}}
    # id2 is in both the vector and text results, so fusion ranks it first; standard kept
    assert [h["_id"] for h in r.hits] == ["id2", "id1", "id5"]
    assert r.filtered_by_acl == 1
    assert r.tokens == {"voyage_embed": 7, "voyage_rerank": 11}
