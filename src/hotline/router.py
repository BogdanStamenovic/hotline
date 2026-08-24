"""The session router: one entry point, three ways to reach Claude.

    fresh   a new persistent `claude` subprocess (default)
    attach  inject into a session already running in front of Bogdan
    agent   a named standing session -- same mechanism as attach, different naming

Everything above this layer speaks text and knows nothing about sockets,
transcripts or subprocesses. That is the whole point: adding a transport later
(SIP, a web page, a paid number) is a new adapter, not a rewrite.

Naming deserves a word. Sessions get *derived* names like `data-d6` and
`hotline-ac`, which nobody is going to say out loud correctly, least of all through
a speech recogniser. So `resolve()` accepts a pid, a session-id prefix, an exact
name, a fuzzy name, the working directory ("the one in uxonews"), and ordinals
("the older one", "the newest") -- because in a voice call the natural way to pick
a session is by where it is or when it started, not by a two-letter suffix.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from time import monotonic

from .ccsocks import LiveSession, discover, inject, status_of, terminate
from .config import DEFAULT_REPLY_TIMEOUT, POLL_INTERVAL, QUIET_SECONDS
from .errors import (
    AmbiguousSession,
    ClaudeLaunchFailed,
    HotlineError,
    InjectFailed,
    ReplyTimeout,
    SessionNotFound,
)
from .fresh import Event, FreshSession, Narrator, Reply
from .provenance import Origin
from .stops import stop_stamp
from .transcript import read_since, size_of, transcript_path, turn_in_flight

__all__ = [
    "AmbiguousSession",
    "ClaudeLaunchFailed",
    "Event",
    "HotlineError",
    "InjectFailed",
    "Reply",
    "ReplyTimeout",
    "Route",
    "Router",
    "SessionNotFound",
    "Watch",
    "describe",
    "parse_utterance",
]

# Spoken filler that carries no selection information. Stripped before matching so
# "the one in uxonews" and "uxonews" resolve identically.
_FILLER = re.compile(
    r"^(?:the\s+)?(?:session\s+|one\s+|agent\s+)?(?:called\s+|named\s+|in\s+|at\s+|for\s+)?",
    re.IGNORECASE,
)
_TRAILING_FILLER = re.compile(r"\s+(?:one|session|agent)$", re.IGNORECASE)
_NEWEST = {"newest", "latest", "newer", "last", "most recent", "recent", "current"}
_OLDEST = {"oldest", "older", "first", "earliest", "original"}
_ORDINALS = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}

# How long after its last transcript write a session still counts as working. Long
# enough to span a slow tool call, short enough that a session abandoned mid-turn
# stops being described as busy forever.
MID_TURN_WINDOW = 120.0
# Grace for a stop event that fired just before its own text was flushed.
SETTLE_SECONDS = 0.6


@dataclass
class Watch:
    """A message that has been put in a session's inbox, and where to look for the reply."""

    session: LiveSession
    offset: int
    stamp: float
    marker: str
    # Whether the target was mid-turn when we injected. The sender is owed a very
    # different answer in that case -- see `hotline.standin`.
    was_busy: bool = False
    saw_marker: bool = False


@dataclass
class Route:
    mode: str  # fresh | attach | agent | control
    target: str | None
    text: str
    # Only set for mode == "control": list | connect | detach | where | kill
    #                               | help | resources
    action: str | None = None
    # True when the target was inferred from phrasing rather than named. An
    # explicit `connect` beats an inference -- otherwise "what are you working
    # on?" would jump to the newest session even while you are deliberately
    # connected to an older one.
    implicit: bool = False


# Control phrases, checked before anything else. Deliberately a small closed set
# rather than fuzzy matching: "list the files in this directory" must reach a
# session, not be swallowed as a control command.
_LIST = re.compile(r"^(?:session\s*list|list\s+sessions?|sessions?|what\s+sessions?"
                   r"(?:\s+are)?(?:\s+(?:there|running|live|open))?)\s*[:.?]?$", re.IGNORECASE)
_CONNECT = re.compile(r"^(?:connect(?:\s+to)?|switch\s+to|use)\s+(.+?)\s*[.?]?$", re.IGNORECASE)
_DETACH = re.compile(r"^(?:detach|disconnect|leave|never\s*mind|new\s+session)\s*[.?]?$", re.IGNORECASE)
_WHERE = re.compile(r"^(?:where\s+am\s+i|who\s+am\s+i\s+talking\s+to|what\s+am\s+i"
                    r"\s+connected\s+to)\s*[.?]?$", re.IGNORECASE)

