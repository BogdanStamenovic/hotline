"""Reading a session's own transcript to recover what it just said.

The socket is inject-only: nothing comes back on it. The reply has to be read out
of `~/.claude/projects/<mangled-cwd>/<session-id>.jsonl`, which is append-only, so
"what was said since I injected" is exactly "the bytes after the offset I recorded
before injecting".

The one trap is sidechains. Subagent turns are written into the same file with
`isSidechain: true`. Reading the last assistant message without filtering those out
means that any turn which spawned a subagent gets that subagent's internal report
spoken back instead of the answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import projects_dir


@dataclass
class Turn:
    """What a session produced after a given point in its transcript."""

    text: str = ""
    tools: list[str] = field(default_factory=list)
    saw_marker: bool = False
    offset: int = 0


def transcript_path(session_id: str) -> Path | None:
    hits = sorted(projects_dir().glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def size_of(session_id: str) -> int:
    path = transcript_path(session_id)
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()


def _tools_of(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    return [
        str(b.get("name"))
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")
    ]


def read_since(session_id: str, offset: int, marker: str | None = None) -> Turn:
    """Everything the session said after `offset`.

    `marker` is the text we injected. Requiring it to appear before we accept an
    assistant message is what makes this safe against a session that was already
    mid-turn when we injected: the stop event that fires first belongs to the
    turn already in flight, and its reply is not ours.
    """
    path = transcript_path(session_id)
    turn = Turn(offset=offset)
    if path is None:
        return turn
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
            turn.offset = fh.tell()
    except OSError:
        return turn

    marker_seen = marker is None
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if obj.get("isSidechain"):
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        if obj.get("type") == "user" and not marker_seen:
            if marker is not None and marker.strip() in _text_of(msg.get("content")):
                marker_seen = True
            continue
        if obj.get("type") != "assistant" or not marker_seen:
            continue
        # parent_tool_use_id set means this assistant block belongs to a nested
        # tool-driven turn, not the top-level answer.
        if obj.get("parent_tool_use_id"):
            continue
        text = _text_of(msg.get("content"))
        if text:
            turn.text = text
        turn.tools.extend(_tools_of(msg.get("content")))
    turn.saw_marker = marker_seen
    return turn
