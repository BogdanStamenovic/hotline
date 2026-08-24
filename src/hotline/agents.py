"""Who each session is, what it is doing, and when it is finished.

Every agent declares itself at the start of its session -- a task statement it
can rewrite whenever the work turns out to be something else -- and says `done`
when it is finished. Between those two points it owns a Discord channel; after
`done` it writes a handoff and the channel is deleted, and the record of it is
kept for three days.

**Three days from completion, not from creation.** An agent that ran for a week
is exactly the one whose record you want afterwards, and dating the retention
from when it started would throw it away first.

**The registry is durable, not runtime state.** It lives under `XDG_STATE_HOME`
rather than the /run spool the rest of hotline uses. A stop event from before a
reboot is meaningless and should be cleared; an agent that finished yesterday
still has two days left and a channel that still needs deleting.

Nothing here talks to Discord. The registry decides *what* should exist and what
is due for deletion; carrying that out is somebody else's job, which is what
makes the lifecycle testable without a guild.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import agents_file

DEFAULT_KEEP_DAYS = 3.0


@dataclass
class Agent:
    """One session's self-declared identity and lifecycle."""

    session_id: str
    name: str
    task: str
    declared_at: float = field(default_factory=time.time)
    # Set by `done`, and only by `done` -- completion is explicit. A session that
    # merely exited has not finished its work, it has stopped doing it, and those
    # deserve different treatment.
    completed_at: float | None = None
    handoff: str | None = None
    channel_id: int | None = None
    voice_channel_id: int | None = None
    # The session that spawned this one, for subagents.
    parent: str | None = None
    # "Every agent gets a channel, except if explicitly asked" -- this is that
    # exception, and it is per-agent rather than a global setting because the
    # thing being suppressed is one noisy fan-out, not the feature.
    wants_channel: bool = True
    keep_days: float = DEFAULT_KEEP_DAYS

    @property
    def done(self) -> bool:
        return self.completed_at is not None

    @property
    def expires_at(self) -> float | None:
        if self.completed_at is None:
            return None
        return self.completed_at + self.keep_days * 86400

    def describe(self) -> str:
        state = "done" if self.done else "working"
        line = f"{self.name} [{state}] — {self.task}"
        if self.parent:
            line += f" (subagent of {self.parent})"
        return line


class Registry:
    """The set of agents hotline knows about, persisted across restarts."""

    def __init__(self, path: Any = None) -> None:
        self.path = path or agents_file()
        self.agents: dict[str, Agent] = {}
        self.load()

    # ---- persistence ----------------------------------------------------

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        for entry in raw.get("agents", []):
            try:
                agent = Agent(**entry)
            except TypeError:
                # A record written by a newer version with fields we do not know.
                # Skipping one is better than refusing to load the whole registry
                # and silently forgetting every agent on the machine.
                continue
            self.agents[agent.session_id] = agent

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"agents": [asdict(a) for a in self.agents.values()]}))
            os.replace(tmp, self.path)
        except OSError:
            pass

    # ---- the lifecycle --------------------------------------------------

    def declare(
        self,
        session_id: str,
        name: str,
        task: str,
        parent: str | None = None,
        wants_channel: bool = True,
        keep_days: float = DEFAULT_KEEP_DAYS,
    ) -> Agent:
        """Register an agent, or re-declare one that already exists.

        Re-declaring is a retask rather than a reset: the channel it already owns
        and the day it started are kept, because a session that reframes its work
        halfway through is the same agent doing the same job.
        """
        existing = self.agents.get(session_id)
        if existing is not None:
            existing.name = name or existing.name
            existing.task = task
            existing.completed_at = None
            self.save()
            return existing
        agent = Agent(
            session_id=session_id,
            name=name,
            task=task,
            parent=parent,
            wants_channel=wants_channel,
            keep_days=keep_days,
        )
        self.agents[session_id] = agent
        self.save()
        return agent

    def retask(self, session_id: str, task: str) -> Agent | None:
        agent = self.agents.get(session_id)
        if agent is None:
            return None
        agent.task = task
        self.save()
        return agent

    def complete(self, session_id: str, handoff: str | None = None) -> Agent | None:
        agent = self.agents.get(session_id)
        if agent is None:
            return None
        agent.completed_at = time.time()
        agent.handoff = handoff
        self.save()
        return agent

    def forget(self, session_id: str) -> None:
        if self.agents.pop(session_id, None) is not None:
            self.save()

    # ---- queries --------------------------------------------------------

    def get(self, session_id: str) -> Agent | None:
        return self.agents.get(session_id)

    def by_name(self, name: str) -> Agent | None:
        wanted = name.strip().lower()
        for agent in self.agents.values():
            if agent.name.lower() == wanted:
                return agent
        return None

    def working(self) -> list[Agent]:
        return [a for a in self.agents.values() if not a.done]

    def expired(self, now: float | None = None) -> list[Agent]:
        """Agents whose retention has run out and whose record should go.

        Only completed ones can expire. An agent that has been working for a
        fortnight is not stale, it is busy, and nothing here should be able to
        delete the channel of something still running.
        """
        moment = time.time() if now is None else now
        return [
            a
            for a in self.agents.values()
            if a.expires_at is not None and moment >= a.expires_at
        ]

    def needing_channel(self) -> list[Agent]:
        return [a for a in self.working() if a.wants_channel and a.channel_id is None]
