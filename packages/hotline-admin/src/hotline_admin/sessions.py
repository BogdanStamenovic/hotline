"""`--adopt` and `--declare` -- putting a session on the roster and taking one over.

These are the spawn-time half of the admin surface: `--declare` registers a
session and what it is working on (and gives it a Discord channel), `--adopt`
lets a respawned session take over a dead one's identity and channel. Both are
moved out of `hotline.cli` with their behaviour unchanged.

Why they are admin rather than core, given that every agent runs `--adopt` at
startup: what they operate on is the *roster* -- who exists, what they were
told to do, who spawned whom. That is the operator's model of the machine, not
the call/page/Discord plumbing `hotline` core is now supposed to be. Core still
declares the flags, so `hotline --adopt X` on an install without this plugin
parses and gets a clear error instead of argparse's "unrecognized arguments";
only the implementation lives out here.

The channel handling in `declare_session` is the fiddly part and is kept
verbatim: a declaration that cannot reach Discord still registers the session.
Failing the declaration over a missing channel would leave the session
unregistered, which is strictly worse than a registered session with no channel.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable

from hotline_claude.agents import Registry

from hotline.channels import from_env as channels_from_env
from hotline.channels import slug as channel_slug
from hotline.errors import HotlineError


def adopt_session(
    name: str, session_id: str, registry: Registry, log: Callable[[str], None]
) -> int:
    """`--adopt NAME` -- take over a running agent's identity and channel."""
    adopted = registry.adopt(name, session_id)
    if adopted is None:
        # stderr rather than `log`, which `--quiet` suppresses: this is an error,
        # and it names the two ways out because the caller is a session that has
        # just started and cannot do anything else until it has an identity.
        print(
            f"hotline: error: no agent called {name!r} to adopt. "
            "Use --agents to see them, or --declare to start a new one.",
            file=sys.stderr,
        )
        return 1
    log(f"adopted: {adopted.describe()}")
    if adopted.channel_id is None:
        log("it had no channel; use --declare if you want one")
    else:
        log(f"channel: #{channel_slug(adopted.name)}")
    return 0


def declare_session(
    task: str,
    session_id: str,
    name: str,
    *,
    parent: str | None,
    wants_channel: bool,
    keep_days: int,
    registry: Registry,
    log: Callable[[str], None],
) -> int:
    """`--declare TASK` -- register this session and what it is working on.

    `name` is resolved by core before we are called: deriving it means asking
    the live control sockets which session this is, which is session plumbing
    rather than roster bookkeeping.
    """
    agent = registry.declare(
        session_id,
        name,
        task,
        parent=parent,
        wants_channel=wants_channel,
        keep_days=keep_days,
    )
    log(f"declared: {agent.describe()}")
    if agent.wants_channel and agent.channel_id is None:
        manager = channels_from_env()
        if manager is None:
            log("no Discord configured; skipping the channel")
        else:
            try:
                agent.channel_id = manager.create_text(agent.name, topic=agent.task)
                registry.save()
                log(f"channel: #{channel_slug(agent.name)}")
            except HotlineError as exc:
                # The agent is registered either way. Losing its channel is
                # worth saying out loud, but it is not worth failing the
                # declaration and leaving the session unregistered.
                log(f"warning: could not create the channel: {exc}")
    elif agent.channel_id is not None:
        manager = channels_from_env()
        if manager is not None:
            with contextlib.suppress(HotlineError):
                manager.retopic(agent.channel_id, agent.task)
    return 0


def register_adopt() -> Callable[[str, str, Registry, Callable[[str], None]], int]:
    """Entry point hook: hand `hotline.cli` the callable it dispatches `--adopt` to."""
    return adopt_session


def register_declare() -> Callable[..., int]:
    """Entry point hook: hand `hotline.cli` the callable it dispatches `--declare` to."""
    return declare_session
