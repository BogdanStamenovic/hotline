"""A stand-in that answers for a session which is too busy to answer for itself.

`tofix.md` #2 and #5, which are the same mechanism seen from two sides. Send a
message to a session that is mid-turn and it lands in that session's inbox and
sits there -- correct, and indistinguishable from the message having gone
nowhere. From the sender's side there is only silence, and silence is the one
answer a messaging system must never give.

So when the target is busy we do three things instead of waiting:

    1. inject the message anyway, so it is genuinely queued  -- that is the receipt
    2. spawn a short-lived agent that looks at the busy session and reports
    3. keep watching in the background, and relay the real answer when it lands

The stand-in gets the pane and the transcript tail as evidence rather than tools,
and runs single-turn. Latency is the entire product here: an accurate report that
takes ninety seconds is worth less than a good one in five, because the thing it
is competing with is the sender staring at nothing. It is also why this is the one
place hotline pins a smaller model.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

from .ccsocks import LiveSession
from .config import CLAUDE_BIN
from .transcript import transcript_path

STANDIN_MODEL = "sonnet"
STANDIN_TIMEOUT = 75.0

# Long enough to show what the session is in the middle of, short enough not to
# turn the stand-in's prompt into the whole conversation.
PANE_LINES = 60
TRANSCRIPT_BYTES = 12000

PROMPT = """\
You are standing in for a Claude Code session that is currently mid-turn and \
cannot answer for itself. Someone messaged it and is waiting.

Your job is to tell them what is going on, right now, in at most four sentences \
of plain spoken prose. No markdown, no headings, no bullet points -- this may be \
read aloud over a phone call.

Cover, in this order, only what you can actually support from the evidence:
  - what the session appears to be doing at this moment
  - whether it looks alive and progressing, or wedged (stuck on a prompt, no \
output for a long time, crashed, waiting on input nobody is going to give it)
  - the answer to the sender's question, if the evidence supports one

Do not guess. If the evidence does not say, say that it does not say. Never \
claim the work is finished -- you cannot see the future, and the sender will be \
told separately when the real answer arrives.

One thing the pane will mislead you about: messages reach this session over a \
socket, not by typing, so a menu or prompt on screen does NOT mean it is stuck. \
The descriptor status is the authority on whether it is working. Treat the pane \
as evidence of WHAT it is doing, and the status and transcript timing as the \
evidence of WHETHER it is doing anything.

SESSION: {name} (pid {pid}), working in {cwd}
DESCRIPTOR STATUS: {status}
LAST TRANSCRIPT ACTIVITY: {idle}

--- WHAT ITS TERMINAL IS SHOWING ---
{pane}

--- THE TAIL OF ITS TRANSCRIPT ---
{tail}

--- WHAT THE SENDER SAID ---
{question}
"""


@dataclass
class Standing:
    """What the stand-in concluded, plus the receipt that the message was queued."""

    text: str
    delivered: bool
    wedged_hint: bool = False

    def spoken(self, name: str) -> str:
        receipt = (
            f"Your message is queued for {name}."
            if self.delivered
            else f"I could not hand that to {name}."
        )
        return f"{receipt} {self.text}".strip()


def _transcript_tail(session: LiveSession, limit: int = TRANSCRIPT_BYTES) -> tuple[str, str]:
    """The readable end of a transcript, and how long since it last grew.

    Rendered down to `role: what happened` lines rather than passed as raw JSONL.
    The raw form is mostly ids and token counts, and spending the stand-in's
    context on those buys nothing.
    """
    path = transcript_path(session.session_id)
    if path is None:
        return "(no transcript on disk)", "unknown"
    try:
        stat = path.stat()
        with path.open("rb") as fh:
            fh.seek(max(0, stat.st_size - limit))
            raw = fh.read()
    except OSError:
        return "(transcript unreadable)", "unknown"

    idle = f"{time.time() - stat.st_mtime:.0f}s ago"
    lines: list[str] = []
    for chunk in raw.split(b"\n")[1:]:  # first line is probably a partial record
        if not chunk.strip():
            continue
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        role = obj.get("type", "?")
        content = message.get("content")
        if isinstance(content, str):
            lines.append(f"{role}: {content.strip()[:400]}")
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text", "").strip():
                lines.append(f"{role}: {block['text'].strip()[:400]}")
            elif block.get("type") == "tool_use":
                detail = json.dumps(block.get("input") or {})[:160]
                lines.append(f"{role}: [tool {block.get('name')}] {detail}")
    return "\n".join(lines[-40:]) or "(nothing readable)", idle


async def report(
    session: LiveSession,
    question: str,
    delivered: bool,
    timeout: float = STANDIN_TIMEOUT,
) -> Standing:
    """Ask a throwaway agent what the busy session is up to."""
    from . import tmuxen

    pane = tmuxen.capture(session.tmux, PANE_LINES) if session.tmux else ""
    tail, idle = _transcript_tail(session)
    prompt = PROMPT.format(
        name=session.name,
        pid=session.pid,
        cwd=session.cwd,
        status=session.status or "unknown",
        idle=idle,
        pane=pane or "(not running in tmux, so there is no pane to look at)",
        tail=tail,
        question=question,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN,
            "-p",
            prompt,
            "--model",
            STANDIN_MODEL,
            # No tools: everything it needs is in the prompt, and a stand-in that
            # goes exploring is a stand-in that arrives after the real answer.
            "--disallowed-tools",
            "Bash",
            "Read",
            "Edit",
            "Write",
            "Glob",
            "Grep",
            "Task",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, OSError, ValueError) as exc:
        return Standing(
            text=_fallback(session, idle) + f" (the stand-in itself failed: {exc})",
            delivered=delivered,
        )

    text = out.decode(errors="replace").strip()
    if not text:
        # A stand-in that says nothing is a stand-in that failed. Its stderr is
        # the only evidence of why, and dropping it here would make this the
        # second thing in hotline to fail invisibly.
        why = err.decode(errors="replace").strip().splitlines()
        detail = f" (stand-in said nothing; stderr: {why[-1]})" if why else ""
        return Standing(text=_fallback(session, idle) + detail, delivered=delivered)
    return Standing(text=text, delivered=delivered, wedged_hint="wedged" in text.lower())


def _fallback(session: LiveSession, idle: str) -> str:
    """What we can say without an agent at all.

    The stand-in is a subprocess and subprocesses fail. When it does, the sender
    still gets the facts we already had in hand rather than an apology.
    """
    where = f" in tmux session {session.tmux_session}" if session.tmux_session else ""
    return (
        f"{session.name} is {session.status or 'in an unknown state'}{where}; "
        f"its transcript last changed {idle}."
    )
