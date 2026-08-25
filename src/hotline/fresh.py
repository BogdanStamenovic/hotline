"""A persistent `claude` subprocess driven over stream-json.

**Scope note.** This used to be how every hotline conversation ran. It is now
used by the one-shot CLI alone: a process on the end of a pipe cannot also be a
terminal you can attach to, and that is what conversations need, so they moved to
`hotline.tmuxen`. A single `hotline "what is X"` genuinely does not want a tmux
pane left behind, which is why this stayed rather than being deleted.

    claude --input-format stream-json --output-format stream-json --verbose \
           --permission-mode bypassPermissions

One long-lived process fed one JSON object per line on stdin. Verified genuinely
multi-turn over a single pipe: the second turn reports the same `session_id` as the
first and remembers it, so context survives a whole call without a respawn.

Narration is why this matters beyond "it answers". The stream carries, per turn:

  assistant     content blocks, including `tool_use` with the tool's name
  system/task_summary   a short human sentence about what the tool call is for
                        ("Echo test string") -- written for a person, not a log
  system/post_turn_summary   status_detail summarising the finished turn
  rate_limit_event       quota state, worth surfacing before it bites
  result        end of turn

`task_summary.detail` is the good one. "Reading the nginx config" is what you want
spoken during a thirty-second wait; "Bash" is not.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Self

from .config import CLAUDE_BIN
from .errors import ClaudeLaunchFailed, ReplyTimeout


@dataclass
class Event:
    """One narratable thing that happened mid-turn."""

    kind: str  # tool | summary | text | rate_limit | error
    detail: str
    tool: str | None = None


@dataclass
class Reply:
    text: str = ""
    # Set when something happened to the conversation itself that the caller
    # needs told -- their session was reaped, evicted, or lost to a restart.
    # Silently handing them a stranger is the bug this exists to prevent.
    notice: str | None = None
    events: list[Event] = field(default_factory=list)
    session_id: str | None = None
    subtype: str | None = None


Narrator = Callable[[Event], None]


class FreshSession:
    """Owns one `claude` process for the life of a call."""

    def __init__(
        self,
        cwd: str | None = None,
        bypass: bool = True,
        append_system_prompt: str | None = None,
    ) -> None:
        self.cwd = cwd or os.path.expanduser("~")
        self.bypass = bypass
        self.append_system_prompt = append_system_prompt
        self.proc: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self._stderr: list[str] = []
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        argv = [
            CLAUDE_BIN,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if self.bypass:
            argv += ["--permission-mode", "bypassPermissions"]
        if self.append_system_prompt:
            argv += ["--append-system-prompt", self.append_system_prompt]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )
        except OSError as exc:
            raise ClaudeLaunchFailed(f"could not exec {CLAUDE_BIN}: {exc}") from exc
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        async for line in self.proc.stderr:
            text = line.decode(errors="replace").rstrip()
            if text:
                self._stderr.append(text)
                del self._stderr[:-40]

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr[-10:])

    async def ask(
        self,
        text: str,
        narrator: Narrator | None = None,
        timeout: float = 300.0,
    ) -> Reply:
        """Send one user turn and collect everything until `result`."""
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise ClaudeLaunchFailed("session not started")
        if self.proc.returncode is not None:
            raise ClaudeLaunchFailed(
                f"claude exited with {self.proc.returncode}: {self.stderr_tail}"
            )

        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        self.proc.stdin.write((line + "\n").encode())
        await self.proc.stdin.drain()

        reply = Reply()

        def emit(event: Event) -> None:
            reply.events.append(event)
            if narrator is not None:
                narrator(event)

        try:
            return await asyncio.wait_for(self._collect(reply, emit), timeout)
        except TimeoutError as exc:
            raise ReplyTimeout(f"no result within {timeout:.0f}s") from exc

    async def _collect(self, reply: Reply, emit: Narrator) -> Reply:
        assert self.proc is not None and self.proc.stdout is not None
        async for raw in self.proc.stdout:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            kind = obj.get("type")
            if obj.get("session_id"):
                self.session_id = reply.session_id = obj["session_id"]

            if kind == "assistant":
                for block in obj.get("message", {}).get("content", []) or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text", "").strip():
                        reply.text = block["text"].strip()
                        emit(Event("text", reply.text))
                    elif block.get("type") == "tool_use":
                        name = str(block.get("name") or "tool")
                        emit(Event("tool", name, tool=name))
            elif kind == "system":
                sub = obj.get("subtype")
                if sub == "task_summary" and obj.get("detail"):
                    emit(Event("summary", str(obj["detail"])))
                elif sub == "post_turn_summary" and obj.get("status_detail"):
                    emit(Event("summary", str(obj["status_detail"])))
            elif kind == "rate_limit_event":
                info = obj.get("rate_limit_info") or {}
                if info.get("status") and info["status"] != "allowed":
                    emit(Event("rate_limit", json.dumps(info)))
            elif kind == "result":
                reply.subtype = obj.get("subtype")
                if not reply.text and obj.get("result"):
                    reply.text = str(obj["result"]).strip()
                return reply
        raise ClaudeLaunchFailed(f"claude stream ended mid-turn: {self.stderr_tail}")

    async def close(self) -> None:
        if self.proc is None:
            return
        if self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except (OSError, RuntimeError):
                pass
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except TimeoutError:
                self.proc.kill()
        if self._stderr_task is not None:
            self._stderr_task.cancel()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
