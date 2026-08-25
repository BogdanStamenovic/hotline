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
    # "sys-admin" or None. A standing role, granted by Bogdan, carrying authority
    # over other agents and over this repository -- and deliberately NOT over the
    # things only he can consent to. See `provenance.SYSADMIN_SCOPE`.
    authority: str | None = None
    # Where he granted it: a Discord message id, so the delegation is checkable
    # against Discord rather than asserted. Without this, `sys-admin` would be an
    # unverifiable claim that outranks a verifiable one, which is strictly worse
    # than having no role at all.
    granted_by: str | None = None
    granted_in: str | None = None

    @property
    def privileged(self) -> bool:
        return self.authority == "sys-admin"

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
        badge = f" ⟨{self.authority}⟩" if self.authority else ""
        line = f"{self.name}{badge} [{state}] — {self.task}"
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
            # The name is deliberately NOT updated. It is the identity Bogdan
            # types -- `connect hotline-80`, `resume hotline-80` -- and the
            # caller's `name` here is derived from the session, so re-declaring
            # an adopted or resumed agent would quietly rename it back to
            # `data-88` and orphan every reference he has. A retask changes what
            # it is doing, not who it is.
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

    def adopt(self, name: str, session_id: str) -> Agent | None:
        """Move an agent's identity onto a new session, keeping its channel.

        A worker that is respawned -- by the watchdog, or by hand from a handoff
        -- is the same agent continuing, not a new one. Declaring afresh would
        mint a second channel and orphan the one Bogdan is already reading, and
        `connect <name>` would keep resolving to the corpse. The record is the
        identity; the session id is just where it currently lives.

        Returns None if no such agent exists, and refuses to move an identity
        onto a session when the old one is still registered as a different live
        agent -- two sessions narrating into one channel is the confusion this
        is meant to remove.
        """
        agent = self.by_name(name)
        if agent is None:
            return None
        if agent.session_id == session_id:
            return agent
        del self.agents[agent.session_id]
        agent.session_id = session_id
        # An adopted agent is working by definition: something is alive and has
        # picked the job back up. A predecessor that ran `--done` and then got
        # resumed should not stay marked finished.
        agent.completed_at = None
        self.agents[session_id] = agent
        self.save()
        return agent

    def grant(self, name: str, role: str, message_id: str, channel_id: str) -> Agent | None:
        """Give an agent a standing role, recording where it was granted.

        The receipt is the point. Anything on this machine can write this file,
        so the flag alone is an assertion -- but the message id it carries is
        checkable against Discord, so a reader can confirm Bogdan really did
        delegate this rather than take the registry's word for it.
        """
        agent = self.by_name(name)
        if agent is None:
            return None
        agent.authority = role
        agent.granted_by = message_id
        agent.granted_in = channel_id
        self.save()
        return agent

    def privileged(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.privileged]

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
            # A standing role does not expire. It is the thing that survives its
            # own sessions -- "you never really go away, you just recycle" -- and
            # a retention sweep quietly deleting it three days after one stint
            # ended would take the role with it.
            if not a.privileged and a.expires_at is not None and moment >= a.expires_at
        ]

    def needing_channel(self) -> list[Agent]:
        return [a for a in self.working() if a.wants_channel and a.channel_id is None]

    def sweep(
        self,
        manager: Any = None,
        now: float | None = None,
        log: Any = None,
    ) -> list[str]:
        """Close out agents whose retention has run out. Returns what was swept.

        Deleting the channel and forgetting the record happen together, because
        the record is the only thing that knows the channel id. Doing one without
        the other leaves either an orphan channel nobody can name, or a registry
        pointing at a channel that no longer exists.
        """
        swept: list[str] = []
        for agent in self.expired(now):
            if manager is not None:
                for attr in ("channel_id", "voice_channel_id"):
                    cid = getattr(agent, attr)
                    if cid is None:
                        continue
                    try:
                        manager.delete(cid)
                    except Exception as exc:  # noqa: BLE001 - one bad id must not stop the sweep
                        # Kept, not dropped: a channel that would not delete is
                        # the one thing here worth a human seeing, and the agent
                        # keeps its id so the next pass tries again.
                        if log is not None:
                            log(f"could not delete channel {cid} for {agent.name}: {exc}")
                        continue
                    setattr(agent, attr, None)
            swept.append(agent.name)
            self.agents.pop(agent.session_id, None)
        if swept:
            self.save()
        return swept
