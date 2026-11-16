"""Incremental ingest: sources -> chunks -> Voyage embeddings -> Atlas."""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import db as D
from .. import voyage
from ..settings import get_settings
from .chunking import Chunk, chunk_file, identifier_tokens
from .sources import RepoSource, build_source
from .standards import chunk_standard

log = logging.getLogger(__name__)

DEFAULT_EXTENSIONS = [".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".cs", ".sql", ".md", ".yaml", ".yml"]
EMBED_FLUSH = 128


@dataclass
class RepoStats:
    name: str
    kind: str
    commit: str = ""
    files_seen: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    chunks_written: int = 0
    embed_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


def embed_header(doc: dict) -> str:
    if doc["kind"] == "standard":
        return (f"Coding standard: {doc['repo']} v{doc.get('version', '')}\n"
                f"Clause {doc['clause']}: {doc['title']}\nSection: {doc.get('breadcrumb', '')}\n\n{doc['text']}")
    return (f"Repository: {doc['repo']}\nFile: {doc['path']}\n"
            f"Symbol: {doc['symbol']} ({doc['unit']}, {doc['language']})\n\n{doc['text']}")


class Writer:
    """Buffers chunk documents, embeds them in batches and writes per file."""

    def __init__(self, database, stats: RepoStats):
        self.db, self.stats = database, stats
        self.pending: list[tuple[str, str, str, list[dict], str | None]] = []

    def add_file(self, repo: str, path: str, fingerprint: str, docs: list[dict], content: str | None = None) -> None:
        """`content` (code files only) is kept so the agent's get_file tool can return exact lines."""
        self.pending.append((repo, path, fingerprint, docs, content))
        if sum(len(p[3]) for p in self.pending) >= EMBED_FLUSH:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        docs = [d for _, _, _, file_docs, _ in self.pending for d in file_docs]
        if docs:
            result = voyage.embed([embed_header(d) for d in docs], input_type="document")
            for doc, vector in zip(docs, result.vectors):
                doc["embedding"] = vector
            self.stats.embed_tokens += result.tokens
        now = datetime.now(timezone.utc)
        for repo, path, fingerprint, file_docs, content in self.pending:
            self.db[D.CHUNKS].delete_many({"repo": repo, "path": path})
            if file_docs:
                self.db[D.CHUNKS].insert_many(file_docs)
            self.db[D.FILES].update_one(
                {"repo": repo, "path": path},
                {"$set": {"fingerprint": fingerprint, "chunks": len(file_docs), "indexed_at": now,
                          "content": content, "language": file_docs[0]["language"] if file_docs else None,
                          "kind": file_docs[0]["kind"] if file_docs else None,
                          "access_scope": file_docs[0]["access_scope"] if file_docs else None}},
                upsert=True,
            )
            self.stats.chunks_written += len(file_docs)
        self.pending.clear()


def _code_docs(source: RepoSource, repo_cfg: dict, chunks: list[Chunk]) -> list[dict]:
    now = datetime.now(timezone.utc)
    docs = []
    for n, c in enumerate(chunks):
        docs.append({
            "_id": f"{source.name}@{source.branch}:{c.path}#{c.start_line}-{c.end_line}:{n}",
            "kind": "code",
            "repo": source.name,
            "branch": source.branch,
            "commit": source.commit,
            "path": c.path,
            "language": c.language,
            "symbol": c.symbol,
            "unit": c.unit,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "text": c.text,
            "ident_tokens": identifier_tokens(c.symbol + "\n" + c.text),
            "access_scope": list(repo_cfg.get("access_scope", ["public"])),
            "web_url": source.web_url(c.path, c.start_line, c.end_line),
            "indexed_at": now,
        })
    return docs


def ingest_repo(database, repo_cfg: dict, settings, extensions: set[str], max_bytes: int) -> RepoStats:
    stats = RepoStats(name=repo_cfg["name"], kind="code")
    started = time.time()
    try:
        source = build_source(repo_cfg, settings, extensions, max_bytes)
    except Exception as exc:
        stats.errors.append(f"source: {exc}")
        log.error("Repo %s: %s", repo_cfg.get("name"), exc)
        return stats
    stats.commit = source.commit
    known = {d["path"]: d.get("fingerprint") for d in database[D.FILES].find({"repo": source.name}, {"path": 1, "fingerprint": 1})}
    writer = Writer(database, stats)
    seen: set[str] = set()
    scope = list(repo_cfg.get("access_scope", ["public"]))
    for f in source.list_files():
        seen.add(f.path)
        stats.files_seen += 1
        if known.get(f.path) == f.fingerprint:
            continue
        try:
            raw = source.read(f)
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            stats.errors.append(f"{f.path}: {exc}")
            continue
        chunks = chunk_file(f.path, text)
        writer.add_file(source.name, f.path, f.fingerprint, _code_docs(source, repo_cfg, chunks), text)
        stats.files_changed += 1
    writer.flush()
    # Access scope may have changed in config: apply it to all chunks of the repo.
    for coll in (D.CHUNKS, D.FILES):
        database[coll].update_many({"repo": source.name, "access_scope": {"$ne": scope}},
                                   {"$set": {"access_scope": scope}})
    removed = [p for p in known if p not in seen]
    if removed:
        database[D.CHUNKS].delete_many({"repo": source.name, "path": {"$in": removed}})
        database[D.FILES].delete_many({"repo": source.name, "path": {"$in": removed}})
        stats.files_deleted = len(removed)
    stats.seconds = round(time.time() - started, 2)
    return stats


def ingest_standard(database, std_cfg: dict) -> RepoStats:
    std_id = std_cfg.get("id") or std_cfg["name"]
    stats = RepoStats(name=std_id, kind="standard")
    started = time.time()
    path = Path(std_cfg["path"])
    if not path.exists():
        stats.errors.append(f"standard file not found: {path}")
        return stats
    data = path.read_bytes()
    fingerprint = hashlib.sha1(data + str(std_cfg.get("version", "")).encode()).hexdigest()
    stats.files_seen = 1
    stats.commit = std_cfg.get("version", "")
    existing = database[D.FILES].find_one({"repo": std_id, "path": path.name})
    if existing and existing.get("fingerprint") == fingerprint:
        stats.seconds = round(time.time() - started, 2)
        return stats
    now = datetime.now(timezone.utc)
    docs = []
    for n, s in enumerate(chunk_standard(data.decode("utf-8"))):
        docs.append({
            "_id": f"{std_id}:{s.clause}:{n}",
            "kind": "standard",
            "repo": std_id,
            "branch": "",
            "commit": "",
            "version": std_cfg.get("version", ""),
            "standard_name": std_cfg.get("name", std_id),
            "path": path.name,
            "language": "markdown",
            "symbol": s.clause,
            "clause": s.clause,
            "title": s.title,
            "breadcrumb": s.breadcrumb,
            "unit": "clause",
            "start_line": s.start_line,
            "end_line": s.end_line,
            "text": s.text,
            "ident_tokens": identifier_tokens(s.text),
            "access_scope": list(std_cfg.get("access_scope", ["public"])),
            "web_url": std_cfg.get("url"),
            "indexed_at": now,
        })
    writer = Writer(database, stats)
    writer.add_file(std_id, path.name, fingerprint, docs)
    writer.flush()
    stats.files_changed = 1
    stats.seconds = round(time.time() - started, 2)
    return stats


def run_once(database=None) -> dict:
    settings = get_settings()
    database = database if database is not None else D.get_db()
    cfg = settings.yaml("sources.yaml")
    extensions = set(cfg.get("include_extensions") or DEFAULT_EXTENSIONS)
    max_bytes = int(cfg.get("max_file_bytes", 200_000))
    run = {"started_at": datetime.now(timezone.utc), "repos": []}
    for repo_cfg in cfg.get("repos", []):
        stats = ingest_repo(database, repo_cfg, settings, extensions, max_bytes)
        log.info("Repo %-20s files=%d changed=%d deleted=%d chunks=%d tokens=%d errors=%d (%.1fs)",
                 stats.name, stats.files_seen, stats.files_changed, stats.files_deleted,
                 stats.chunks_written, stats.embed_tokens, len(stats.errors), stats.seconds)
        run["repos"].append(stats.__dict__)
    for std_cfg in cfg.get("standards", []):
        stats = ingest_standard(database, std_cfg)
        log.info("Standard %-16s changed=%d chunks=%d tokens=%d errors=%d",
                 stats.name, stats.files_changed, stats.chunks_written, stats.embed_tokens, len(stats.errors))
        run["repos"].append(stats.__dict__)
    run["finished_at"] = datetime.now(timezone.utc)
    run["embed_model"] = settings.embed_model
    run["embed_tokens"] = sum(r["embed_tokens"] for r in run["repos"])
    run["ok"] = not any(r["errors"] for r in run["repos"])
    database[D.INGEST_RUNS].insert_one(dict(run))
    return run
