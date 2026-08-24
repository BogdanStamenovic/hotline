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
from dataclasses import dataclass
from time import monotonic

from .ccsocks import LiveSession, discover, inject, status_of
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
from .stops import stop_stamp
from .transcript import read_since, size_of

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


@dataclass
class Route:
    mode: str  # fresh | attach | agent
    target: str | None
    text: str


def parse_utterance(utterance: str) -> Route:
    """Work out what the caller wants from how they opened the call.

    Deliberately conservative: anything that isn't clearly a routing phrase is
    treated as a question for a fresh session, because the cost of guessing
    "attach" wrongly is a command landing in the wrong session.
    """
    text = utterance.strip()
    low = text.lower()

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
        return Route("attach", "newest", "What are you working on right now?")

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

        exact = [s for s in live if s.name.lower() == want]
        if len(exact) == 1:
            return exact[0]

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

    async def ask_fresh(
        self,
        text: str,
        narrator: Narrator | None = None,
        cwd: str | None = None,
        timeout: float = DEFAULT_REPLY_TIMEOUT,
    ) -> Reply:
        async with FreshSession(cwd or self.default_cwd, bypass=self.bypass) as session:
            return await session.ask(text, narrator=narrator, timeout=timeout)

    async def ask_session(
        self,
        spec: str,
        text: str,
        narrator: Narrator | None = None,
        timeout: float = DEFAULT_REPLY_TIMEOUT,
    ) -> Reply:
        """Inject into a live session and read its reply back out of the transcript.

        The two baselines taken before injecting are what make this safe when the
        target is already mid-turn: the stop that fires next may belong to the turn
        already in flight, so we keep waiting until the transcript actually shows
        our own message followed by an answer.
        """
        session = self.resolve(spec)
        sid = session.session_id
        offset = size_of(sid)
        stamp = stop_stamp(sid)

        await inject(session, text)

        deadline = monotonic() + timeout
        last_size = size_of(sid)
        last_change = monotonic()
        saw_marker = False

        while monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)

            size = size_of(sid)
            if size != last_size:
                last_size, last_change = size, monotonic()

            stopped = stop_stamp(sid) > stamp
            quiet = (
                monotonic() - last_change >= QUIET_SECONDS
                and status_of(session.pid) in (None, "idle")
            )
            if not (stopped or quiet):
                continue

            turn = read_since(sid, offset, marker=text)
            saw_marker = saw_marker or turn.saw_marker
            if turn.saw_marker and turn.text:
                reply = Reply(text=turn.text, session_id=sid, subtype="attached")
                reply.events = [Event("tool", name, tool=name) for name in turn.tools]
                if narrator is not None:
                    for event in reply.events:
                        narrator(event)
                return reply
            if stopped:
                stamp = stop_stamp(sid)

        raise ReplyTimeout(
            f"{describe(session)} did not produce a reply within {timeout:.0f}s. "
            + (
                "It did receive the message, so it is still working or it stopped "
                "without answering."
                if saw_marker
                else "The message never reached its transcript -- it is most likely "
                'being held. Set "crossSessionInbound": "accept" in '
                "~/.claude/settings.json, or approve it in that session's UI."
            )
        )

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
