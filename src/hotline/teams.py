"""Named groups of agents, one per repo, each member a keeper or passing.

**A team is a repo.** Bogdan's model (10-14): when an agent works inside a repo,
that repo is its work, and the repo's name and location are its hotline team. So
`--declare` puts an agent into the team of the git repo it runs in (worktrees
count as their main repo), unless it is already in a team by hand. Teams made by
hand with `--team X --join NAME` (KinReply) still work and are left alone.

**Two personas, a priority spectrum.** *Keepers* are the standing agents of a
team -- hotline's operator, the KinReply managers: the ones he reviews first and
talks to for weeks. *Passing* agents are workers that come and go. Only keepers
are exempt from retention; a passing member is reaped like anyone else. Rows
written before personas existed carry no `keepers` list, and every member of
such a team is read as a keeper, which is exactly what membership meant then.

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
import subprocess
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
    keepers: list[str] = field(default_factory=list)
    # The repo this team is, when it is one. Hand-made teams have none.
    path: str | None = None

    @property
    def key(self) -> str:
        return key_of(self.display)

    def is_keeper(self, name: str) -> bool:
        wanted = name.strip().lower()
        return any(k.lower() == wanted for k in self.keepers)

    def ordered(self) -> list[str]:
        """Members, keepers first: the order Bogdan reviews them in."""
        return [m for m in self.members if self.is_keeper(m)] + [
            m for m in self.members if not self.is_keeper(m)
        ]


PERSONAS = ("keeper", "passing")


def repo_of(cwd: str | os.PathLike[str]) -> tuple[str, Path] | None:
    """The repo a directory belongs to, as (name, root); None outside a repo.

    Uses the *common* git dir, so a worktree like ~/data/kinreply-wt/36/api lands
    in the team of the repo it was cut from rather than founding its own.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    common = Path(out.stdout.strip())
    root = common.parent if common.name == ".git" else common
    return root.name, root


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
                members = list(entry.get("members", []))
                team = Team(
                    display=str(entry["display"]),
                    members=members,
                    # No list at all = written before personas: all were kept.
                    keepers=list(entry["keepers"]) if "keepers" in entry else list(members),
                    path=entry.get("path"),
                )
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
                            {
                                "display": t.display,
                                "members": t.members,
                                "keepers": t.keepers,
                                **({"path": t.path} if t.path else {}),
                            }
                            for t in teams.values()
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

    def keeper_names(self) -> set[str]:
        return {k.lower() for t in self._read().values() for k in t.keepers}

    def persona_of(self, name: str) -> str | None:
        team = self.team_of(name)
        if team is None:
            return None
        return "keeper" if team.is_keeper(name) else "passing"

    # ---- changes --------------------------------------------------------

    def join(self, team: str, name: str, keeper: bool = True, path: str | None = None) -> Team:
        """Put an agent in a team, taking it out of any other first.

        One team per agent, because a channel sits in exactly one category.
        `keeper` defaults to True because a hand-made `--join` has always meant
        "keep this one"; the repo auto-join passes False unless told otherwise.
        """
        wanted = name.strip().lower()
        with self._editing() as teams:
            for other in teams.values():
                other.members = [m for m in other.members if m.lower() != wanted]
                other.keepers = [k for k in other.keepers if k.lower() != wanted]
            target = teams.setdefault(key_of(team), Team(display=team.strip()))
            if path and not target.path:
                target.path = path
            target.members.append(name.strip())
            if keeper:
                target.keepers.append(name.strip())
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
            target.keepers = [k for k in target.keepers if k.lower() != wanted]
            return True

    def set_persona(self, name: str, persona: str) -> Team | None:
        """Mark a member keeper or passing. None if it is in no team."""
        if persona not in PERSONAS:
            raise HotlineError(f"persona must be one of {', '.join(PERSONAS)}, not {persona!r}")
        wanted = name.strip().lower()
        with self._editing() as teams:
            for team in teams.values():
                match = next((m for m in team.members if m.lower() == wanted), None)
                if match is None:
                    continue
                team.keepers = [k for k in team.keepers if k.lower() != wanted]
                if persona == "keeper":
                    team.keepers.append(match)
                return team
        return None

    def join_repo(self, name: str, cwd: str | os.PathLike[str], keeper: bool) -> Team | None:
        """Auto-join the team of the repo `cwd` is in. Hand-made teams win.

        Returns the agent's team afterwards (possibly one it was already in),
        or None when it is in no team and `cwd` is not inside a repo.
        """
        current = self.team_of(name)
        if current is not None:
            return current
        repo = repo_of(cwd)
        if repo is None:
            return None
        display, root = repo
        return self.join(display, name, keeper=keeper, path=str(root))


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
