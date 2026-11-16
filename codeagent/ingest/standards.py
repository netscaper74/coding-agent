"""Chunks a coding-standards document (Markdown) into one chunk per clause/section."""
from __future__ import annotations

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# Recognises "SCS-4.2 Title", "4.2 Title", "[SEC-01] Title"
CLAUSE_ID = re.compile(r"^\[?([A-Z]{2,10}-\d+(?:\.\d+)*|\d+(?:\.\d+)+)\]?[\s:.-]+(.*)$")
MAX_SECTION_CHARS = 6000


@dataclass
class Section:
    clause: str
    title: str
    breadcrumb: str
    start_line: int
    end_line: int
    text: str


def chunk_standard(markdown: str) -> list[Section]:
    lines = markdown.splitlines()
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    current: dict | None = None

    def flush(end_line: int) -> None:
        if current is None:
            return
        body = "\n".join(lines[current["start"] - 1:end_line]).strip()
        if len(body.splitlines()) <= 1:  # heading with no content (e.g. a chapter title)
            return
        for offset, piece in enumerate(_split(body)):
            sections.append(Section(
                clause=current["clause"] + (f"#{offset + 1}" if offset else ""),
                title=current["title"],
                breadcrumb=current["breadcrumb"],
                start_line=current["start"],
                end_line=end_line,
                text=piece,
            ))

    for i, line in enumerate(lines, start=1):
        match = HEADING.match(line)
        if not match:
            continue
        flush(i - 1)
        level, heading = len(match.group(1)), match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        clause_match = CLAUSE_ID.match(heading)
        clause = clause_match.group(1) if clause_match else heading
        title = clause_match.group(2).strip() if clause_match else heading
        current = {"start": i, "clause": clause, "title": title,
                   "breadcrumb": " > ".join(h for _, h in stack)}
    flush(len(lines))
    return sections


def _split(text: str) -> list[str]:
    if len(text) <= MAX_SECTION_CHARS:
        return [text]
    parts, buf = [], ""
    for para in text.split("\n\n"):
        if buf and len(buf) + len(para) > MAX_SECTION_CHARS:
            parts.append(buf.strip())
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts
