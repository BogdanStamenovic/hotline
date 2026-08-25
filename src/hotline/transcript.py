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
from typing import Any

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


# ---------------------------------------------------------------------------
# Structured tailing, for anything that wants the whole shape of a turn rather
# than just its final sentence.
#
# `read_since` answers one question -- "what did it say after I injected" -- and
# answers it well. Reconstructing a map of what a session is *doing* needs the
# steps too: which prompt opened the turn, which tools ran under it, which of
# those a subagent ran, and where a compaction cut the history in half. That is
# a different projection of the same bytes, so it reuses the same offset
# discipline and the same `_is_real_user_turn` / sidechain filtering rather than
# becoming a second parser with its own idea of what a turn is.
# ---------------------------------------------------------------------------

MAX_SLICE_BYTES = 8 << 20
"""Ceiling on one read. A tool result can be megabytes and several can land
between two nudges, so this is generous; what it actually stops is a first
contact with a week-old transcript pulling the whole thing into memory at once.
A truncated slice ends on a line boundary and the next read continues."""

# Records the CLI writes into the user's slot on the user's behalf. They pass
# `_is_real_user_turn` -- they are `type: "user"` and carry no tool_result -- but
# treating them as turn boundaries would open a phase for every skill load,
# every slash command's echo and every subagent status line. Matched on the
# opening of the text because that is the only stable marker they carry: the
# flags around them (`isMeta`, `promptSource`) are set inconsistently across the
# same shapes, verified by dumping 700 user records on this machine.
SYNTHETIC_USER_PREFIXES = (
    "<task-notification>",
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<user-prompt-submit-hook>",
    "[Request interrupted",
    "Base directory for this skill:",
    "Caveat: The messages below were generated by the user",
)


@dataclass(frozen=True)
class TranscriptEvent:
    """One thing that happened, in the order the file records it.

    `kind` is one of:

    * `"user"`   -- a real user turn, the thing that opens a phase
    * `"assistant"` -- assistant prose, the thing a phase's outcome comes from
    * `"tool"`   -- one `tool_use` block
    * `"compact"` -- a `compact_boundary` system record, with real numbers
    """

    kind: str
    text: str = ""
    tool: str | None = None
    tool_use_id: str | None = None
    parent_tool_use_id: str | None = None
    is_sidechain: bool = False
    at: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Slice:
    """What one read produced, and how much it is worth believing.

    The counts are the point. `read_since`'s `except ValueError: continue` is
    right for its job -- one unreadable line must not cost a reply -- and wrong
    as the sole source of a map, where the same silence reads as "the agent did
    nothing". A caller that persists this is expected to check `trustworthy`
    before advancing its stored offset, so that a file it has stopped being able
    to read is re-read rather than skipped past.
    """

    events: list[TranscriptEvent] = field(default_factory=list)
    offset: int = 0
    sidechains: dict[str, int] = field(default_factory=dict)
    lines: int = 0
    unparseable: int = 0
    recognised: int = 0
    truncated: bool = False
    # A single record longer than MAX_SLICE_BYTES. Advancing past it loses that
    # record; not advancing stalls the session forever. The caller is told so it
    # can say which it did.
    overlong: bool = False

    @property
    def trustworthy(self) -> bool:
        """Did this read produce anything the parser recognised at all?

        Recognised means "a JSON object with a `type`", deliberately not "a
        record type we know about": the CLI adds new ones often enough that
        keying on a known-types list would raise a false alarm every release.
        What this catches is the real failure -- a file that has stopped being
        newline-delimited JSON, or an offset that has landed mid-record."""
        return self.lines == 0 or self.recognised > 0


def _epoch(obj: dict) -> float | None:
    """The record's own timestamp, in epoch seconds.

    ISO-8601 with a trailing `Z`, which `fromisoformat` only learned to parse in
    3.11; this package requires 3.12, so the swap is safe."""
    raw = obj.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _is_synthetic_user_turn(obj: dict) -> bool:
    """A user record the CLI wrote rather than a person."""
    if obj.get("isCompactSummary") or obj.get("isVisibleInTranscriptOnly"):
        return True
    text = _text_of((obj.get("message") or {}).get("content")).lstrip()
    return text.startswith(SYNTHETIC_USER_PREFIXES)


