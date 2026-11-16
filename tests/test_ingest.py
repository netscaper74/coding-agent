from pathlib import Path

from codeagent import voyage
from codeagent.ingest import pipeline
from codeagent.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
EXT = set(pipeline.DEFAULT_EXTENSIONS)


def _stub_embed(monkeypatch):
    calls = []

    def embed(texts, input_type):
        calls.append((len(texts), input_type))
        return voyage.EmbedResult([[0.0] * 4 for _ in texts], 3 * len(texts))

    monkeypatch.setattr(voyage, "embed", embed)
    return calls


def test_repo_ingest_is_incremental(monkeypatch, fake_db, tmp_path):
    calls = _stub_embed(monkeypatch)
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/a.py").write_text("def a():\n    return 1\n")
    (repo / "src/b.py").write_text("def b():\n    return 2\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules/x.js").write_text("function x() {}")
    cfg = {"name": "demo", "source": "local", "path": str(repo), "branch": "main", "access_scope": ["group:x"]}

    stats = pipeline.ingest_repo(fake_db, cfg, get_settings(), EXT, 200_000)
    assert (stats.files_seen, stats.files_changed, stats.errors) == (2, 2, [])
    chunks = fake_db["chunks"].docs
    assert {c["symbol"] for c in chunks} == {"a", "b"}
    assert all(c["access_scope"] == ["group:x"] and c["embedding"] for c in chunks)
    assert calls[-1][1] == "document"
    assert fake_db["files"].find_one({"path": "src/a.py"})["content"].startswith("def a")

    # unchanged -> nothing re-embedded; changed + deleted files handled
    n_calls = len(calls)
    stats = pipeline.ingest_repo(fake_db, cfg, get_settings(), EXT, 200_000)
    assert stats.files_changed == 0 and len(calls) == n_calls
    (repo / "src/a.py").write_text("def a2():\n    return 3\n")
    (repo / "src/b.py").unlink()
    stats = pipeline.ingest_repo(fake_db, cfg, get_settings(), EXT, 200_000)
    assert (stats.files_changed, stats.files_deleted) == (1, 1)
    assert {c["symbol"] for c in fake_db["chunks"].docs} == {"a2"}

    # scope change in config is applied without re-embedding
    cfg["access_scope"] = ["group:y"]
    pipeline.ingest_repo(fake_db, cfg, get_settings(), EXT, 200_000)
    assert all(c["access_scope"] == ["group:y"] for c in fake_db["chunks"].docs)


def test_sample_repos_and_standard(monkeypatch, fake_db):
    _stub_embed(monkeypatch)
    for name in ("payments-svc", "web-portal"):
        cfg = {"name": name, "source": "local", "path": str(ROOT / "sample-repos" / name), "access_scope": ["public"]}
        stats = pipeline.ingest_repo(fake_db, cfg, get_settings(), EXT, 200_000)
        assert stats.chunks_written > 0 and not stats.errors
    std = pipeline.ingest_standard(fake_db, {"id": "ACME-SCS", "version": "2026.1",
                                             "path": str(ROOT / "standards/acme-secure-coding.md")})
    assert std.chunks_written >= 15
    clauses = {c["clause"] for c in fake_db["chunks"].docs if c["kind"] == "standard"}
    assert "SCS-5.1" in clauses
    syms = {(c["repo"], c["symbol"]) for c in fake_db["chunks"].docs}
    assert ("payments-svc", "find_by_reference") in syms or ("payments-svc", "LedgerRepository") in syms
    assert ("payments-svc", "daily_interest") in syms
