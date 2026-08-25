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
    # True when the last thing in this slice is a step rather than an answer --
    # an outstanding tool call. Without it a waiter cannot tell "here is the
    # answer" from "let me check that", and returns the opening sentence of a
    # turn that has barely started.
    in_flight: bool = False


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
    # Indices within this slice only. Evaluating over the whole tail would let a
    # tool call from *before* the injection keep the turn looking unfinished
    # forever.
    last_answer = last_tool = -1
    for index, raw in enumerate(data.split(b"\n")):
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
        content = msg.get("content")
        tools = _tools_of(content)
        text = _text_of(content)
        if text:
            turn.text = text
        # Checked in this order because a record carrying a tool call is a step,
        # not an answer. The CLI never puts both in one record (0 of 665 measured
        # on this machine), so in practice these are disjoint.
        if tools:
            last_tool = index
        elif text:
            last_answer = index
        turn.tools.extend(tools)
    turn.saw_marker = marker_seen
    turn.in_flight = last_tool > last_answer
    return turn


def _is_real_user_turn(obj: dict) -> bool:
    """A user record that is a person talking, not a tool handing back a result.

    Tool results are written with `type: "user"` too. Counting them as user turns
    would make every tool call look like a new unanswered question.
    """
    if obj.get("type") != "user":
        return False
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def turn_in_flight(session_id: str, tail_bytes: int = 200_000) -> bool:
    """Is this session part-way through answering something?

    Two other signals were tried and both were wrong. The descriptor's `status`
    field never changes for a tmux-spawned session -- measured `waiting` at boot
    and still `waiting` through a full twenty-five second tool call. And "wrote
    recently with no stop since" fails because the Stop hook fires *before* the
    turn's final transcript write, so it calls every session busy for as long as
    its window lasts.

    The transcript is unambiguous: a turn is finished only when the last thing in
    it is an assistant message with text. An unanswered question, or an
    outstanding tool call, both mean the session is still working.
    """
    path = transcript_path(session_id)
    if path is None:
        return False
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - tail_bytes))
            data = fh.read()
    except OSError:
        return False

    last_ask = last_answer = last_tool = -1
    for index, raw in enumerate(data.split(b"\n")):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if obj.get("isSidechain") or obj.get("parent_tool_use_id"):
            continue
        if _is_real_user_turn(obj):
            last_ask = index
            continue
        if obj.get("type") != "assistant":
            continue
        content = (obj.get("message") or {}).get("content")
        # A record carrying a tool call is a step, not an answer -- a model that
        # says "let me check that" and then calls a tool has answered nothing yet.
        # Measured over 665 assistant records on this machine, the CLI never puts
        # text and a tool call in the same record, so this is not throwing
        # anything away; the ordering is checked this way round so that a record
        # containing both is still treated as unfinished.
        if _tools_of(content):
            last_tool = index
        elif _text_of(content):
            last_answer = index
    return last_ask > last_answer or last_tool > last_answer
