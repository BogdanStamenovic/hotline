"""Bringing an agent back after its session is gone.

Shared by the CLI's `--resume` and the Discord `resume` command, because they
are the same operation reached from two places and they must not drift: a revive
that keeps the channel from one entry point and orphans it from the other is
worse than either behaviour consistently.

The interesting decision is what to seed the replacement with. An agent that
finished on purpose left a handoff, and that is the better brief. An agent that
was *killed* left nothing -- and those are the ones most worth reviving, so
"no handoff" has to mean "read the transcript" rather than "refuse". The
transcript is always there, which makes every agent recoverable in principle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import Agent, Registry
from .errors import HotlineError
from .transcript import transcript_path


class NoSuchAgent(HotlineError):
    """No agent by that name is registered."""


class NothingToResumeFrom(HotlineError):
    """The agent exists, but left neither a handoff nor a readable transcript."""


@dataclass
class Brief:
    """What a replacement is handed, and where it came from."""

    seed: str
    # False when this was reconstructed from a transcript rather than read from a
    # handoff. Callers say so out loud: a revived agent working from a corpse is
    # in a materially weaker position than one working from a handoff, and hiding
    # that would let it be trusted more than it deserves.
    from_handoff: bool


def brief_for(agent: Agent) -> Brief | None:
    """The seed message for a replacement, or None if nothing survives."""
    if agent.handoff:
        try:
            text = Path(agent.handoff).read_text()
        except OSError:
            text = ""
        if text:
            return Brief(
                seed=(
                    f"You are resuming work that a previous session finished a "
                    f"stint on. Its task was: {agent.task}\n\n"
                    f"This is the handoff it left at {agent.handoff}:\n\n{text}\n\n"
                    "Read it, say in one sentence what you understand the state "
                    "to be, and wait."
                ),
                from_handoff=True,
            )

    corpse = transcript_path(agent.session_id)
    if corpse is None:
        return None
    return Brief(
        seed=(
            "You are taking over work from a session that was KILLED before it "
            "could write a handoff, so there is no summary -- only the raw "
            f"record.\n\nIts task was: {agent.task}\n\n"
            f"Its full transcript is at {corpse}. Read it (it is JSONL and may be "
            "large, so parse it with python rather than cat), and separately "
            "verify the actual state of the system against what it *claimed* -- "
            "it died mid-flight and its last actions may not have completed. Then "
            "write a handoff of your own before continuing, so this cannot happen "
            "twice.\n\nSay in one sentence what you understand the state to be, "
            "and wait."
        ),
        from_handoff=False,
    )


def resumable(registry: Registry, live_ids: set[str], limit: int = 10) -> list[Agent]:
    """Agents worth offering to bring back, newest first.

    An agent whose session is still alive is not resumable -- it is running, and
    the thing to do with it is `connect`. Everything else is fair game whether it
    finished tidily or was killed, because both leave something to resume from.
    """
    candidates = [
        a
        for a in registry.agents.values()
        if a.session_id not in live_ids and brief_for(a) is not None
    ]
    # A standing role sorts first, always. Bogdan asked for it explicitly -- "if
    # i do resume you are always on the top of the list" -- and the reasoning
    # holds independently: the sys-admin agent is the one that manages the
    # others, so it is the one to bring back before deciding anything else.
    candidates.sort(key=lambda a: (a.privileged, a.completed_at or a.declared_at), reverse=True)
    return candidates[:limit]


def rehome(
    registry: Registry,
    agent: Agent,
    new_session_id: str,
    manager: Any = None,
) -> Agent:
    """Move an agent's record onto the session now doing its work.

    The old record is retired rather than edited so the revived agent gets its
    own retention clock. The channel is the exception: a killed agent still owns
    a live one, and it is the thread Bogdan has been reading, so it is carried
    over rather than replaced. Only a channel that is genuinely gone -- the case
    `--done` creates -- earns a new one.
    """
    old_channel = agent.channel_id
    registry.forget(agent.session_id)
    revived = registry.declare(
        # `agent.name`, not the new session's: resuming by name and getting back
        # something called `hotline-36` loses the identity you resumed.
        new_session_id,
        agent.name,
        agent.task,
        parent=agent.parent,
        wants_channel=agent.wants_channel,
        keep_days=agent.keep_days,
    )
    if manager is None or not revived.wants_channel:
        return revived
    if old_channel is not None and manager.exists(old_channel):
        revived.channel_id = old_channel
    else:
        revived.channel_id = manager.create_text(revived.name, topic=revived.task)
    registry.save()
    return revived


@dataclass
class Resumed:
    """A revived agent: what came back, and everything a caller has to report.

    Every field is something a caller would otherwise have to re-derive, and two
    of them are things a caller must not quietly drop. `from_handoff` says
    whether the replacement is working from a summary or from a corpse, and
    `channel_error` says the channel could not be sorted out -- reporting the
    resume as a plain success while either is unwelcome news is how a revived
    agent ends up trusted more than it deserves.
    """

    agent: Agent
    session: Any  # ccsocks.LiveSession
    brief: Brief
    tmux: str
    kept_channel: bool
    channel_error: str | None = None

    @property
    def from_handoff(self) -> bool:
        return self.brief.from_handoff


async def resume(
    name: str,
    registry: Registry,
    *,
    cwd: str | None = None,
    channels: Any = None,
) -> Resumed:
    """Bring an agent back: a new session, seeded with what survives of the old.

    This is the counterweight to disposable channels. Deleting the channel on
    completion means the handoff is the only thing that survives, so there has
    to be a way to turn that back into a working agent -- otherwise "done" is
    indistinguishable from "lost". An agent that was *killed* never wrote a
    handoff at all, which is why the transcript is the fallback rather than an
    error.

    A new session, not the old one: the old process is gone. What continues is
    the work.

    **It does not deliver the brief.** That is where the CLI and the daemon
    genuinely differ -- the CLI waits up to five minutes for an answer and
    prints it, while an HTTP request from a phone cannot -- and it is the only
    thing they differ on. Everything above it is shared, because two copies of
    "spawn, rehome, keep the channel" would be two chances to drift on what
    resuming means.
    """
    from . import tmuxen

    agent = registry.by_name(name)
    if agent is None:
        raise NoSuchAgent(f"no agent called {name!r}")
    brief = brief_for(agent)
    if brief is None:
        raise NothingToResumeFrom(
            f"{agent.name} left no handoff and its transcript is gone, "
            "so there is nothing to resume it from"
        )

    session = await tmuxen.spawn(agent.name, cwd=cwd or None, name=agent.name)

    had = agent.channel_id
    channel_error: str | None = None
    try:
        revived = rehome(registry, agent, session.session_id, channels)
    except HotlineError as exc:
        # A Discord that will not answer must not cost him the session that is
        # already running. The record is repaired without a channel and the
        # failure is carried out rather than logged and forgotten.
        channel_error = str(exc)
        revived = registry.declare(session.session_id, agent.name, agent.task)

    return Resumed(
        agent=revived,
        session=session,
        brief=brief,
        tmux=tmuxen.tmux_name(agent.name),
        kept_channel=revived.channel_id is not None and revived.channel_id == had,
        channel_error=channel_error,
    )
