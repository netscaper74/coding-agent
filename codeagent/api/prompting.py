"""Builds the grounded prompt: editor context (from Continue) + retrieved code + standards."""
from __future__ import annotations

import re

SYSTEM_RULES = """You are Repo Agent, a coding assistant grounded in this organisation's private repositories and coding standards.

How to work:
- Use the developer's editor context (their messages, open files, selections) and the RETRIEVED CONTEXT below.
- Cite every claim about existing code or standards with its source label and location, e.g. [S2 src/ledger/post.py:41-88] or [S5 ACME-SCS 4.2]. Only cite labels that appear in the retrieved context.
- If the retrieved context does not contain what is needed, say so plainly and suggest where to look. Never invent files, functions, APIs or clauses.
- When you propose a change: keep it minimal, show it as a fenced code block (or unified diff) with the file path, follow the coding-standard clauses that apply and name them.
- You have read-only access. You never modify, run or commit anything yourself; the developer reviews and applies every change.
- Be concise and practical."""

NO_CONTEXT_NOTE = ("RETRIEVED CONTEXT: none of the repositories this developer can access matched the request. "
                   "Answer from the editor context only and say that no repository sources were found.")

EDIT_MARKERS = (
    "rewrite", "rewritten", "the user has requested a section of code",
    "apply the following", "merge the changes", "<code_to_edit>",
)


def message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in (None, "text"))
    return ""


def last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return message_text(m)
    return ""


_CODE_BLOCK = re.compile(r"```.*?```", re.S)


def retrieval_query(messages: list[dict]) -> str:
    """The developer's question, plus a bounded slice of any code they selected."""
    text = last_user_text(messages)
    prose = _CODE_BLOCK.sub(" ", text).strip()
    code = "\n".join(_CODE_BLOCK.findall(text))[:1500]
    query = (prose[:2000] + ("\n" + code if code else "")).strip()
    return query or text[:3000]


_TESTS = re.compile(r"\b(unit[- ]?tests?|tests?|pytest|jest|junit|spec)\b", re.I)
_EXPLAIN = re.compile(r"^\s*(explain|what|where|why|how|which|who|describe|summari[sz]e|walk me)\b|\bexplain\b", re.I)
_EDIT = re.compile(r"\b(refactor|rewrite|fix|change|implement|add|update|rename|convert|optimi[sz]e|migrate|improve)\b", re.I)


def classify(messages: list[dict], model: str) -> str:
    if model.endswith("-apply") or model.endswith("-raw"):
        return "apply"
    text = last_user_text(messages)
    lowered = text.lower()
    if any(marker in lowered for marker in EDIT_MARKERS):
        return "edit"
    head = _CODE_BLOCK.sub(" ", text)[:600]
    if _TESTS.search(head):
        return "tests"
    if _EXPLAIN.search(head):
        return "explain"
    if _EDIT.search(head):
        return "edit"
    return "chat"


def _label(i: int) -> str:
    return f"S{i}"


def source_location(hit: dict) -> str:
    if hit["kind"] == "standard":
        return f"{hit['repo']} {hit.get('clause')}"
    return f"{hit['repo']}/{hit['path']}:{hit['start_line']}-{hit['end_line']}"


def render_context(hits: list[dict], max_chars: int) -> str:
    parts = ["RETRIEVED CONTEXT (read-only; cite by label):"]
    used = len(parts[0])
    for i, h in enumerate(hits, start=1):
        if h["kind"] == "standard":
            header = (f"[{_label(i)}] Coding standard {h['repo']} v{h.get('version', '')} — clause {h.get('clause')}: "
                      f"{h.get('title')}")
            body = h["text"]
        else:
            header = (f"[{_label(i)}] {h['repo']}/{h['path']}:{h['start_line']}-{h['end_line']} "
                      f"({h['language']}, {h.get('unit')} {h.get('symbol')}, branch {h.get('branch')})")
            body = f"```{h['language']}\n{h['text']}\n```"
        block = f"{header}\n{body}"
        if used + len(block) > max_chars:
            room = max_chars - used - len(header) - 40
            if room < 400:
                break
            block = f"{header}\n{body[:room]}\n…(truncated)"
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def augment(messages: list[dict], hits: list[dict], max_chars: int) -> list[dict]:
    """Keeps the client's own system messages first, then adds our rules and context."""
    context = render_context(hits, max_chars) if hits else NO_CONTEXT_NOTE
    ours = {"role": "system", "content": f"{SYSTEM_RULES}\n\n{context}"}
    out = list(messages)
    insert_at = 0
    while insert_at < len(out) and out[insert_at].get("role") in ("system", "developer"):
        insert_at += 1
    out.insert(insert_at, ours)
    return out


_CITE = re.compile(r"\[S(\d+)\b")


def citation_stats(answer: str, n_sources: int) -> tuple[int, int, set[int]]:
    """Returns (distinct cited labels, invalid labels, set of valid label numbers)."""
    cited = {int(m) for m in _CITE.findall(answer)}
    valid = {c for c in cited if 1 <= c <= n_sources}
    return len(cited), len(cited - valid), valid


def sources_footer(hits: list[dict]) -> str:
    lines = ["", "", "---", "**Sources**"]
    for i, h in enumerate(hits, start=1):
        loc = source_location(h)
        url = h.get("web_url")
        lines.append(f"- [{_label(i)}] [{loc}]({url})" if url else f"- [{_label(i)}] `{loc}`")
    return "\n".join(lines)
