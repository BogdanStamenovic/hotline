"""Named groups of agents that Bogdan wants kept around.

A team is a Discord category plus a promise: its members' channels live under
that category, and they are never reaped by retention. Bogdan asked for this for
the KinReply manager agents (site, legal, hosting migration) -- standing roles
he talks to for weeks, not one-shot workers whose channel is disposable.

**Membership is stored by agent name, in its own file, not on the Agent record.**
Two reasons, both measured rather than assumed:

* The name is the identity that survives respawns. `rehome` retires the old
  record and declares a fresh one, and `adopt` moves the record to a new session
  id; a field on the record has to be hand-copied through every one of those
  paths (the handoff and the sys-admin role were both lost that way before).
  A list of names needs no copying.
* Long-running processes (`hotlined`, `hotline-permwatcher`) run whatever code
  they started with, and the old `Registry.load` *skips* any row carrying a field
  it does not know. A `team` field on the record would make a not-yet-restarted
  daemon forget the very agents this exists to keep, and drop every message
  typed into their channels. A sibling file is invisible to old code.

The file sits next to the agent registry (`teams.json` beside `agents.json`), so
a registry opened on a test path finds its own teams file and never the real one.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import state_dir
from .errors import HotlineError


def teams_file() -> Path:
    return state_dir() / "teams.json"


def key_of(team: str) -> str:
    """Teams match case-insensitively: `kinreply` and `KinReply` are one team."""
    return team.strip().casefold()


@dataclass
class Team:
    # What Bogdan sees: the Discord category name. Fixed by whoever created the
    # team, so `--team kinreply --join x` later does not rename "KinReply".
    display: str
    members: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return key_of(self.display)


class Teams:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or teams_file()

    # ---- persistence ----------------------------------------------------

    def _read(self) -> dict[str, Team]:
        try:
            raw = json.loads(self.path.read_text()).get("teams", [])
        except (OSError, ValueError):
            return {}
        out: dict[str, Team] = {}
        for entry in raw:
            try:
                team = Team(display=str(entry["display"]), members=list(entry.get("members", [])))
            except (KeyError, TypeError):
                continue
            out[team.key] = team
        return out

    @contextlib.contextmanager
    def _editing(self) -> Iterator[dict[str, Team]]:
        # Read-modify-write under a lock, never from a copy loaded earlier: the
        # agent registry lost a declare's channel_id to exactly that on 09-26.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            teams = self._read()
            yield teams
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".teams.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "teams": [
                            {"display": t.display, "members": t.members} for t in teams.values()
                        ]
                    },
                    f,
                    indent=1,
                )
            os.replace(tmp, self.path)

    # ---- queries --------------------------------------------------------

    def all(self) -> list[Team]:
        return sorted(self._read().values(), key=lambda t: t.key)

    def get(self, team: str) -> Team | None:
        return self._read().get(key_of(team))

    def team_of(self, name: str) -> Team | None:
        wanted = name.strip().lower()
        for team in self._read().values():
            if any(m.lower() == wanted for m in team.members):
                return team
        return None

    def member_names(self) -> set[str]:
        return {m.lower() for t in self._read().values() for m in t.members}

    # ---- changes --------------------------------------------------------

    def join(self, team: str, name: str) -> Team:
        """Put an agent in a team, taking it out of any other first.

        One team per agent, because a channel sits in exactly one category.
        """
        wanted = name.strip().lower()
        with self._editing() as teams:
            for other in teams.values():
                other.members = [m for m in other.members if m.lower() != wanted]
            target = teams.setdefault(key_of(team), Team(display=team.strip()))
            target.members.append(name.strip())
            # A team with nobody in it is kept: the Discord category outlives
            # its members too, and its display name should not be forgotten.
            return target

    def leave(self, team: str, name: str) -> bool:
        wanted = name.strip().lower()
        with self._editing() as teams:
            target = teams.get(key_of(team))
            if target is None or not any(m.lower() == wanted for m in target.members):
                return False
            target.members = [m for m in target.members if m.lower() != wanted]
            return True


def parent_for(manager: Any, name: str, path: Path | None = None) -> int | None:
    """The category a team member's new channel should be created under.

    Never raises: a channel outside its category is a cosmetic fault, and a
    member left with no channel at all because the category lookup failed would
    be a real one.
    """
    team = Teams(path).team_of(name)
    if team is None or manager is None or not hasattr(manager, "ensure_category"):
        return None
    try:
        return int(manager.ensure_category(team.display))
    except (HotlineError, OSError, ValueError, TypeError):
        return None


def move_into(manager: Any, team: Team, channel_ids: list[int], log: Callable[[str], None]) -> bool:
    """Move channels under the team's category. True if every move landed."""
    try:
        category = manager.ensure_category(team.display)
    except HotlineError as exc:
        log(f"warning: could not find or create category {team.display!r}: {exc}")
        return False
    ok = True
    for cid in channel_ids:
        try:
            manager.move(cid, category)
        except HotlineError as exc:
            log(f"warning: could not move channel {cid}: {exc}")
            ok = False
    return ok
