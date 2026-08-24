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
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import standin, tmuxen
from .ccsocks import LiveSession
from .config import bindings_file
from .errors import HotlineError, SessionNotFound
from .fresh import Narrator, Reply
from .provenance import Origin
from .router import Route, Router, Watch, describe, parse_utterance
from .transcript import transcript_path

log = logging.getLogger(__name__)

# Answers to "send it?". Deliberately a small closed set: anything else is
# treated as a NEW message replacing the held one, which is the safe reading --
# a caller who types another instruction instead of answering has changed their
# mind, and delivering the old text would be the exact failure this guards.
_YES = re.compile(r"^(?:y|yes|yeah|yep|yup|ok|okay|sure|send(?:\s+it)?|go|do\s+it|"
                  r"confirm(?:ed)?)\s*[.!]?$", re.IGNORECASE)
def _clip(text: str | None, limit: int = 160) -> str:
    """Echo the held message back short, so he can see WHAT he is confirming."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


_NO = re.compile(r"^(?:n|no|nope|nah|cancel|stop|don'?t|drop\s+it|never\s*mind)\s*[.!]?$",
                 re.IGNORECASE)

# How long a background relay will wait for a busy session to get round to the
# message it was handed. Generous: the sender has already been answered by the
# stand-in, so nothing is blocked on this, and a turn that takes forty minutes is
# a turn whose answer is still worth delivering.
RELAY_TIMEOUT = 3600.0

# How long a session hotline started may sit with nobody talking to it before it
# is closed. Reaping deliberately leaves sessions running so they can be attached
# to later, which without a bound is a slow leak: each `claude` is a few hundred
# megabytes and this machine has fifteen gigabytes for everything including local
# models. Four hours is long enough to come back to something after lunch.
ORPHAN_TIMEOUT = 4 * 3600.0

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
    # The stable handle for the same session. A name can change and a pid
    # certainly does -- the builder session's pid was replaced mid-conversation
    # and the binding was correctly dropped rather than pointed at a stranger,
    # which is safe and still meant the connection did not stick. Session ids
    # survive both, so resolution prefers this and keeps the name for display.
    attached_id: str | None = None
    # The agent names from the last `resume` listing shown to THIS conversation,
    # so `resume 2` means the second line he was shown rather than the second
    # line of a list computed fresh. Same reasoning as `last_listing`, and the
    # same bug if it is skipped -- except a wrong pick here revives a stranger.
    last_resume_listing: list[str] = field(default_factory=list)
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
    # A message held back until the caller confirms where it is going, and the
    # target it is going to. Bogdan asked for this after messages of his reached
    # sessions he did not mean: in #general he wants to be told who he is talking
    # to *before* the message lands, not after.
    held: str | None = None
    held_for: str | None = None
    # The target he has already confirmed. Asking on every single message would
    # make the channel unusable, so the confirmation is per-target and sticky:
    # once he has said yes to a session, messages flow until the target changes
    # under him -- which is the event he actually wants to catch.
    confirmed: str | None = None
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
        confirm_keys: set[str] | None = None,
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
        # Conversations where a message is held for confirmation before it is
        # delivered. Only #general: a per-agent channel is unambiguous by
        # construction -- that channel *is* that agent -- so asking there would be
        # ceremony with no question behind it.
        self.confirm_keys = confirm_keys or set()
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
            conv = Conversation(
                key=key,
                cwd=self.cwd,
                attached_to=entry.get("attached_to"),
                attached_id=entry.get("attached_id"),
            )
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
                        key: {
                            "attached_to": conv.attached_to,
                            "attached_id": conv.attached_id,
                            "own": conv.own,
                        }
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
        await self._close_orphans()
        return len(stale)

    async def _close_orphans(self) -> None:
        """Close sessions we started that nobody is talking to any more.

        Only sessions hotline itself spawned (the `hl-` tmux prefix) and only
        those not bound to a live conversation -- a session Bogdan started, or one
        someone is attached to, is never a candidate. Idleness is measured from
        the transcript, so a session left thinking for three hours is not orphaned.
        """
        bound = {conv.own for conv in self.conversations.values() if conv.own}
        bound |= {conv.attached_to for conv in self.conversations.values() if conv.attached_to}
        now = time.time()
        for session in self.router.sessions():
            name = session.tmux_session or ""
            if not name.startswith(tmuxen.PREFIX) or session.name in bound:
                continue
            path = transcript_path(session.session_id)
            try:
                idle = now - path.stat().st_mtime if path else 0.0
            except OSError:
                continue
            if idle < ORPHAN_TIMEOUT:
                continue
            with contextlib.suppress(HotlineError, SessionNotFound, OSError):
                await self.router.kill_session(str(session.pid))

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

    def bind(self, key: str, session_name: str, session_id: str | None = None) -> None:
        """Point a conversation at a session without a `connect` being spoken.

        Walking into an agent's own voice channel already says who you want to
        talk to; making you then say it out loud as well would be the interface
        asking you to repeat yourself.
        """
        conv = self._conversation(key)
        conv.attached_to = session_name
        conv.attached_id = session_id
        self._save_bindings()

    def release(self, key: str) -> str | None:
        """Undo a `bind`. Returns what it was pointing at, if anything."""
        conv = self.conversations.get(key)
        if conv is None:
            return None
        was, conv.attached_to = conv.attached_to, None
        # A conversation with no binding and no session of its own is just a
        # stale key in the file; drop it rather than persist it forever.
        if conv.own is None and not conv.turns:
            self.conversations.pop(key, None)
        self._save_bindings()
        return was

    async def _own_session(self, conv: Conversation, task: str = "") -> str:
        """The name of this conversation's own session, spawning one if needed."""
        name = await self._spawn_own(conv, task)
        return name

    def _enrol(self, session_id: str, name: str, task: str) -> int | None:
        """Register a session Bogdan created himself, and give it a channel.

        Declaring used to be cooperative: an agent registered itself if it
        thought to. That works for agents spawned from a script that tells them
        to, and fails for exactly the ones Bogdan starts by talking -- they have
        no idea they are supposed to, so they never get a channel and every one
        of them narrates into #general instead. He asked for the opposite rule:
        an agent he makes directly MUST have its own thread.

        The task is provisional -- his opening message, which is the best guess
        available before the agent has done anything. `--declare` retasks in
        place, keeping the channel, so the agent can correct it once it knows
        what the work actually is.

        Never fatal. A session that is running is worth more than a tidy
        registry, so a Discord failure here costs a channel, not the session.
        """
        from .agents import Registry
        from .channels import from_env as channels_from_env

        try:
            registry = Registry()
            existing = registry.get(session_id)
            if existing is not None:
                return existing.channel_id
            summary = " ".join(task.split())[:200] or "started from Discord; not yet declared"
            agent = registry.declare(session_id, name, summary)
            manager = channels_from_env()
            if manager is None or not agent.wants_channel or agent.channel_id is not None:
                return agent.channel_id
            agent.channel_id = manager.create_text(agent.name, topic=agent.task)
            registry.save()
            return agent.channel_id
        except Exception as exc:  # noqa: BLE001 - never take a session down over bookkeeping
            log.warning("could not enrol %s: %s: %s", name, type(exc).__name__, exc)
            return None

    async def _spawn_own(self, conv: Conversation, task: str = "") -> str:
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
        self._enrol(session.session_id, session.name, task)
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

        if route.action == "agents":
            from .agents import Registry

            known = sorted(
                Registry().agents.values(), key=lambda a: a.declared_at, reverse=True
            )
            if not known:
                return Reply(
                    text="No agents have declared themselves.", subtype="control"
                )
            lines = [a.describe() for a in known]
            return Reply(text="\n".join(lines), subtype="control")

        if route.action == "new_agent":
            return await self._new_agent(route)

        if route.action == "resume":
            handled = await self._resume(conv, route, live)
            if handled is not None:
                return handled
            # Not a resumable agent and not a live session: let it be answered as
            # an ordinary question rather than swallowed.
            return None

        if route.action == "detach":
            was = conv.attached_to
            conv.attached_to = conv.attached_id = None
            conv.confirmed = conv.held = conv.held_for = None
            if was:
                return Reply(text=f"Detached from {was}. Back to your own session.",
                             subtype="control")
            # "new session" means throw this one away and start over, so it ends
            # the session rather than falling through as chat. A bare "detach" is a
            # command and must be answered as one -- leaking it to the model as
            # chat is what Bogdan hit.
            if route.text.strip().lower() == "new session":
                # Report what actually happened. This used to announce "your
                # previous session was closed" unconditionally, with the kill
                # wrapped in a suppress -- so when the session survived, the next
                # message was answered by the very session he had just been told
                # was gone. A pane is named after the conversation, so
                # `tmuxen.spawn` hands the same one back if it is still alive:
                # the claim was not just unverified, it was self-defeating.
                closed, was = False, conv.own
                if was:
                    with contextlib.suppress(HotlineError, SessionNotFound, OSError):
                        await self.router.kill_session(was)
                    await asyncio.sleep(0.5)
                    closed = not tmuxen.exists(tmuxen.tmux_name(conv.key))
                conv.own = None
                conv.confirmed = conv.held = conv.held_for = None
                self._save_bindings()
                if was and not closed:
                    return Reply(
                        text=f"**{was} did not close** — it is still running, and "
                             f"because this channel's session is named after the "
                             f"channel, your next message would reach it again "
                             f"rather than something new.\nIf you want a genuinely "
                             f"separate one, say `new agent <what it should do>` — "
                             f"that gets its own session and its own channel. To "
                             f"force this one down: `session kill {was}`.",
                        subtype="control",
                    )
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
                conv.attached_to = conv.attached_id = None
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
            conv.attached_id = session.session_id
            # Re-arm the "send it?" question: the target just changed, so an
            # earlier confirmation describes somewhere his next message is no
            # longer going.
            conv.confirmed = conv.held = conv.held_for = None
            return Reply(
                text=f"Connected to {describe(session)}. Everything you say now goes there "
                     f'until you say "detach".',
                subtype="control",
            )
        return None

    async def _new_agent(self, route: Route) -> Reply:
        """`new agent <task>` -- a genuinely separate agent, with its own channel.

        This did not exist and could not be improvised, which is the bug it
        fixes. A pane is named after the conversation key, so a channel's own
        session is a singleton: `tmuxen.spawn` hands back the existing pane when
        one is already there, and "new session" therefore closed nothing it could
        not immediately re-open as the same session. Bogdan asked #general for a
        new agent, was told it had started over, and was answered by the session
        that was already running.

        So the key is minted here rather than derived. Its own key means its own
        pane, its own registry record and its own channel -- which is the shape
        he asked for when he said every agent needs its own thread.
        """
        task = (route.target or "").strip()
        if not task:
            return Reply(
                text='Say what it should work on — `new agent stylize the app on '
                     'port 8000`. The task becomes its name and its channel topic.',
                subtype="control",
            )
        # Minted, not derived: two agents started a second apart must not collide,
        # and nothing about the conversation can be part of the name without
        # making it a singleton again.
        key = f"agent-{secrets.token_hex(3)}"
        try:
            session = await tmuxen.spawn(key, cwd=self.cwd, bypass=self.router.bypass)
        except HotlineError as exc:
            return Reply(text=f"Could not start it: {exc}", subtype="control")

        channel = self._enrol(session.session_id, session.name, task)
        # Handed the task straight away rather than waiting to be spoken to: he
        # asked for an agent to do a thing, not for an idle session.
        with contextlib.suppress(HotlineError):
            await self.router.deliver(session.name, task, Origin(
                kind="system",
                label="hotline, relaying the task this agent was created for",
            ))
        where = f" in <#{channel}>" if channel else " (no channel — Discord refused)"
        return Reply(
            text=f"Started **{session.name}**{where}, working on:\n> {task}\n"
                 f"Talk to it there, or `connect {session.name}` from here. "
                 f"`tmux attach -t {tmuxen.tmux_name(key)}` to take it over directly.",
            subtype="control",
        )

    async def _resume(
        self, conv: Conversation, route: Route, live: list[LiveSession]
    ) -> Reply | None:
        """`resume` -- what can be brought back, and bringing one back.

        Reviving means spawning a session and handing it a brief, and the brief
        can be a whole handoff or a whole transcript to read. Waiting for that to
        be understood would hold the Discord turn open for minutes, so the seed
        is sent in the background and the reply comes back as soon as the session
        exists. He gets the agent's own first words in its channel, where the
        rest of that conversation belongs anyway.
        """
        from .agents import Agent, Registry
        from .channels import from_env as channels_from_env
        from .revive import brief_for, rehome, resumable

        registry = Registry()
        live_ids = {s.session_id for s in live}
        offer = resumable(registry, live_ids)

        if not route.target:
            if not offer:
                return Reply(
                    text="Nothing to resume — every agent on record is still running.",
                    subtype="control",
                )
            conv.last_resume_listing = [a.name for a in offer]
            lines = ["Agents you can bring back, newest first:"]
            for index, candidate in enumerate(offer, 1):
                found = brief_for(candidate)
                source = "handoff" if found and found.from_handoff else "transcript only"
                state = "finished" if candidate.done else "killed"
                lines.append(
                    f"{index}. {candidate.name} — {candidate.task} [{state}; {source}]"
                )
            lines.append("")
            lines.append('Say "resume 2" or "resume data-f3".')
            return Reply(text="\n".join(lines), subtype="control")

        # A name that belongs to something still running is a connect, not a
        # revive -- resurrecting the living would fork the work in two.
        spec = route.target.strip()
        for session in live:
            if session.name.lower() == spec.lower():
                conv.attached_to = session.name
                conv.attached_id = session.session_id
                conv.confirmed = conv.held = conv.held_for = None
                return Reply(
                    text=f"{session.name} is still running — connected to it instead "
                         f"of resurrecting it.",
                    subtype="control",
                )

        agent: Agent | None = None
        if spec.isdigit() and conv.last_resume_listing:
            index = int(spec) - 1
            if 0 <= index < len(conv.last_resume_listing):
                agent = registry.by_name(conv.last_resume_listing[index])
        if agent is None:
            agent = registry.by_name(spec)
        if agent is None or agent.session_id in live_ids:
            return None

        brief = brief_for(agent)
        if brief is None:
            return Reply(
                text=f"{agent.name} left no handoff and its transcript is gone, so "
                     "there is nothing to resume it from.",
                subtype="control",
            )
        try:
            session = await tmuxen.spawn(agent.name, cwd=self.cwd, name=agent.name)
        except HotlineError as exc:
            return Reply(text=f"Could not start a session for {agent.name}: {exc}",
                         subtype="control")

        had = agent.channel_id
        name, task = agent.name, agent.task
        try:
            revived = rehome(registry, agent, session.session_id, channels_from_env())
            channel = revived.channel_id
        except HotlineError as exc:
            log.warning("revived %s but could not sort its channel: %s", name, exc)
            channel = had

        async def seed() -> None:
            with contextlib.suppress(Exception):
                await self.router.ask_session(session.name, brief.seed, timeout=600.0)

        self._seeding = asyncio.create_task(seed())

        where = f" — reading it in <#{channel}> now" if channel else ""
        source = "its handoff" if brief.from_handoff else "its transcript (it was killed, so there is no handoff)"
        return Reply(
            text=f"Brought **{name}** back as `{session.name}`, seeded from {source}"
                 f"{where}.\n> {task}",
            subtype="control",
        )

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
        origin: Origin | None = None,
    ) -> tuple[Route, Reply]:
        """One turn of a conversation, routed the same way the CLI routes it."""
        conv = self._conversation(key)

        # A held message is answered before anything is parsed, because "yes" is
        # not a question for a model and routing it as one would deliver the word
        # "yes" to a session instead of the message it was approving.
        if conv.held is not None:
            answer = utterance.strip()
            if _YES.match(answer):
                utterance, conv.confirmed = conv.held, conv.held_for
                conv.held = conv.held_for = None
            elif _NO.match(answer):
                dropped, target = conv.held, conv.held_for
                conv.held = conv.held_for = None
                self._save_bindings()
                return Route("control", target, utterance), Reply(
                    text=f"Dropped. Nothing was sent to {target}.\n> {_clip(dropped)}",
                    subtype="control",
                )
            else:
                # Not an answer: he has moved on. Discard the held message rather
                # than delivering something he has stopped meaning to send.
                conv.held = conv.held_for = None

        route = parse_utterance(utterance)

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
            # The id first: it outlives a rename and a new pid, both of which
            # happen to a long-running session.
            target, mode = (conv.attached_id or conv.attached_to), "attach"
            # Resolve by id, report by name: a caller being told it is attached to
            # "bbb" learns nothing.
            display = conv.attached_to
        elif route.mode != "fresh" and route.target:
            target, mode = route.target, route.mode
            display = target
        else:
            if len(self.conversations) > self.max_sessions:
                await self._evict_oldest()
            async with conv.lock:
                target = await self._own_session(conv, route.text)
            mode = "own"
            display = target

        # Where it is going, said before it goes. Sticky per target: once he has
        # confirmed a session, messages flow until the target changes underneath
        # him, which is the event worth catching rather than every message.
        if key in self.confirm_keys and display != conv.confirmed:
            conv.held, conv.held_for = route.text, display
            conv.last_used = time.monotonic()
            self._save_bindings()
            where = {
                "attach": f"You are talking to **{display}** (connected).",
                "own": f"This would start **{display}**, this channel's own session.",
            }.get(mode, f"This would go to **{display}**.")
            return Route("control", display, route.text), Reply(
                text=(
                    f"{where}\nSend it?  *yes* / *no* — or just type something else "
                    f"to replace it.\n> {_clip(route.text)}"
                ),
                subtype="control",
            )

        try:
            reply = await self._send(conv, target, route.text, narrator, timeout, origin)
        except SessionNotFound:
            # The session this conversation was pointed at is gone. Say so; do not
            # quietly substitute a stranger, which is the whole of tofix #8.
            if target in (conv.attached_to, conv.attached_id):
                conv.attached_to = conv.attached_id = None
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
        return Route(mode, display, route.text), reply

    async def _send(
        self,
        conv: Conversation,
        target: str,
        text: str,
        narrator: Narrator | None,
        timeout: float,
        origin: Origin | None = None,
    ) -> Reply:
        """Deliver, and either wait for the answer or hand the caller to a stand-in.

        The split matters. Delivery is fast and either happened or did not; the
        answer may be minutes away. When the target is mid-turn, waiting on it
        gives the sender silence indistinguishable from the message being lost, so
        instead a stand-in reports on it immediately and the real answer is relayed
        when it lands.
        """
        watch = await self.router.deliver(target, text, origin)

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
`agents` — who is working on what, and who has finished
`new agent <task>` — start a SEPARATE agent with its own session and channel
`resume` — agents you can bring back, numbered; `resume 2` or `resume <name>`

Every message relayed into a session now carries a provenance header saying
where it came from. A message relayed from here carries the Discord channel and
message id, so the agent can run `hotline --provenance -` and have Discord
itself confirm you posted it. A message from another agent is labelled as such
and as not an authorization channel.
`new session` — close *this channel's* session and start over. Note it is not
   how you get a second agent: this channel's session is named after the channel,
   so a replacement is the same session again. Use `new agent` for that.

In #general, a message is held and you are told where it is going before it goes
— answer `yes` to send, `no` to drop, or just type something else to replace it.
You are asked once per target, not once per message; connecting somewhere new
asks again. Per-agent channels never ask: that channel *is* that agent.

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
