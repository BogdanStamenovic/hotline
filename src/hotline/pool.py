"""Keeps a Claude session alive between HTTP requests.

The iPhone Shortcut is a loop: dictate, POST, speak, repeat. Each turn is a
separate HTTP request, but they are all one conversation, so the `session_id` the
phone sends has to map to a `claude` process that stays alive between them.
Spawning per request would make every turn amnesiac and pay startup cost twice.

Idle sessions are reaped. A `claude` subprocess is not free, and a phone that
walks out of Tailscale range mid-call never sends a goodbye -- so the only
trustworthy signal that a conversation is over is that nothing has arrived for a
while.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .errors import HotlineError, SessionNotFound
from .fresh import FreshSession, Narrator, Reply
from .router import Route, Router, describe, parse_utterance


@dataclass
class Conversation:
    key: str
    cwd: str | None = None
    session: FreshSession | None = None
    last_used: float = field(default_factory=time.monotonic)
    turns: int = 0
    # Sticky routing. Once set by `connect`, plain messages go to that session
    # instead of the pooled subprocess, until `detach`. Bogdan asked for this
    # directly: repeating "join data-13" on every message is not how a
    # conversation works.
    attached_to: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The turn currently in flight, if any. Kept so that a caller whose HTTP
    # request timed out can rejoin the same turn instead of starting a new one or
    # throwing away the work.
    pending: asyncio.Task[tuple[Route, Reply]] | None = None

    @property
    def claude_session_id(self) -> str | None:
        return self.session.session_id if self.session else None


class SessionPool:
    """One live conversation per caller-supplied key."""

    def __init__(
        self,
        router: Router | None = None,
        idle_timeout: float = 900.0,
        max_sessions: int = 8,
        cwd: str | None = None,
        append_system_prompt: str | None = None,
    ) -> None:
        self.router = router or Router(default_cwd=cwd)
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions
        self.cwd = cwd
        self.append_system_prompt = append_system_prompt
        self.conversations: dict[str, Conversation] = {}
        self._reaper: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_forever())

    async def _reap_forever(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.reap()

    async def reap(self) -> int:
        now = time.monotonic()
        stale = [
            key
            for key, conv in self.conversations.items()
            if now - conv.last_used > self.idle_timeout and not conv.lock.locked()
        ]
        for key in stale:
            await self._drop(key)
        return len(stale)

    async def _drop(self, key: str) -> None:
        conv = self.conversations.pop(key, None)
        if conv and conv.session:
            await conv.session.close()

    async def _evict_oldest(self) -> None:
        """Bound the number of live `claude` processes.

        Evicting the least recently used is the right call over refusing the new
        request: the caller in front of you matters more than a conversation
        nobody has touched.
        """
        idle = [
            (conv.last_used, key)
            for key, conv in self.conversations.items()
            if not conv.lock.locked()
        ]
        if idle:
            await self._drop(min(idle)[1])

    def _conversation(self, key: str) -> Conversation:
        conv = self.conversations.get(key)
        if conv is None:
            conv = Conversation(key=key, cwd=self.cwd)
            self.conversations[key] = conv
        return conv

    def _control(self, conv: Conversation, route: Route) -> Reply | None:
        """Handle a connection command. None means "that was not really a command".

        `connect` only counts when the target actually resolves -- otherwise
        "connect the dots in this diagram" would be swallowed as a control command
        instead of being answered.
        """
        live = self.router.sessions()

        if route.action == "list":
            if not live:
                return Reply(text="No live Claude sessions right now.", subtype="control")
            lines = ["Live sessions, newest first:"]
            for index, session in enumerate(live, 1):
                marker = " (connected)" if conv.attached_to == session.name else ""
                lines.append(
                    f"{index}. {session.name} — {session.cwd} "
                    f"[{session.status or '?'}, pid {session.pid}]{marker}"
                )
            lines.append("")
            lines.append('Say "connect 2" or "connect data-13", then just talk. "detach" to stop.')
            return Reply(text="\n".join(lines), subtype="control")

        if route.action == "help":
            return Reply(text=HELP_TEXT, subtype="control")

        if route.action == "resources":
            return Reply(text=describe_resources(), subtype="control")

        if route.action == "detach":
            was = conv.attached_to
            conv.attached_to = None
            if was:
                return Reply(text=f"Detached from {was}. Back to a fresh session.",
                             subtype="control")
            # "new session" means start over, so it falls through to a real turn.
            # A bare "detach" is a command and must be answered as one -- leaking
            # it to the model as chat is what Bogdan hit.
            if route.text.strip().lower() != "new session":
                return Reply(text="Not connected to anything — already on a fresh session.",
                             subtype="control")
            return None

        if route.action == "where":
            if conv.attached_to:
                return Reply(text=f"Connected to {conv.attached_to}.", subtype="control")
            claude_id = conv.claude_session_id or "not started yet"
            return Reply(
                text=f"Not connected to anything — talking to a fresh session ({claude_id}).",
                subtype="control",
            )

        if route.action == "connect" and route.target:
            spec = route.target
            # "connect 2" means the second line of the list that was just shown,
            # not pid 2. Bare numbers are indices here; pids are reachable by name.
            if spec.isdigit() and 1 <= int(spec) <= len(live):
                session = live[int(spec) - 1]
            else:
                try:
                    session = self.router.resolve(spec)
                except SessionNotFound:
                    return None  # not a session name -- treat it as a question
            conv.attached_to = session.name
            return Reply(
                text=f"Connected to {describe(session)}. Everything you say now goes there "
                     f'until you say "detach".',
                subtype="control",
            )
        return None

    async def ask(
        self,
        key: str,
        utterance: str,
        narrator: Narrator | None = None,
        timeout: float = 300.0,
    ) -> tuple[Route, Reply]:
        """One turn of a conversation, routed the same way the CLI routes it."""
        route = parse_utterance(utterance)

        if route.mode == "control":
            conv = self._conversation(key)
            handled = self._control(conv, route)
            if handled is not None:
                return route, handled
            # Not actually a control command; fall through as an ordinary question.
            route = Route("fresh", None, route.text)

        conv = self._conversation(key)
        if conv.attached_to and (route.mode == "fresh" or route.implicit):
            reply = await self.router.ask_session(
                conv.attached_to, route.text, narrator=narrator, timeout=timeout
            )
            conv.last_used = time.monotonic()
            conv.turns += 1
            return Route("attach", conv.attached_to, route.text), reply

        if route.mode != "fresh" and route.target:
            # Attach and agent modes have no process of their own to keep warm --
            # the session on the other end is the state.
            reply = await self.router.ask_session(
                route.target, route.text, narrator=narrator, timeout=timeout
            )
            return route, reply

        if len(self.conversations) > self.max_sessions:
            await self._evict_oldest()

        async with conv.lock:
            if conv.session is None or (
                conv.session.proc is not None and conv.session.proc.returncode is not None
            ):
                if conv.session is not None:
                    await conv.session.close()
                conv.session = FreshSession(
                    conv.cwd,
                    bypass=self.router.bypass,
                    append_system_prompt=self.append_system_prompt,
                )
                await conv.session.start()
            try:
                reply = await conv.session.ask(route.text, narrator=narrator, timeout=timeout)
            except HotlineError:
                # A dead subprocess must not poison the key forever; the next turn
                # should get a fresh one rather than the same corpse.
                await conv.session.close()
                conv.session = None
                raise
            conv.last_used = time.monotonic()
            conv.turns += 1
        return route, reply

    async def ask_soft(
        self,
        key: str,
        utterance: str,
        narrator: Narrator | None = None,
        soft_timeout: float = 100.0,
        hard_timeout: float = 900.0,
    ) -> tuple[Route, Reply] | None:
        """Like `ask`, but returns None instead of waiting out a long turn.

        The turn is shielded, not cancelled: a phone giving up after a hundred
        seconds must not destroy several minutes of real work. Whatever the caller
        says next rejoins the same task and collects its answer.
        """
        conv = self.conversations.get(key)
        if conv is not None and conv.pending is not None and not conv.pending.done():
            task = conv.pending
        else:
            task = asyncio.ensure_future(
                self.ask(key, utterance, narrator=narrator, timeout=hard_timeout)
            )
            conv = self.conversations.get(key)
            if conv is not None:
                conv.pending = task

        try:
            result = await asyncio.wait_for(asyncio.shield(task), soft_timeout)
        except TimeoutError:
            return None
        conv = self.conversations.get(key)
        if conv is not None and conv.pending is task:
            conv.pending = None
        return result

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        # Cancel first, then await -- a cancel() that is never awaited leaves the
        # task in "cancelling" forever and the subprocess behind it running.
        pending = [
            conv.pending
            for conv in self.conversations.values()
            if conv.pending is not None and not conv.pending.done()
        ]
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001,S110 - shutting down
                pass
        for key in list(self.conversations):
            await self._drop(key)

    def stats(self) -> dict[str, Any]:
        # Any, not object: this is shaped for JSON and for the bot's status
        # command, and pretending it has a static shape only buys casts.
        return {
            "conversations": len(self.conversations),
            "keys": [
                {
                    "key": conv.key,
                    "turns": conv.turns,
                    "idle_seconds": round(time.monotonic() - conv.last_used, 1),
                    "claude_session_id": conv.claude_session_id,
                    "attached_to": conv.attached_to,
                }
                for conv in self.conversations.values()
            ],
        }


HELP_TEXT = """**Commands** (these are handled by hotline itself, not sent to a model)