def _read_slice(path: Path, offset: int, max_bytes: int) -> tuple[bytes, int, bool, bool]:
    """Bytes from `offset` up to the last COMPLETE line. `(data, offset, truncated, overlong)`.

    The line-boundary trim is what makes this safe to run against a file being
    appended to. `read_since` uses `fh.tell()` and can therefore land mid-record;
    it gets away with it because it re-reads the same tail every time and is
    looking for a marker. An incremental reader that stores its offset cannot:
    half a line consumed is a record lost for good.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(max_bytes)
    except OSError:
        return b"", offset, False, False
    if not data:
        return b"", offset, False, False
    truncated = len(data) >= max_bytes
    cut = data.rfind(b"\n")
    if cut < 0:
        # One record longer than the whole budget. Advancing loses it; not
        # advancing stops this session's map forever. Say so and move on.
        if truncated:
            return data, offset + len(data), truncated, True
        # Not at the budget: this is simply a line still being written.
        return b"", offset, False, False
    return data[: cut + 1], offset + cut + 1, truncated, False


def _events_of(obj: dict) -> list[TranscriptEvent]:
    """Everything one transcript record contributes. Usually nothing."""
    kind = obj.get("type")
    at = _epoch(obj)
    sidechain = bool(obj.get("isSidechain"))
    parent = obj.get("parent_tool_use_id")

    if kind == "system" and obj.get("subtype") == "compact_boundary":
        meta = obj.get("compactMetadata")
        meta = meta if isinstance(meta, dict) else {}
        return [TranscriptEvent(
            kind="compact",
            text=str(obj.get("content") or "Conversation compacted"),
            at=at,
            is_sidechain=sidechain,
            detail={
                "trigger": meta.get("trigger"),
                "pre_tokens": meta.get("preTokens"),
                "post_tokens": meta.get("postTokens"),
                "dropped_tokens": meta.get("cumulativeDroppedTokens"),
                "duration_ms": meta.get("durationMs"),
            },
        )]

    message = obj.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")

    if kind == "user":
        # Reusing the existing predicate rather than re-deriving it: a tool
        # result is written as `type: "user"` too, and counting those as turns
        # is exactly the bug `_is_real_user_turn` already exists to avoid.
        if not _is_real_user_turn(obj) or _is_synthetic_user_turn(obj):
            return []
        text = _text_of(content)
        return [TranscriptEvent(kind="user", text=text, at=at, is_sidechain=sidechain,
                                parent_tool_use_id=parent)] if text else []

    if kind != "assistant" or not isinstance(content, list):
        return []
    out: list[TranscriptEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and block.get("name"):
            arguments = block.get("input")
            out.append(TranscriptEvent(
                kind="tool",
                tool=str(block.get("name")),
                tool_use_id=block.get("id"),
                parent_tool_use_id=parent,
                is_sidechain=sidechain,
                at=at,
                detail=arguments if isinstance(arguments, dict) else {},
            ))
        elif block.get("type") == "text" and str(block.get("text") or "").strip():
            out.append(TranscriptEvent(
                kind="assistant",
                text=str(block["text"]).strip(),
                parent_tool_use_id=parent,
                is_sidechain=sidechain,
                at=at,
            ))
    return out


def _scan(data: bytes, into: Slice, *, sidechain: bool) -> None:
    """Parse one blob of complete lines into `into`, counting what it could not."""
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        into.lines += 1
        try:
            obj = json.loads(raw)
        except ValueError:
            into.unparseable += 1
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            into.unparseable += 1
            continue
        into.recognised += 1
        for event in _events_of(obj):
            into.events.append(
                event if not sidechain
                else TranscriptEvent(
                    kind=event.kind, text=event.text, tool=event.tool,
                    tool_use_id=event.tool_use_id,
                    parent_tool_use_id=event.parent_tool_use_id,
                    is_sidechain=True, at=event.at, detail=event.detail,
                )
            )


def sidechain_paths(session_id: str) -> list[Path]:
    """The separate files a session's subagents write into.

    Measured on Claude Code 2.1.241: sidechain turns are NOT inline in the main
    transcript any more. Every record in it reads `isSidechain: false`, and each
    subagent gets `<projects>/<mangled-cwd>/<session-id>/subagents/agent-*.jsonl`
    instead, carrying `isSidechain: true` and the PARENT's `sessionId`.

    The inline handling above is kept regardless, because it costs nothing and
    an older CLI -- or a session started before an upgrade -- still writes that
    shape. This function is what makes subagent tool calls visible on the
    version actually installed.
    """
    path = transcript_path(session_id)
    if path is None:
        return []
    directory = path.parent / session_id / "subagents"
    try:
        return sorted(directory.glob("*.jsonl"))
    except OSError:
        return []


def events_since(
    session_id: str,
    offset: int,
    *,
    sidechains: dict[str, int] | None = None,
    max_bytes: int = MAX_SLICE_BYTES,
) -> Slice:
    """Everything structured that happened after `offset`, plus the new offset.

    `sidechains` maps a subagent file's name to the offset already consumed from
    it, and the returned `Slice.sidechains` is the map to store back. It is a
    separate dict rather than a second integer because there is one file per
    subagent and they are appended to concurrently with the main transcript.

    Events are ordered by their own timestamps, so a subagent's tool calls land
    between the parent's rather than in a block at the end. Python's sort is
    stable, so records sharing a timestamp keep file order, main transcript
    first.
    """
    result = Slice(offset=offset, sidechains=dict(sidechains or {}))
    path = transcript_path(session_id)
    if path is None:
        return result

    data, new_offset, truncated, overlong = _read_slice(path, offset, max_bytes)
    result.offset = new_offset
    result.truncated = truncated
    result.overlong = overlong
    _scan(data, result, sidechain=False)

    for extra in sidechain_paths(session_id):
        name = extra.name
        seen = int(result.sidechains.get(name, 0))
        chunk, moved, cut, over = _read_slice(extra, seen, max_bytes)
        result.sidechains[name] = moved
        result.truncated = result.truncated or cut
        result.overlong = result.overlong or over
        _scan(chunk, result, sidechain=True)

    result.events.sort(key=lambda event: event.at if event.at is not None else 0.0)
    return result
