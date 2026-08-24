"""Keeps a conversation pointed at the same Claude session between messages.

Every transport -- the phone, Discord text, a voice call -- is a sequence of
separate requests that a person experiences as one conversation. This is the
layer that makes that true: a caller key maps to a session, and stays mapped.

**A session is a tmux pane now.** It used to be a `claude` subprocess on the end
of a pipe, which worked and was invisible: nothing to attach to, nothing to look
at, nothing to kill by name. Fresh sessions are spawned by `hotline.tmuxen` and
reached the same way an attached session is, so there is one kind of session in
the system instead of two.

That change pays for itself three times over. The tmux session is named after the
conversation, so `tmux attach -t hl-discord` walks you into the session you were
just messaging. The name is derived from the key rather than remembered, so a
conversation that was reaped, or that outlived a daemon restart, **reconnects to
its own session with its context intact** instead of being handed a stranger --
which was `tofix.md` #8. And killing one is `session kill`, not an archaeology
expedition through `ps`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import standin, tmuxen
from .ccsocks import LiveSession
from .config import bindings_file
from .errors import HotlineError, SessionNotFound
from .fresh import Narrator, Reply
from .router import Route, Router, Watch, describe, parse_utterance

# How long a background relay will wait for a busy session to get round to the
# message it was handed. Generous: the sender has already been answered by the
# stand-in, so nothing is blocked on this, and a turn that takes forty minutes is
# a turn whose answer is still worth delivering.
RELAY_TIMEOUT = 3600.0

Deliver = Callable[[str, str], Awaitable[None]]


@dataclass
class Conversation:
    key: str
    cwd: str | None = None
    # The name of the session this conversation's own messages go to when it is
    # not attached to something else. Spawned on demand, in tmux.
    own: str | None = None
    last_used: float = field(default_factory=time.monotonic)
    turns: int = 0
    # Sticky routing. Once set by `connect`, plain messages go to that session
    # instead of this conversation's own, until `detach`. Bogdan asked for this
    # directly: repeating "join data-13" on every message is not how a
    # conversation works.
    attached_to: str | None = None
    # The session names from the last `session list` shown to THIS conversation.
    # `connect 2` has to mean the second line of the list you were just shown, not
    # the second line of a list computed fresh -- sessions come and go, so the
    # numbering shifts underneath you. Bogdan typed `connect 1` and reached a
    # relay session instead of the builder, then spent ten minutes being answered
    # by something that could not see the work.
    last_listing: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The turn currently in flight, if any. Kept so that a caller whose HTTP
    # request timed out can rejoin the same turn instead of starting a new one or
    # throwing away the work.
    pending: asyncio.Task[tuple[Route, Reply]] | None = None
    # Answers still owed to this caller by sessions that were busy when we wrote
    # to them.
    relays: set[asyncio.Task[None]] = field(default_factory=set)

    @property
    def tmux_target(self) -> str | None:
        return tmuxen.tmux_name(self.key)


class SessionPool:
    """One live conversation per caller-supplied key."""

    def __init__(
        self,
        router: Router | None = None,
        idle_timeout: float = 900.0,
        max_sessions: int = 8,
        cwd: str | None = None,
        append_system_prompt: str | None = None,
        deliver: Deliver | None = None,
        use_standin: bool = True,
    ) -> None:
        self.router = router or Router(default_cwd=cwd)
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions
        self.cwd = cwd
        self.append_system_prompt = append_system_prompt
        # How to reach the caller when we have something for them that no request
        # is waiting on -- a busy session finally answering. Without this the
        # stand-in can only ever promise a relay it cannot perform.
        self.deliver = deliver
        self.use_standin = use_standin
        self.conversations: dict[str, Conversation] = {}
        # Why a conversation went away, kept until the caller is told. Bogdan had
        # five turns with a session, it vanished mid-message, and the reply came
        # back from a stranger with none of the context -- with nothing anywhere
        # saying a swap had happened.
        self.retired: dict[str, str] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._shutting_down = False
        self._load_bindings()

    # ---- surviving a restart -------------------------------------------

    def _load_bindings(self) -> None:
        """Restore `connect` bindings after a restart.

        A restart no longer costs anyone their context: a conversation's own
        session lives in tmux and outlives the daemon entirely, and its name is
        derived from the key rather than remembered. So the next message walks
        back into the same pane. Only a session that has actually gone away earns
        a notice.
        """
        try:
            saved = json.loads(bindings_file().read_text())
        except (OSError, ValueError):
            return
        for key, entry in saved.items():
            if not isinstance(entry, dict):
                continue
            conv = Conversation(key=key, cwd=self.cwd, attached_to=entry.get("attached_to"))
            self.conversations[key] = conv
            if entry.get("own") and not tmuxen.exists(tmuxen.tmux_name(key)):
                self.retired[key] = (
                    "hotlined restarted and this conversation's session did not "
                    "survive it, so its context is gone"
                )

    def _save_bindings(self) -> None:
        try:
            path = bindings_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        key: {"attached_to": conv.attached_to, "own": conv.own}
                        for key, conv in self.conversations.items()
                    }
                )
            )
        except OSError:
            pass

    def start(self) -> None:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_forever())

    async def _reap_forever(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.reap()

    async def reap(self) -> int:
        """Forget idle conversations without destroying anything.

        This used to kill the subprocess, which is how a conversation could vanish
        underneath someone mid-exchange. Now it only drops the in-memory entry: the
        tmux session stays, and because its name is derived from the caller key,
        the next message finds it again with everything still in it. Bogdan's
        complaint was that he could not save a session for later -- reaping it out
        from under him is the same bug wearing a schedule.
        """
        now = time.monotonic()
        stale = [
            key
            for key, conv in self.conversations.items()
            if now - conv.last_used > self.idle_timeout
            and not conv.lock.locked()
            and not any(not task.done() for task in conv.relays)
        ]
        for key in stale:
            await self._forget(key)
        return len(stale)

    async def _forget(self, key: str) -> None:
        """Drop the binding. The session itself keeps running, and can be walked into."""
        conv = self.conversations.pop(key, None)
        if conv is not None:
            for task in conv.relays:
                task.cancel()
        if not self._shutting_down:
            self._save_bindings()

    async def _drop(self, key: str, reason: str = "") -> None:
        """Forget the conversation *and* end its session, saying why."""
        conv = self.conversations.get(key)
        if conv is not None and conv.own:
            with contextlib.suppress(HotlineError, SessionNotFound, OSError):
                await self.router.kill_session(conv.own)
        await self._forget(key)
        if reason and conv is not None:
            self.retired[key] = reason

    async def _evict_oldest(self) -> None:
        """Bound the number of live sessions hotline is responsible for.

        Evicting the least recently used is the right call over refusing the new
        request: the caller in front of you matters more than a conversation
        nobody has touched. This one really does end the session, because the
        whole point is to stop paying for it.
        """
        idle = [
            (conv.last_used, key)
            for key, conv in self.conversations.items()
            if not conv.lock.locked()
        ]
        if idle:
            await self._drop(
                min(idle)[1],
                f"too many conversations were open at once ({self.max_sessions}), so the "
                "least recently used one was closed",
            )

    def _conversation(self, key: str) -> Conversation:
        conv = self.conversations.get(key)
        if conv is None:
            conv = Conversation(key=key, cwd=self.cwd)
            self.conversations[key] = conv
        return conv

    async def _own_session(self, conv: Conversation) -> str:
        """The name of this conversation's own session, spawning one if needed."""
        name = await self._spawn_own(conv)
        return name

    async def _spawn_own(self, conv: Conversation) -> str:
        if conv.own:
            try:
                self.router.resolve(conv.own)
                return conv.own
            except (SessionNotFound, HotlineError):
                # It died, or someone killed it. Replacing it silently is precisely
                # the tofix #8 failure -- the caller keeps talking and only works
                # out something changed when the answers stop making sense.
                self.retired.setdefault(
                    conv.key,
                    f"your session {conv.own} had gone away, so this is a new one "
                    "with none of the earlier conversation in it",
                )
                conv.own = None
        session = await tmuxen.spawn(conv.key, cwd=conv.cwd or self.cwd,
                                     bypass=self.router.bypass)
        conv.own = session.name
        self._save_bindings()
        return session.name

    # ---- control commands ------------------------------------------------

    async def _control(self, conv: Conversation, route: Route) -> Reply | None:
        """Handle a connection command. None means "that was not really a command".

        `connect` and `kill` only count when the target actually resolves --
        otherwise "connect the dots in this diagram" and "kill the process on port
        8080" would be swallowed as control commands instead of being answered.
        """
        live = self.router.sessions()

        if route.action == "list":
            if not live:
                return Reply(text="No live Claude sessions right now.", subtype="control")
            conv.last_listing = [session.name for session in live]
            lines = ["Live sessions, newest first:"]
            for index, session in enumerate(live, 1):
                marks = []
                if conv.attached_to == session.name:
                    marks.append("connected")
                if conv.own == session.name:
                    marks.append("this conversation")
                if session.tmux_session:
                    marks.append(f"tmux attach -t {session.tmux_session}")
                else:
                    marks.append("no pane — cannot be attached to")
                lines.append(
                    f"{index}. {session.name} — {session.cwd} "
                    f"[{session.status or '?'}, pid {session.pid}] ({'; '.join(marks)})"
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
                return Reply(text=f"Detached from {was}. Back to your own session.",
                             subtype="control")
            # "new session" means throw this one away and start over, so it ends
            # the session rather than falling through as chat. A bare "detach" is a
            # command and must be answered as one -- leaking it to the model as
            # chat is what Bogdan hit.
            if route.text.strip().lower() == "new session":
                if conv.own:
                    with contextlib.suppress(HotlineError, SessionNotFound, OSError):
                        await self.router.kill_session(conv.own)
                conv.own = None
                self._save_bindings()
                return Reply(
                    text="Started over. Your previous session was closed; the next "
                         "thing you say opens a fresh one.",
                    subtype="control",
                )
            return Reply(text="Not connected to anything — you are on your own session.",
                         subtype="control")

        if route.action == "where":
            if conv.attached_to:
                return Reply(text=f"Connected to {conv.attached_to}.", subtype="control")
            if conv.own:
                return Reply(
                    text=f"On your own session {conv.own}, in tmux as "
                         f"{conv.tmux_target} — `tmux attach -t {conv.tmux_target}`.",
                    subtype="control",
                )
            return Reply(
                text="Not connected to anything, and your own session has not been "
                     "started yet — say anything and it will be.",
                subtype="control",
            )

        if route.action == "kill" and route.target:
            try:
                target = self._resolve_listed(conv, route.target, live)
            except SessionNotFound as exc:
                return Reply(text=str(exc), subtype="control")
            if target is None:
                return None  # not a session -- let it be answered as a question
            session = target
            if conv.own == session.name:
                conv.own = None
            if conv.attached_to == session.name:
                conv.attached_to = None
            try:
                outcome = await self.router.kill_session(str(session.pid))
            except HotlineError as exc:
                return Reply(text=str(exc), subtype="control")
            self._save_bindings()
            return Reply(text=outcome, subtype="control")

        if route.action == "connect" and route.target:
            try:
                target = self._resolve_listed(conv, route.target, live)
            except SessionNotFound as exc:
                return Reply(text=str(exc), subtype="control")
            if target is None:
                return None  # not a session name -- treat it as a question
            session = target
            conv.attached_to = session.name
            return Reply(
                text=f"Connected to {describe(session)}. Everything you say now goes there "
                     f'until you say "detach".',
                subtype="control",
            )
        return None

    def _resolve_listed(
        self, conv: Conversation, spec: str, live: list[LiveSession]
    ) -> LiveSession | None:
        """Resolve a target the way the caller meant it, or None if it is not one.

        "connect 2" means the second line of the list that was just shown to *this*
        conversation, not pid 2 and not the second line of a list computed now.
        Bogdan typed `connect 1`, reached a different session than the one he had
        been shown, and spent ten minutes being answered by something that could
        not see the work.
        """
        if spec.isdigit() and 1 <= int(spec) <= len(conv.last_listing):
            wanted = conv.last_listing[int(spec) - 1]
            try:
                return self.router.resolve(wanted)
            except (SessionNotFound, HotlineError):
                # Meant unambiguously and no longer reachable. Falling through to
                # the model here would answer "connect 1" as though it were chat.
                raise SessionNotFound(
                    f"{wanted} was number {spec} when I listed them, but it has "
                    'since exited. Say "session list" for a current list.'
                ) from None
        if spec.isdigit() and 1 <= int(spec) <= len(live):
            return live[int(spec) - 1]
        try:
            return self.router.resolve(spec)
        except (SessionNotFound, HotlineError):
            return None

    # ---- the turn --------------------------------------------------------

    async def ask(
        self,
        key: str,
        utterance: str,
        narrator: Narrator | None = None,
        timeout: float = 300.0,
    ) -> tuple[Route, Reply]:
        """One turn of a conversation, routed the same way the CLI routes it."""
        route = parse_utterance(utterance)
        conv = self._conversation(key)

        if route.mode == "control":
            handled = await self._control(conv, route)
            if handled is not None:
                handled.notice = self.retired.pop(key, None)
                conv.last_used = time.monotonic()
                self._save_bindings()
                return route, handled
            # Not actually a control command; fall through as an ordinary question.
            route = Route("fresh", None, route.text)

        if conv.attached_to and (route.mode == "fresh" or route.implicit):
            target, mode = conv.attached_to, "attach"
        elif route.mode != "fresh" and route.target:
            target, mode = route.target, route.mode
        else:
            if len(self.conversations) > self.max_sessions:
                await self._evict_oldest()
            async with conv.lock:
                target = await self._own_session(conv)
            mode = "own"

        try:
            reply = await self._send(conv, target, route.text, narrator, timeout)
        except SessionNotFound:
            # The session this conversation was pointed at is gone. Say so; do not
            # quietly substitute a stranger, which is the whole of tofix #8.
            if conv.attached_to == target:
                conv.attached_to = None
                self._save_bindings()
                raise SessionNotFound(
                    f"{target} is gone — it exited or was killed. You are back on "
                    f'your own session; say "session list" to pick another.'
                ) from None
            # A session that died between spawning and delivering. Rare, and the
            # honest thing is to say so rather than retry into a stranger.
            if conv.own == target:
                conv.own = None
                self._save_bindings()
            raise

        conv.last_used = time.monotonic()
        conv.turns += 1
        # Popped here, not on the way in: routing this turn may itself have
        # discovered that the session went away, and that notice is owed to the
        # caller now rather than one message late.
        if reply.notice is None:
            reply.notice = self.retired.pop(key, None)
        self._save_bindings()
        return Route(mode, target, route.text), reply

    async def _send(
        self,
        conv: Conversation,
        target: str,
        text: str,
        narrator: Narrator | None,
        timeout: float,
    ) -> Reply:
        """Deliver, and either wait for the answer or hand the caller to a stand-in.

        The split matters. Delivery is fast and either happened or did not; the
        answer may be minutes away. When the target is mid-turn, waiting on it
        gives the sender silence indistinguishable from the message being lost, so
        instead a stand-in reports on it immediately and the real answer is relayed
        when it lands.
        """
        watch = await self.router.deliver(target, text)

        if watch.was_busy and self.use_standin:
            standing = await standin.report(watch.session, text, delivered=True)
            self._relay_later(conv, watch)
            return Reply(
                text=standing.spoken(watch.session.name),
                session_id=watch.session.session_id,
                subtype="standin",
            )

        return await self.router.collect(watch, narrator=narrator, timeout=timeout)

    def _relay_later(self, conv: Conversation, watch: Watch) -> None:
        """Keep waiting for the busy session, and push its answer out when it comes.

        This is the half of the promise the stand-in makes. Without it the sender is
        told "I'll relay its answer" by something that has no way to do so.
        """
        if self.deliver is None:
            return

        async def run() -> None:
            name = watch.session.name
            try:
                reply = await self.router.collect(watch, timeout=RELAY_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except HotlineError as exc:
                body = f"{name} never answered the message I queued for it: {exc}"
            else:
                body = f"{name} has finished the message you sent it:\n\n{reply.text}"
            assert self.deliver is not None
            with contextlib.suppress(Exception):
                await self.deliver(conv.key, body)

        task = asyncio.create_task(run())
        conv.relays.add(task)
        task.add_done_callback(conv.relays.discard)

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
        """Stop tracking conversations. Deliberately leaves their sessions running.

        A daemon restart must not cost anyone their context -- the sessions are in
        tmux, they are named after their conversations, and the next message walks
        straight back into them. Killing them here would reintroduce exactly the
        vanishing-session bug the tmux move was made to fix.
        """
        self._save_bindings()
        self._shutting_down = True
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        # Cancel first, then await -- a cancel() that is never awaited leaves the
        # task in "cancelling" forever and the subprocess behind it running.
        tasks = [
            task
            for conv in self.conversations.values()
            for task in ([conv.pending] if conv.pending else []) + list(conv.relays)
            if not task.done()
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        self.conversations.clear()

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
                    "session": conv.own,
                    "tmux": conv.tmux_target if conv.own else None,
                    "attached_to": conv.attached_to,
                    "relays_pending": sum(1 for t in conv.relays if not t.done()),
                }
                for conv in self.conversations.values()
            ],
        }


HELP_TEXT = """**Commands** (these are handled by hotline itself, not sent to a model)

`help` — this
`session list` — live sessions, numbered, with the tmux target for each
`connect <n|name|dir>` — bind this conversation to a session; then just talk
`detach` — unbind, back to your own session
`session kill <n|name>` — SIGTERM it, then close its tmux session
`where am i` — which session you are bound to, and how to attach to it
`resources` — RAM, VRAM, load
`new session` — close your session and start over

Anything else goes to a Claude session. `connect` accepts a number from the
list, a session name, a directory (`uxonews`), or an ordinal (`the older one`).

Your session runs in tmux, so you can walk up to this machine and take it over
with `tmux attach -t hl-<name>` — `where am i` tells you the exact command.
If you message a session that is mid-turn, a stand-in answers straight away with
what it is doing, and the real answer is relayed here when it lands.

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
