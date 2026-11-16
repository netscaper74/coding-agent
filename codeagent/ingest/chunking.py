"""Code-aware chunking.

Each chunk is one meaningful unit (function, method, class, type) with its exact line
range, so answers can cite `path:start-end`. Uses tree-sitter when available, Python's
own `ast` module as a fallback for Python, and line windows for everything else.
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

MAX_CHUNK_LINES = 120   # bigger definitions are split into their members
WINDOW_LINES = 60
WINDOW_OVERLAP = 10
HEADER_LINES = 15       # signature/docstring chunk kept for oversized classes
MIN_MODULE_LINES = 3    # top-level code outside definitions worth indexing

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".java": "java",
    ".go": "go",
    ".cs": "csharp",
    ".sql": "sql",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
}

DEF_TYPES = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition", "generator_function_declaration"},
    "typescript": {"function_declaration", "class_declaration", "method_definition", "interface_declaration",
                   "type_alias_declaration", "enum_declaration", "abstract_class_declaration"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
             "method_declaration", "constructor_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "csharp": {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration",
               "enum_declaration", "method_declaration", "constructor_declaration"},
}
DEF_TYPES["tsx"] = DEF_TYPES["typescript"]

# Nodes we look inside to find definitions (namespaces, class bodies, exports...).
CONTAINER_TYPES = {
    "export_statement", "class_body", "declaration_list", "namespace_declaration",
    "file_scoped_namespace_declaration", "program", "module", "source_file", "compilation_unit",
    "block", "interface_body", "enum_body", "field_declaration_list",
}
# JS/TS `const handler = async () => {...}` at top level is a definition too.
ARROW_HOLDERS = {"lexical_declaration", "variable_declaration"}


@dataclass
class Chunk:
    path: str
    language: str
    symbol: str
    unit: str          # function | class | method | type | module | window
    start_line: int    # 1-based, inclusive
    end_line: int
    text: str


def detect_language(path: str) -> str | None:
    for ext, lang in LANGUAGE_BY_EXT.items():
        if path.lower().endswith(ext):
            return lang
    return None


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def identifier_tokens(text: str, limit: int = 400) -> str:
    """'postLedgerEntry' / 'post_ledger_entry' -> 'post ledger entry'. Helps keyword search on code."""
    words: dict[str, None] = {}
    for ident in _IDENT.findall(text):
        for part in ident.split("_"):
            for w in _CAMEL.findall(part):
                w = w.lower()
                if len(w) > 1:
                    words.setdefault(w, None)
        if len(words) >= limit:
            break
    return " ".join(words)


def chunk_file(path: str, source: str) -> list[Chunk]:
    language = detect_language(path) or "text"
    lines = source.splitlines()
    if not lines:
        return []
    chunks: list[Chunk] | None = None
    if language in DEF_TYPES:
        chunks = _chunk_tree_sitter(path, language, source, lines)
        if chunks is None and language == "python":
            chunks = _chunk_python_ast(path, source, lines)
    if chunks is None:
        return _windows(path, language, lines, 1, len(lines), symbol=_basename(path))
    return chunks + _uncovered_module_code(path, language, lines, chunks)


# --------------------------------------------------------------------------- tree-sitter

def _get_parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
    except ImportError:
        return None
    try:
        return get_parser(language)
    except Exception as exc:  # grammar not bundled
        log.debug("No tree-sitter grammar for %s: %s", language, exc)
        return None


def _chunk_tree_sitter(path: str, language: str, source: str, lines: list[str]) -> list[Chunk] | None:
    parser = _get_parser(language)
    if parser is None:
        return None
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:
        log.warning("tree-sitter failed on %s: %s", path, exc)
        return None
    out: list[Chunk] = []
    try:
        _visit(tree.root_node, language, path, lines, [], out)
    except Exception as exc:  # unexpected grammar shape: fall back rather than lose the file
        log.warning("tree-sitter walk failed on %s: %s", path, exc)
        return None
    return out


def _row(point) -> int:
    """0-based row of a tree-sitter Point (tuple in older bindings, object with .row in newer)."""
    return point.row if hasattr(point, "row") else point[0]


def _node_name(node) -> str | None:
    for field in ("name",):
        child = node.child_by_field_name(field)
        if child is not None:
            return child.text.decode("utf-8", "replace")
    inner = node.child_by_field_name("definition") or node.child_by_field_name("declaration")
    if inner is not None:
        return _node_name(inner)
    for child in node.named_children:
        if child.type in {"type_spec", "variable_declarator"}:
            return _node_name(child)
    return None


def _unit_for(node_type: str, parents: list[str]) -> str:
    if "class" in node_type or "interface" in node_type or "struct" in node_type or "record" in node_type:
        return "class"
    if "type" in node_type or "enum" in node_type:
        return "type"
    return "method" if parents else "function"


def _is_arrow_holder(node) -> bool:
    if node.type not in ARROW_HOLDERS:
        return False
    text = node.text.decode("utf-8", "replace")
    return "=>" in text or "function" in text


def _visit(node, language: str, path: str, lines: list[str], parents: list[str], out: list[Chunk]) -> None:
    defs = DEF_TYPES[language]
    for child in node.named_children:
        is_def = child.type in defs or (not parents and _is_arrow_holder(child))
        if is_def:
            # decorated_definition / export wrappers carry the real definition inside
            inner = child
            if child.type == "decorated_definition":
                inner = child.child_by_field_name("definition") or child
            name = _node_name(child) or f"<anonymous@{_row(child.start_point) + 1}>"
            qualified = ".".join(parents + [name])
            start, end = _row(child.start_point) + 1, _row(child.end_point) + 1
            unit = _unit_for(inner.type, parents)
            if end - start + 1 <= MAX_CHUNK_LINES:
                out.append(_make(path, language, qualified, unit, lines, start, end))
            else:
                before = len(out)
                _visit(inner, language, path, lines, parents + [name], out)
                if len(out) == before:
                    out.extend(_windows(path, language, lines, start, end, qualified))
                else:
                    out.append(_make(path, language, qualified, unit, lines, start,
                                     min(end, start + HEADER_LINES - 1)))
        elif child.type in CONTAINER_TYPES or child.type == "export_statement":
            _visit(child, language, path, lines, parents, out)


# --------------------------------------------------------------------------- python ast fallback

def _chunk_python_ast(path: str, source: str, lines: list[str]) -> list[Chunk] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    out: list[Chunk] = []

    def visit(body, parents: list[str]) -> None:
        for node in body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            end = node.end_lineno or node.lineno
            qualified = ".".join(parents + [node.name])
            unit = "class" if isinstance(node, ast.ClassDef) else ("method" if parents else "function")
            if end - start + 1 <= MAX_CHUNK_LINES:
                out.append(_make(path, "python", qualified, unit, lines, start, end))
            else:
                before = len(out)
                visit(node.body, parents + [node.name])
                if len(out) == before:
                    out.extend(_windows(path, "python", lines, start, end, qualified))
                else:
                    out.append(_make(path, "python", qualified, unit, lines, start,
                                     min(end, start + HEADER_LINES - 1)))

    visit(tree.body, [])
    return out


# --------------------------------------------------------------------------- helpers

def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _make(path, language, symbol, unit, lines, start, end) -> Chunk:
    return Chunk(path=path, language=language, symbol=symbol, unit=unit,
                 start_line=start, end_line=end, text="\n".join(lines[start - 1:end]))


def _windows(path, language, lines, start, end, symbol) -> list[Chunk]:
    out = []
    pos = start
    while pos <= end:
        stop = min(end, pos + WINDOW_LINES - 1)
        text = "\n".join(lines[pos - 1:stop])
        if text.strip():
            out.append(Chunk(path, language, symbol, "window", pos, stop, text))
        if stop == end:
            break
        pos = stop - WINDOW_OVERLAP + 1
    return out


def _uncovered_module_code(path, language, lines, chunks: list[Chunk]) -> list[Chunk]:
    """Imports, constants and config at module level that no definition chunk covers."""
    covered = [False] * (len(lines) + 1)
    for c in chunks:
        for i in range(c.start_line, c.end_line + 1):
            covered[i] = True
    out: list[Chunk] = []
    run_start = None
    for i in range(1, len(lines) + 2):
        free = i <= len(lines) and not covered[i]
        if free and run_start is None:
            run_start = i
        elif not free and run_start is not None:
            segment = lines[run_start - 1:i - 1]
            if sum(1 for line in segment if line.strip()) >= MIN_MODULE_LINES:
                for w in _windows(path, language, lines, run_start, i - 1, _basename(path)):
                    w.unit = "module"
                    out.append(w)
            run_start = None
    return out