_AGENTS = re.compile(
    r"^(?:agents?|who(?:'?s| is)\s+working(?:\s+on\s+what)?|what(?:\s+is|'?s)\s+"
    r"everyone\s+doing)\s*[.?]?$", re.IGNORECASE
)
_HELP = re.compile(r"^(?:help|commands?|what\s+can\s+(?:you|i)\s+do)\s*[.?!]?$", re.IGNORECASE)
# Bare `resume` lists what can be brought back; `resume <n|name>` brings one back.
# The target form deliberately overlaps with attaching to a live session, and the
# ambiguity is resolved where the data is: if it names something running, that is
# a connect, and only otherwise is it a revive. Bogdan typed a bare "resume" in
# #general expecting exactly this and got a brand new session instead, because
# nothing matched it and it fell through as a question.
_RESUME = re.compile(r"^(?:resume|revive|bring\s+back)(?:\s+(?:agent\s+)?(.+?))?\s*[.?]?$",
                     re.IGNORECASE)
_RESOURCES = re.compile(
    r"^(?:resources?|load|how\s+(?:much|is)\s+(?:ram|memory|vram|load)\S*)\s*[.?]?$", re.IGNORECASE
)
# `kill` has to be an explicit verb with an explicit target. No fuzzy synonyms and
# no bare "kill" -- this ends a process someone may be sitting in front of, and it
# is the one command here where a generous match is a bug rather than a kindness.
_KILL = re.compile(
    r"^(?:kill|terminate)\s+(?:session\s+)?(.+?)\s*[.?]?$"
    r"|^session\s+(?:kill|terminate|stop|end)\s+(.+?)\s*[.?]?$",
    re.IGNORECASE,
)

def parse_utterance(utterance: str) -> Route:
    """Work out what the caller wants from how they opened the call.

    Deliberately conservative: anything that isn't clearly a routing phrase is
    treated as a question for a fresh session, because the cost of guessing
    "attach" wrongly is a command landing in the wrong session.
    """
    text = utterance.strip()
    low = text.lower()

    # Control first. These are about the connection itself, not questions for
    # whatever is on the other end of it.
    if _HELP.match(low):
        return Route("control", None, text, action="help")
    resume = _RESUME.match(text)
    if resume:
        target = (resume.group(1) or "").strip()
        return Route("control", target or None, text, action="resume")
    if _RESOURCES.match(low):
        return Route("control", None, text, action="resources")
    if _AGENTS.match(low):
        return Route("control", None, text, action="agents")
    if _LIST.match(low):
        return Route("control", None, text, action="list")
    kill = _KILL.match(text)
    if kill:
        target = kill.group(1) or kill.group(2) or ""
        return Route("control", target.strip(), text, action="kill")
    if _DETACH.match(low):
        return Route("control", None, text, action="detach")
    if _WHERE.match(low):
        return Route("control", None, text, action="where")
    connect = _CONNECT.match(text)
    if connect:
        return Route("control", connect.group(1).strip(), text, action="connect")

    match = re.match(r"^(?:please\s+)?(?:can you\s+)?(?:join|attach to|connect to|resume)\s+(.+)$", low)
    if match:
        target, rest = _split_target(text[match.start(1) :])
        return Route("attach", target, rest)

    match = re.match(r"^(?:please\s+)?ask\s+(.+)$", low)
    if match:
        target, rest = _split_target(text[match.start(1) :])
        return Route("agent", target, rest)

    if re.match(r"^(?:start a\s+)?new (?:session|chat|conversation)\b[,.]?\s*", low):
        return Route("fresh", None, re.sub(r"^[^,.]*[,.]?\s*", "", text, count=1))

    if re.match(r"^what (?:are|is) you(?:r|'re)?\s+working on\b", low):
        return Route("attach", "newest", "What are you working on right now?", implicit=True)

    return Route("fresh", None, text)


def _split_target(tail: str) -> tuple[str, str]:
    """Separate the session name from the question that follows it.

    A comma is the reliable boundary in dictated speech ("join data-13, what's
    failing"). Without one, take the first token as the name -- session names never
    contain spaces.
    """
    if "," in tail:
        head, rest = tail.split(",", 1)
        return head.strip(), rest.strip()
    parts = tail.split(None, 1)
    return (parts[0].strip(), parts[1].strip() if len(parts) > 1 else "")


def mid_turn(session: LiveSession, window: float = MID_TURN_WINDOW) -> bool:
    """Is this session in the middle of a turn right now?

    Two cheaper signals were tried first and both were wrong, which is worth
    recording because both looked obviously right.

    The descriptor's `status` cannot answer this: a session started in tmux
    reports "waiting" from the moment it boots until it dies, unchanged through a
    full twenty-five second tool call. And "wrote recently, with no stop recorded
    since that write" fails because the Stop hook fires *before* the turn's final
    transcript write -- so it called every session busy for two minutes after
    every turn, and the very first live `session kill` got a stand-in instead.

    So: ask the transcript whether the last thing a person said has been answered,
    and bound it by a window so a session abandoned mid-turn does not stay
    "working" forever.
    """
    if session.status == "busy":
        return True
    path = transcript_path(session.session_id)
    if path is None:
        return False
    try:
        if time.time() - path.stat().st_mtime > window:
            return False
    except OSError:
        return False
    return turn_in_flight(session.session_id)


