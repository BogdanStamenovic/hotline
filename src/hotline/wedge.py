"""Is a live session actually consuming what it is sent?

The watchdog's health test used to be `agent.session_id in live` -- the session
descriptor exists, so the worker is fine. On 2026-09-19 that read healthy for 31
minutes while the operator was a corpse: it stopped producing turns at 19:14:46,
ten seconds into its life, and Bogdan's instruction was relayed into it twice
(19:19:57 and 19:35:34) and never read. `hotline --list` said "waiting", the
watchdog checked every five minutes and saw a live pid, and he eventually power
cycled the box to get an operator that would answer him.

Liveness is not health. A process can hold its socket open forever without ever
taking another turn, and that is the failure this module exists to name.

**The signal.** Claude Code records the message queue in the transcript:

    {"type":"queue-operation","operation":"enqueue","timestamp":...,"content":...}
    {"type":"queue-operation","operation":"remove", "timestamp":...,"content":...}

An `enqueue` with no later `remove` of the same text is a message the session was
handed and has not picked up. That is a fact about the session's own behaviour,
not a status field describing it -- which is the whole point, because every
status field in this project has at some stage lied.

**Why a pending message alone is not enough.** A healthy session absorbs a queued
message at its next turn boundary, so one that is mid-way through a twenty-minute
build legitimately leaves it sitting there. Killing that session would destroy
real work to fix nothing. So a wedge requires both:

  * a message pending longer than `stale_after`, and
  * no assistant turn written since it arrived.

The second is the decisive one. A session that is merely busy keeps appending to
its transcript; the wedged one appended nothing at all after 17:14:46. Together
they are specific enough to act on automatically.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .transcript import transcript_path

# The watchdog runs every five minutes. Ten gives a session two passes to absorb
# a message on its own before anything is called wrong, which keeps a slow tool
# call from being mistaken for a wedge.
STALE_AFTER = 600.0


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class Verdict:
    """What the transcript says about a session's appetite for its queue."""

    wedged: bool
    pending: int = 0
    # Seconds the oldest unconsumed message has been waiting.
    waiting: float = 0.0
    # When that message was handed over. Callers that want to recognise "this
    # same wedge again" key on this rather than recomputing it from `waiting`,
    # which drifts by a second depending on when they ask.
    oldest: float = 0.0
    # Why not wedged, when it is not -- so a caller can log something truer than
    # a bare False.
    reason: str = ""

    def __bool__(self) -> bool:
        return self.wedged


def scan(path: Path, *, now: float | None = None, stale_after: float = STALE_AFTER) -> Verdict:
    """Read one transcript and decide whether its session has stopped answering.

    Tolerates a partial final line: the file is appended to while we read it, so
    a truncated last record is normal and means "nothing yet", not "corrupt".
    """
    now = time.time() if now is None else now

    # Queue entries are matched by their text. Two identical messages queued back
    # to back are indistinguishable, which is fine -- a `remove` then clears the
    # older one and the survivor still reports as pending.
    pending: dict[str, list[float]] = {}
    last_assistant = 0.0

    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return Verdict(False, reason="transcript unreadable")

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue

        kind = obj.get("type")
        when = _epoch(obj.get("timestamp"))

        if kind == "assistant" and when:
            last_assistant = max(last_assistant, when)
        elif kind == "queue-operation":
            content = obj.get("content")
            if not isinstance(content, str):
                continue
            if obj.get("operation") == "enqueue":
                pending.setdefault(content, []).append(when or now)
            elif obj.get("operation") == "remove":
                queued = pending.get(content)
                if queued:
                    queued.pop(0)
                    if not queued:
                        pending.pop(content, None)

    oldest = min((t for times in pending.values() for t in times), default=None)
    if oldest is None:
        return Verdict(False, reason="queue empty")

    waiting = now - oldest
    count = sum(len(times) for times in pending.values())

    if waiting < stale_after:
        return Verdict(False, count, waiting, oldest, reason=f"queued {waiting:.0f}s ago, still fresh")
    if last_assistant > oldest:
        # It answered *after* the message landed, so it is taking turns; the
        # message is queued behind a turn in flight rather than stranded.
        return Verdict(False, count, waiting, oldest, reason="answered since the message arrived")

    return Verdict(True, count, waiting, oldest, reason=f"{count} message(s) unread for {waiting / 60:.0f} min")


def check(session_id: str, *, now: float | None = None, stale_after: float = STALE_AFTER) -> Verdict:
    """The same verdict, for a session id whose transcript we have to find first."""
    path = transcript_path(session_id)
    if path is None:
        return Verdict(False, reason="no transcript on disk")
    return scan(path, now=now, stale_after=stale_after)
