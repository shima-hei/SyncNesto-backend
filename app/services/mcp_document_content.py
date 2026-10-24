"""版を固定した文書の必要部分だけを返し、原文照合付き編集を組み立てる。"""

import hashlib
import re

from app.core import error_messages as messages
from app.core.exceptions import BadRequestError, ConflictError
from app.schemas.mcp_development import DocumentTextEdit, ProjectDocumentContentInput

ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def body_metadata(body: str) -> dict:
    """転送する本文とは独立して、全体の長さとdigestを返す。"""
    return {
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "offset_unit": "unicode_code_point",
    }


def headings(body: str) -> list[dict]:
    """fence外のATX見出しと、子見出しを含む節の終端を求める。"""
    result, offset = [], 0
    fence = None
    for line in body.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        marker = FENCE.match(raw)
        if fence is not None:
            char, size = fence
            if (
                marker
                and marker[1][0] == char
                and len(marker[1]) >= size
                and not marker[2].strip()
            ):
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        elif match := ATX.match(raw):
            title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
            result.append(
                {
                    "level": len(match[1]),
                    "title": title[:200],
                    "title_truncated": len(title) > 200,
                    "start": offset,
                }
            )
        offset += len(line)
    following = {}
    for row in reversed(result):
        row["end"] = min(
            (following[level] for level in following if level <= row["level"]),
            default=len(body),
        )
        following[row["level"]] = row["start"]
    return result


def read_content(body: str, data: ProjectDocumentContentInput) -> dict:
    """明示的な継続offsetを返し、黙って全文取得へ戻さない。"""
    if data.offset > len(body):
        raise BadRequestError(messages.MCP_INVALID_INPUT)
    result = {**body_metadata(body), "mode": data.mode}
    if data.mode == "outline":
        rows = [h for h in headings(body) if h["start"] >= data.offset]
        return {
            **result,
            "format": "markdown_atx",
            "headings": rows[:50],
            "next_offset": rows[50]["start"] if len(rows) > 50 else None,
        }
    if data.mode == "text":
        end = min(data.offset + data.length, len(body))
        return {
            **result,
            "start": data.offset,
            "end": end,
            "text": body[data.offset : end],
            "next_offset": end if end < len(body) else None,
        }
    query = data.query
    assert query is not None
    matches, position = [], data.offset
    while (start := body.find(query, position)) != -1:
        if len(matches) == 20:
            return {**result, "matches": matches, "next_offset": start}
        end = start + len(query)
        left, right = max(0, start - 80), min(len(body), end + 80)
        matches.append(
            {
                "start": start,
                "end": end,
                "excerpt_start": left,
                "excerpt_end": right,
                "text": body[left:right],
            }
        )
        position = end
    return {**result, "matches": matches, "next_offset": None}


def edited_body(body: str, edits: list[DocumentTextEdit]) -> str:
    """元の版の範囲を一括照合してから末尾側より反映する。"""
    ordered = sorted(edits, key=lambda e: (e.start, e.end))
    previous = None
    for edit in ordered:
        if previous is not None and (
            previous.end > edit.start or previous.start == edit.start
        ):
            raise BadRequestError(messages.MCP_INVALID_INPUT)
        if edit.end > len(body) or body[edit.start : edit.end] != edit.expected_text:
            raise ConflictError(messages.MCP_STALE_QUOTE)
        previous = edit
    for edit in reversed(ordered):
        body = body[: edit.start] + edit.replacement + body[edit.end :]
    if len(body) > 100_000:
        raise BadRequestError(messages.MCP_INVALID_INPUT)
    return body