`help` — this
`session list` — live sessions, numbered
`connect <n|name|dir>` — bind this conversation to a session; then just talk
`detach` — unbind, back to a fresh session
`where am i` — which session you are bound to
`resources` — RAM, VRAM, load
`new session` — throw away context and start over

Anything else goes to a Claude session. `connect` accepts a number from the
list, a session name, a directory (`uxonews`), or an ordinal (`the older one`).

On Discord only: `!status`, `!join`, `!leave`."""


def describe_resources() -> str:
    """RAM, VRAM and load, so you can tell whether another session will fit."""
    import shutil
    import subprocess

    lines: list[str] = []
    try:
        meminfo = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.split()[0]) // 1024
        lines.append(
            f"RAM: {meminfo['MemAvailable']} MB available of {meminfo['MemTotal']} MB"
        )
    except (OSError, KeyError, ValueError, IndexError):
        lines.append("RAM: unreadable")

    try:
        with open("/proc/loadavg") as fh:
            load = fh.read().split()[:3]
        lines.append(f"Load: {' '.join(load)}")
    except (OSError, IndexError):
        pass

    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.strip()
            if out:
                lines.append(f"GPU: {out}")
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        usage = shutil.disk_usage("/")
        lines.append(f"Disk /: {usage.free // 2**30} GB free of {usage.total // 2**30} GB")
    except OSError:
        pass

    return "\n".join(lines)