def describe(session: LiveSession) -> str:
    return f"{session.name} (pid {session.pid}, {session.cwd})"


class Router:
    def __init__(self, default_cwd: str | None = None, bypass: bool = True) -> None:
        self.default_cwd = default_cwd
        self.bypass = bypass

    # ---- resolution -----------------------------------------------------

    def sessions(self) -> list[LiveSession]:
        return discover()

    def resolve(self, spec: str) -> LiveSession:
        live = self.sessions()
        if not live:
            raise SessionNotFound("no live Claude sessions on this machine")

        want = _FILLER.sub("", spec.strip(), count=1).strip().strip("'\"").lower()
        want = want.rstrip(".,!?;:")
        # "the newest one" survives the leading-filler strip as "newest one".
        want = _TRAILING_FILLER.sub("", want).strip()
        if not want:
            raise SessionNotFound("no session named in that request")

        if want in _NEWEST:
            return live[0]
        if want in _OLDEST:
            return live[-1]
        if want in _ORDINALS:
            index = _ORDINALS[want]
            if index >= len(live):
                raise SessionNotFound(f"there is no {want} session; {len(live)} are live")
            return live[index]

        if want.isdigit():
            for session in live:
                if session.pid == int(want):
                    return session

        # Spoken names arrive with spaces where the real name has hyphens:
        # "connect to data thirteen" is transcribed "Connect to Data 13", and
        # "data 13" must find `data-13`. Cheap, and the alternative is telling
        # Bogdan to pronounce punctuation.
        for candidate in (want, want.replace(" ", "-"), want.replace(" ", "")):
            exact = [s for s in live if s.name.lower() == candidate]
            if len(exact) == 1:
                return exact[0]

        # The tmux session name is what `where am i` and `session list` hand the
        # caller ("tmux attach -t hl-final"), so it is the name they will say back.
        by_tmux = [s for s in live if (s.tmux_session or "").lower() == want]
        if len(by_tmux) == 1:
            return by_tmux[0]

        by_id = [s for s in live if s.session_id.lower().startswith(want)]
        if len(by_id) == 1:
            return by_id[0]

        # Fuzzy, in decreasing order of how much the caller probably meant it.
        for candidates in (
            [s for s in live if s.name.lower().startswith(want)],
            [s for s in live if s.cwd_leaf.lower() == want],
            [s for s in live if want in s.cwd.lower()],
            [s for s in live if want in s.name.lower()],
        ):
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise AmbiguousSession(
                    f"'{spec}' matches {len(candidates)}: "
                    + ", ".join(describe(c) for c in candidates)
                )

        raise SessionNotFound(
            f"no live session matches '{spec}'. Live: "
            + (", ".join(describe(s) for s in live) or "none")
        )

    # ---- the three modes ------------------------------------------------

    # The pipe-driven transport, kept for exactly one caller: the one-shot CLI.
    # Conversations use tmux sessions (see `hotline.tmuxen`) because they need to
    # be attachable, killable and long-lived. A single `hotline "what is X"` needs
    # none of that, and spawning a pane per question would leave litter behind for
    # every one-liner. Nothing else should use this.
    async def ask_fresh(
        self,
        text: str,
        narrator: Narrator | None = None,
        cwd: str | None = None,
        timeout: float = DEFAULT_REPLY_TIMEOUT,
    ) -> Reply:
        async with FreshSession(cwd or self.default_cwd, bypass=self.bypass) as session:
            return await session.ask(text, narrator=narrator, timeout=timeout)

    async def deliver(self, spec: str, text: str, origin: Origin | None = None) -> Watch:
        """Resolve a session, put the message in its inbox, and hand back a receipt.

        Split out from the waiting half deliberately. Delivery either happened or
        it did not, and that is knowable in milliseconds -- whereas the *answer*
        may be five minutes away if the target is mid-turn. Conflating the two is
        what made a message to a busy session look identical to a message that
        went nowhere.

        The two baselines are taken before injecting, not after: if the target is
        already working, the next stop event belongs to the turn in flight and its
        reply is not ours.
        """
        session = self.resolve(spec)
        # The header goes on before the marker is taken, because the marker is
        # how the reply is found in the transcript and the transcript will hold
        # what was actually delivered, header and all.
        wire = origin.wrap(text) if origin is not None else text
        watch = Watch(
            session=session,
            offset=size_of(session.session_id),
            stamp=stop_stamp(session.session_id),
            marker=wire,
            was_busy=mid_turn(session),
        )
        await inject(session, wire)
        return watch

    async def collect(
        self,
        watch: Watch,
        narrator: Narrator | None = None,
        timeout: float = DEFAULT_REPLY_TIMEOUT,
    ) -> Reply:
        """Wait for the delivered message to be answered, narrating as it goes.

        Narration used to exist only for fresh sessions, because only the
        stream-json transport emitted events. But the transcript is being appended
        to the whole time a turn runs, so the same events are readable here -- and
        an attached session doing four minutes of work is exactly where a caller
        most needs to hear that something is happening.
        """
        session = watch.session
        sid = session.session_id
        deadline = monotonic() + timeout
        last_size = size_of(sid)
        last_change = monotonic()
        narrated = 0
        stamp = watch.stamp
        settled = False

        while monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)

            size = size_of(sid)
            grew = size != last_size
            if grew:
                last_size, last_change = size, monotonic()

            turn = read_since(sid, watch.offset, marker=watch.marker) if grew else None
            if turn is not None:
                watch.saw_marker = watch.saw_marker or turn.saw_marker
                if narrator is not None and turn.saw_marker:
                    for name in turn.tools[narrated:]:
                        narrator(Event("tool", name, tool=name))
                narrated = max(narrated, len(turn.tools))

            # `stamp` is never advanced. The stop hook can fire a beat before the
            # final assistant text reaches the transcript, and consuming the stop
            # in that window disarms the only reliable completion signal we have:
            # the loop then spins until the *next* turn's stop and hands this
            # caller that turn's answer. Live, one turn waited 226 seconds and
            # returned the reply to a different question. Leaving the stop armed
            # costs nothing -- `saw_marker` plus non-empty text is what actually
            # decides, and a stop belonging to an earlier turn yields neither.
            stopped = stop_stamp(sid) > stamp
            # Quiescence stays narrow on purpose. It is the fallback for sessions
            # with no Stop hook, and it must never be widened to cover "waiting":
            # a tmux-spawned session reports "waiting" for its entire life,
            # including mid-turn, so accepting it returned the model's opening
            # preamble as the answer eight seconds into a twenty-five second job.
            quiet = (
                monotonic() - last_change >= QUIET_SECONDS
                and status_of(session.pid) in (None, "idle")
            )
            if not (stopped or quiet):
                continue

            if stopped and not settled:
                # Let a just-fired stop finish landing its text before reading.
                settled = True
                await asyncio.sleep(SETTLE_SECONDS)

            turn = read_since(sid, watch.offset, marker=watch.marker)
            watch.saw_marker = watch.saw_marker or turn.saw_marker
            # `in_flight` is the guard that was missing. Leaving the stop armed
            # fixed a turn being handed another turn's answer, and introduced a
            # quieter failure in its place: a stop landing at or just after
            # injection latches `stopped` forever, so the loop returned the first
            # text the target emitted -- the opening sentence of a turn that had
            # barely started. A sender gets a plausible paragraph and assumes it
            # is the whole answer.
            if turn.saw_marker and turn.text and not turn.in_flight:
                reply = Reply(text=turn.text, session_id=sid, subtype="attached")
                reply.events = [Event("tool", name, tool=name) for name in turn.tools]
                return reply

        raise ReplyTimeout(
            f"{describe(session)} did not produce a reply within {timeout:.0f}s. "
            + (
                "It did receive the message, so it is still working or it stopped "
                "without answering."
                if watch.saw_marker
                else "The message never reached its transcript -- it is most likely "
                'being held. Set "crossSessionInbound": "accept" in '
                "~/.claude/settings.json, or approve it in that session's UI."
            )
        )

    async def ask_session(
        self,
        spec: str,
        text: str,
        narrator: Narrator | None = None,
        timeout: float = DEFAULT_REPLY_TIMEOUT,
        origin: Origin | None = None,
    ) -> Reply:
        """Inject into a live session and read its reply back out of the transcript."""
        watch = await self.deliver(spec, text, origin)
        return await self.collect(watch, narrator=narrator, timeout=timeout)

    async def kill_session(self, spec: str) -> str:
        """`session kill <name-or-ref>`, resolving the same way everything else does."""
        session = self.resolve(spec)
        outcome = await terminate(session)
        return f"{describe(session)} {outcome}."

    async def ask(
        self,
        utterance: str,
        narrator: Narrator | None = None,
        timeout: float = DEFAULT_REPLY_TIMEOUT,
    ) -> tuple[Route, Reply]:
        route = parse_utterance(utterance)
        if route.mode == "fresh" or route.target is None:
            return route, await self.ask_fresh(route.text, narrator=narrator, timeout=timeout)
        return route, await self.ask_session(
            route.target, route.text, narrator=narrator, timeout=timeout
        )
