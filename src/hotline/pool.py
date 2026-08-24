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

from .errors import HotlineError
from .fresh import FreshSession, Narrator, Reply
from .router import Route, Router, parse_utterance


@dataclass
class Conversation:
    key: str
    cwd: str | None = None
    session: FreshSession | None = None
    last_used: float = field(default_factory=time.monotonic)
    turns: int = 0
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
    ) -> None:
        self.router = router or Router(default_cwd=cwd)
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions
        self.cwd = cwd
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

    async def ask(
        self,
        key: str,
        utterance: str,
        narrator: Narrator | None = None,
        timeout: float = 300.0,
    ) -> tuple[Route, Reply]:
        """One turn of a conversation, routed the same way the CLI routes it."""
        route = parse_utterance(utterance)

        if route.mode != "fresh" and route.target:
            # Attach and agent modes have no process of their own to keep warm --
            # the session on the other end is the state.
            reply = await self.router.ask_session(
                route.target, route.text, narrator=narrator, timeout=timeout
            )
            return route, reply

        conv = self.conversations.get(key)
        if conv is None:
            if len(self.conversations) >= self.max_sessions:
                await self._evict_oldest()
            conv = Conversation(key=key, cwd=self.cwd)
            self.conversations[key] = conv

        async with conv.lock:
            if conv.session is None or (
                conv.session.proc is not None and conv.session.proc.returncode is not None
            ):
                if conv.session is not None:
                    await conv.session.close()
                conv.session = FreshSession(conv.cwd, bypass=self.router.bypass)
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
                }
                for conv in self.conversations.values()
            ],
        }
