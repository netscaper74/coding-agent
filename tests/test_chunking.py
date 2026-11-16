from pathlib import Path

from codeagent.ingest import chunking
from codeagent.ingest.chunking import chunk_file, identifier_tokens
from codeagent.ingest.standards import chunk_standard

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample-repos"


def test_python_chunks_are_functions_with_exact_lines():
    path = "src/ledger/validation.py"
    source = (SAMPLES / "payments-svc" / path).read_text()
    chunks = chunk_file(path, source)
    by_symbol = {c.symbol: c for c in chunks}
    assert {"validate_entry", "_validate_amount"} <= set(by_symbol)
    c = by_symbol["validate_entry"]
    lines = source.splitlines()
    assert lines[c.start_line - 1].startswith("def validate_entry")
    assert c.text == "\n".join(lines[c.start_line - 1:c.end_line])
    # module-level imports/constants are kept as a module chunk
    assert any(ch.unit == "module" and "MAX_AMOUNT" in ch.text for ch in chunks)


def test_python_methods_are_qualified_when_class_is_large(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_CHUNK_LINES", 10)
    source = (SAMPLES / "payments-svc" / "src/ledger/posting.py").read_text()
    symbols = {c.symbol for c in chunk_file("src/ledger/posting.py", source)}
    assert "PostingService.post_entry" in symbols
    assert "PostingService.reverse_entry" in symbols
    assert "PostingService" in symbols  # header chunk


def test_unparsed_languages_fall_back_to_windows():
    source = "\n".join(f"line {i}" for i in range(150))
    chunks = chunk_file("db/big.sql", source)
    assert chunks[0].start_line == 1 and chunks[0].end_line == chunking.WINDOW_LINES
    assert chunks[1].start_line == chunking.WINDOW_LINES - chunking.WINDOW_OVERLAP + 1
    assert chunks[-1].end_line == 150


def test_all_sample_files_chunk_without_errors():
    for repo in SAMPLES.iterdir():
        for f in repo.rglob("*"):
            if f.is_file():
                rel = f.relative_to(repo).as_posix()
                chunks = chunk_file(rel, f.read_text())
                assert chunks, rel
                for c in chunks:
                    assert 1 <= c.start_line <= c.end_line


def test_identifier_tokens_split_camel_and_snake():
    tokens = identifier_tokens("def postLedgerEntry(idempotency_key): HTTPServer")
    for word in ("post", "ledger", "entry", "idempotency", "key", "http", "server"):
        assert word in tokens.split()


def test_standard_sections_have_clause_ids():
    sections = chunk_standard((ROOT / "standards/acme-secure-coding.md").read_text())
    clauses = {s.clause: s for s in sections}
    assert {"SCS-4.1", "SCS-5.1", "SCS-8.1"} <= set(clauses)
    assert clauses["SCS-4.1"].title == "Never use binary floating point for money"
    assert "SCS-4 Money and numeric handling" in clauses["SCS-4.1"].breadcrumb
    # chapter headings without body are not emitted
    assert "SCS-4" not in clauses
