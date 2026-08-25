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
from .transcript import transcript_path


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
