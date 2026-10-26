"""Asking, once, whether an agent that fell asleep should become a keeper.

Bogdan's rule (10-26): after an agent falls asleep its channel gets a message
"make this agent a keeper?" with Yes and No. Yes wakes it, has it write a
summary, and makes it a keeper. No means no more messages about it. Typing
"make this a keeper" in the channel is the same as clicking Yes.

Why it exists: agents now sleep after 30 idle minutes, and a sleeping *passing*
agent is purged three days after it was last alive. Before, nothing told him an
agent had gone to sleep, so the first he heard of a channel he cared about was
its deletion. The question comes at the moment it is cheapest to answer.

**Asked once per agent, ever**, unless he answers. The answer and the asking are
both recorded in `keeper-prompts.json` beside the registry, keyed by agent
name: the name is the identity that survives a revive, the session id is not.

**Only fresh sleeps are asked about.** An agent qualifies if it was last seen
alive within `RECENT`, so the first pass after this shipped does not post into
every channel that has been asleep for days.

Keepers are per team and a team is a repo (`teams.py`). An agent outside any
repo joins one shared hand-made team, `KEEPERS_TEAM`, so "keeper" means the same
thing for it: a category of its own in the sidebar and no retention.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .agents import Agent, Registry
from .teams import Teams

RECENT = 2 * 3600.0
KEEPERS_TEAM = "keepers"
YES, NO = "keeper:yes:", "keeper:no:"

# "make this a keeper", "make it a keeper", "make this agent a keeper."
MAKE_KEEPER = re.compile(r"^\s*make\s+(?:this|it)(?:\s+agent)?\s+a\s+keeper\s*[.!]*\s*$", re.IGNORECASE)

SUMMARY_ASK = (
    "Bogdan just made you a keeper: a standing agent he will come back to, exempt "
    "from retention. Write a summary of your work now -- what you were asked to do, "
    "what you did and found, where things stand, and what is open -- as a handoff "
    "file in your working directory (HANDOFF.md or the repo's handoff convention), "
    "then post a short version of it in your own channel with hotline-say."
)


class Prompts:
    """`{name: {"state": asked|declined|kept, "at": ts, "message_id": id}}` on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def state(self, name: str) -> str | None:
        entry = self.read().get(name.lower())
        return entry.get("state") if isinstance(entry, dict) else None

    def set(self, name: str, state: str, **extra: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.read()
            data[name.lower()] = {"state": state, "at": time.time(), **extra}
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".keeper.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)


def prompts_beside(registry_path: Path) -> Prompts:
    return Prompts(registry_path.with_name("keeper-prompts.json"))


def teams_beside(registry_path: Path) -> Teams:
    return Teams(registry_path.with_name("teams.json"))


def due(
    registry: Registry,
    live_ids: set[str],
    seen: dict[str, float],
    prompts: Prompts,
    now: float | None = None,
) -> list[Agent]:
    """Agents that just fell asleep and have never been asked about."""
    moment = time.time() if now is None else now
    keepers = teams_beside(registry.path).keeper_names()
    asked = prompts.read()
    out = []
    for a in registry.agents.values():
        name = a.name.lower()
        if a.done or a.privileged or a.channel_id is None or a.session_id in live_ids:
            continue
        if name in keepers or name in asked:
            continue
        last = seen.get(name)
        if last is None or moment - last > RECENT:
            continue
        out.append(a)
    return out


def components(name: str) -> list[dict[str, Any]]:
    """A Yes/No button row. Raw API shape, because it is posted over REST."""
    return [
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "Yes, make it a keeper", "custom_id": YES + name},
                {"type": 2, "style": 4, "label": "No", "custom_id": NO + name},
            ],
        }
    ]


def ask(manager: Any, agent: Agent, prompts: Prompts) -> int | None:
    """Post the question in the agent's channel and record that it was asked."""
    assert agent.channel_id is not None
    body = {
        "content": (
            f"💤 **{agent.name}** fell asleep after being idle. Make it a **keeper**? "
            "A keeper is never deleted; a passing agent is deleted 3 days after it was "
            "last alive. Yes wakes it and has it write a summary. "
            'You can also type "make this a keeper" here any time.'
        ),
        "components": components(agent.name),
    }
    sent = manager.send(agent.channel_id, body)
    message_id = int(sent["id"]) if isinstance(sent, dict) and "id" in sent else None
    prompts.set(agent.name, "asked", message_id=message_id)
    return message_id


def make_keeper(agent: Agent, cwd: str | None, registry_path: Path) -> str:
    """Mark the agent a keeper of its team (its repo's, or the shared one). Returns the team."""
    teams = teams_beside(registry_path)
    team = teams.team_of(agent.name)
    if team is not None:
        teams.set_persona(agent.name, "keeper")
        return team.display
    if cwd:
        with contextlib.suppress(Exception):
            joined = teams.join_repo(agent.name, cwd, keeper=True)
            if joined is not None:
                return joined.display
    return teams.join(KEEPERS_TEAM, agent.name, keeper=True).display
