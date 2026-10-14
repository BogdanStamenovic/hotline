"""Keep the Discord sidebar in step with who is actually alive.

Bogdan's layout (10-14):

* **The operator is not in a folder.** Its channel sits outside every category,
  pinned to the top row. Discord always draws uncategorized channels above the
  categories, so position 0 there is the top of the sidebar.
* **Dead teams are not visible.** He owns the server, and Discord shows the owner
  every channel whatever the permission overwrites say, so "invisible" cannot be
  done with permissions. Deleting the channels would be invisible but takes the
  scrollback with it (the KinReply managers' channels hold weeks of his
  decisions). He chose the third way: a team with no live member has its
  channels moved into one **asleep** category at the bottom, which he collapses
  once and Discord keeps collapsed for him. Its own category is deleted while
  empty and comes back, icon and all, when a member wakes.
* **Inside asleep, still grouped by team.** He asked for subfolders; Discord
  has none (a category cannot hold a category). The closest real thing is a
  read-only *divider* channel per team (`📁-kinreply`) with that team's channels
  ordered under it. Dividers are created and removed here, and are recognised
  by their prefix inside the asleep category only.

Agents outside any team follow the same rule: live ones stay loose under the
operator, dead ones go to sleep. Otherwise the top row would be a column of dead
channels directly under the one he asked to have pinned there.

This is a reconciler, not an event handler. Agents die without telling anyone
(a closed pane, a power-off), so the only reliable trigger is to look: hotlined
runs `arrange` on a timer, and the commands that change liveness run it straight
away so he does not wait for the next tick. It reads the guild once and PATCHes
only what is out of place, so a pass over a settled sidebar costs one GET.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .channels import CATEGORY, bare
from .teams import Teams

ASLEEP = "asleep"
LOOSE = "no team"
DIVIDER = "📁-"


def sleeping(team: str) -> str:
    """227bd16's per-team sleep category; only still named so it gets cleaned up."""
    return f"{team} · {ASLEEP}"


def divider_name(team: str) -> str:
    return DIVIDER + "-".join(team.casefold().split())


@dataclass
class Plan:
    moves: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.moves or self.removed)

    def describe(self) -> str:
        return "; ".join(self.moves + [f"removed empty category {c}" for c in self.removed])


def _parent(channel: dict) -> int | None:
    raw = channel.get("parent_id")
    return int(raw) if raw else None


def arrange(
    manager: Any,
    registry: Any,
    live_ids: set[str],
    teams: Teams | None = None,
    log: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> Plan:
    """Put every agent channel where its liveness says it belongs.

    Only channels the registry knows about are touched, and only categories
    named after a team (or the asleep one) are ever deleted, and only while
    empty. `#general`, the voice lobby and anything Bogdan made by hand are
    never in either set.
    """
    teams = teams or Teams(registry.path.with_name("teams.json"))
    plan = Plan()
    channels = manager.all()
    by_id = {int(c["id"]): c for c in channels}
    category_names = {
        int(c["id"]): str(c.get("name", "")) for c in channels if c.get("type") == CATEGORY
    }

    def channels_of(agent: Any) -> list[int]:
        return [
            cid
            for cid in (agent.channel_id, agent.voice_channel_id)
            if cid is not None and int(cid) in by_id
        ]

    def place(cid: int, parent: int | None, label: str, position: int | None = None) -> None:
        current = by_id[int(cid)]
        if _parent(current) == parent and (position is None or current.get("position") == position):
            return
        where = category_names.get(parent, "a new category") if parent else "the top row"
        plan.moves.append(f"#{current.get('name')} → {where} ({label})")
        if dry_run:
            return
        body: dict[str, Any] = {"parent_id": str(parent) if parent is not None else None}
        if position is not None:
            body["position"] = position
        manager.patch(int(cid), body)

    def category(name: str) -> int:
        if dry_run:
            # Not created in a dry run; a negative id stands in so the plan still
            # shows the moves into it.
            wanted = bare(name)
            found = next((cid for cid, n in category_names.items() if bare(n) == wanted), None)
            if found is None:
                found = -1 - len(category_names)
                category_names[found] = f"{name} (would be created)"
            return found
        cid = int(manager.ensure_category(name))
        category_names.setdefault(cid, name)
        return cid

    agents = list(registry.agents.values())
    operators = [a for a in agents if a.privileged]
    operator_names = {a.name.lower() for a in operators}
    for agent in operators:
        for cid in channels_of(agent):
            place(cid, None, "operator", position=0)

    asleep: dict[str, list[int]] = {}
    in_a_team: set[str] = set()
    for team in teams.all():
        members = [registry.by_name(m) for m in team.members if m.lower() not in operator_names]
        in_a_team.update(m.lower() for m in team.members)
        members = [a for a in members if a is not None and channels_of(a)]
        if not members:
            continue
        if any(a.session_id in live_ids for a in members):
            home = category(team.display)
            for agent in members:
                for cid in channels_of(agent):
                    place(cid, home, f"{team.display} is awake")
        else:
            asleep.setdefault(team.display, []).extend(
                cid for a in members for cid in channels_of(a)
            )

    for agent in agents:
        if agent.name.lower() in operator_names or agent.name.lower() in in_a_team:
            continue
        for cid in channels_of(agent):
            if agent.session_id in live_ids:
                place(cid, None, "live, no team")
            else:
                asleep.setdefault(LOOSE, []).append(cid)

    # Teams alphabetically, the teamless ones last.
    order = sorted(asleep, key=lambda t: (t == LOOSE, t.casefold()))
    bed = category(ASLEEP) if asleep else None
    for team_name in order:
        for cid in asleep[team_name]:
            place(cid, bed, f"{team_name} asleep")
    if not dry_run:
        _tidy_bed(manager, bed, [(t, asleep[t]) for t in order], plan)

    # Empty team categories: the dead team's heading would otherwise stay in the
    # sidebar with nothing under it, which is the visible thing he asked to lose.
    # A dry run moved nothing, so it has nothing to count yet and removes nothing.
    if not dry_run:
        ours = {team.key for team in teams.all()} | {ASLEEP, bare(sleeping("loose"))}
        ours |= {bare(sleeping(team.display)) for team in teams.all()}
        occupied = {_parent(c) for c in manager.all()}
        for cid, name in category_names.items():
            if bare(name) in ours and cid not in occupied:
                manager.delete_category(cid)
                plan.removed.append(name)

    if plan and log is not None:
        log(f"arranged discord: {plan.describe()}")
    return plan


def _tidy_bed(
    manager: Any, bed: int | None, groups: list[tuple[str, list[int]]], plan: Plan
) -> None:
    """Dividers and order inside asleep, and asleep below every other category.

    Writes only when something is out of place, so a settled sidebar costs the
    one extra GET this needs to see the moves it just made.
    """
    listing = manager.all()
    stale = [
        c
        for c in listing
        if str(c.get("name", "")).startswith(DIVIDER)
        and (bed is None or _parent(c) != bed)
        and c.get("type") != CATEGORY
    ]
    if bed is None:
        for c in stale:
            manager.delete(int(c["id"]), force=True)
            plan.removed.append(f"#{c['name']}")
        return
    children = [c for c in listing if _parent(c) == bed]
    dividers = {
        str(c["name"]): int(c["id"]) for c in children if str(c["name"]).startswith(DIVIDER)
    }
    wanted: list[int] = []
    for team_name, cids in groups:
        name = divider_name(team_name)
        if name not in dividers:
            dividers[name] = manager.create_divider(name, bed, team_name)
            plan.moves.append(f"#{name} created in asleep")
        wanted.append(dividers[name])
        wanted.extend(cids)
    keep = set(wanted)
    for name, cid in dividers.items():
        if cid not in keep:
            manager.delete(cid, force=True)
            plan.removed.append(f"#{name}")
    for c in stale:
        manager.delete(int(c["id"]), force=True)
        plan.removed.append(f"#{c['name']}")

    listing = manager.all()
    current = [
        int(c["id"])
        for c in sorted(
            (c for c in listing if _parent(c) == bed and int(c["id"]) in keep),
            key=lambda c: (int(c.get("position", 0)), int(c["id"])),
        )
    ]
    if current != wanted:
        manager.reorder([(cid, i) for i, cid in enumerate(wanted)])
        plan.moves.append("asleep reordered by team")

    cats = {int(c["id"]): int(c.get("position", 0)) for c in listing if c.get("type") == CATEGORY}
    others = [p for cid, p in cats.items() if cid != bed]
    if others and cats.get(bed, 0) <= max(others):
        manager.patch(bed, {"position": max(others) + 1})
