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
# Discord refuses a 51st channel in one category (CHANNEL_PARENT_MAX_CHANNELS),
# dividers included. asleep hit it on 11-15 and every arrange pass for four days
# died on its first move into it, so overflow spills into "asleep 2", "asleep 3".
CATEGORY_CAP = 50


def bed_name(index: int) -> str:
    return ASLEEP if index == 0 else f"{ASLEEP} {index + 1}"


def is_bed(name: str) -> bool:
    plain = bare(name)
    head, _, tail = plain.partition(" ")
    return head == ASLEEP and (not tail or tail.isdigit())


def pack(groups: list[tuple[str, list[int]]], cap: int = CATEGORY_CAP) -> list[list[tuple[str, list[int]]]]:
    """Split team groups into beds of at most `cap` channels, one divider each.

    A team is kept whole inside one bed when it fits; one bigger than a whole bed
    is cut into pieces, each under its own divider in the next bed.
    """
    beds: list[list[tuple[str, list[int]]]] = [[]]
    room = cap
    for team, cids in groups:
        pending = list(cids)
        while pending:
            fits_whole_bed = 1 + len(pending) <= cap
            if 1 + len(pending) > room and (fits_whole_bed or room < 2):
                beds.append([])
                room = cap
            take, pending = pending[: room - 1], pending[room - 1 :]
            beds[-1].append((team, take))
            room -= 1 + len(take)
    return [b for b in beds if b]


def sleeping(team: str) -> str:
    """227bd16's per-team sleep category; only still named so it gets cleaned up."""
    return f"{team} · {ASLEEP}"


def divider_name(team: str) -> str:
    return DIVIDER + "-".join(team.casefold().split())


@dataclass
class Plan:
    moves: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.moves or self.removed or self.failed)

    def describe(self) -> str:
        return "; ".join(
            self.moves
            + [f"removed empty category {c}" for c in self.removed]
            + [f"FAILED {f}" for f in self.failed]
        )


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

    def place(cid: int, parent: int | None, label: str, position: int | None = None) -> bool:
        """Move one channel; False if Discord refused, which never stops the pass.

        One refused move used to raise out of arrange, so everything queued
        behind it stayed put too -- the whole sidebar froze for four days on a
        single full category (11-15..11-19).
        """
        current = by_id[int(cid)]
        if _parent(current) == parent and (position is None or current.get("position") == position):
            return True
        where = category_names.get(parent, "a new category") if parent else "the top row"
        line = f"#{current.get('name')} → {where} ({label})"
        if dry_run:
            plan.moves.append(line)
            return True
        body: dict[str, Any] = {"parent_id": str(parent) if parent is not None else None}
        if position is not None:
            body["position"] = position
        try:
            manager.patch(int(cid), body)
        except Exception as exc:  # noqa: BLE001 - logged in the plan, the rest still runs
            plan.failed.append(f"{line}: {exc}"[:300])
            return False
        plan.moves.append(line)
        return True

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
    packed = pack([(t, asleep[t]) for t in order])
    bed_ids = [category(bed_name(index)) for index in range(len(packed))]
    # Fill the LAST bed first. Channels leave an earlier bed for a later one
    # before anything enters the earlier one, so a bed sitting at the cap has
    # room by the time its newcomers arrive (11-19: the first live pass sent
    # gamemode-0f into a still-full asleep and Discord refused it).
    landed_by_bed: dict[int, list[tuple[str, list[int]]]] = {}
    for index in reversed(range(len(packed))):
        bed = bed_ids[index]
        landed = [
            (team_name, [cid for cid in cids if place(cid, bed, f"{team_name} asleep")])
            for team_name, cids in packed[index]
        ]
        landed_by_bed[index] = [(t, c) for t, c in landed if c]
    beds = [(bed_ids[i], landed_by_bed[i]) for i in range(len(packed))]
    if not dry_run:
        _tidy_beds(manager, beds, plan)

    # Empty team categories: the dead team's heading would otherwise stay in the
    # sidebar with nothing under it, which is the visible thing he asked to lose.
    # A dry run moved nothing, so it has nothing to count yet and removes nothing.
    if not dry_run:
        ours = {team.key for team in teams.all()} | {ASLEEP, bare(sleeping("loose"))}
        ours |= {bare(sleeping(team.display)) for team in teams.all()}
        occupied = {_parent(c) for c in manager.all()}
        for cid, name in category_names.items():
            if (bare(name) in ours or is_bed(name)) and cid not in occupied:
                manager.delete_category(cid)
                plan.removed.append(name)

    if plan and log is not None:
        log(f"arranged discord: {plan.describe()}")
    return plan


def _tidy_beds(
    manager: Any, beds: list[tuple[int, list[tuple[str, list[int]]]]], plan: Plan
) -> None:
    """Dividers and order inside each asleep bed, and the beds below every other category.

    Writes only when something is out of place, so a settled sidebar costs the
    one extra GET this needs to see the moves it just made.
    """
    listing = manager.all()
    bed_ids = {bed for bed, _ in beds}
    for c in listing:
        if (
            str(c.get("name", "")).startswith(DIVIDER)
            and c.get("type") != CATEGORY
            and _parent(c) not in bed_ids
        ):
            manager.delete(int(c["id"]), force=True)
            plan.removed.append(f"#{c['name']}")
    if not beds:
        return

    changed = False
    for bed, groups in beds:
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
                changed = True
            wanted.append(dividers[name])
            wanted.extend(cids)
        keep = set(wanted)
        for name, cid in dividers.items():
            if cid not in keep:
                manager.delete(cid, force=True)
                plan.removed.append(f"#{name}")
                changed = True
        if changed:
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

    listing = manager.all()
    cats = {int(c["id"]): int(c.get("position", 0)) for c in listing if c.get("type") == CATEGORY}
    others = [p for cid, p in cats.items() if cid not in bed_ids]
    floor = max(others) if others else -1
    for index, (bed, _) in enumerate(beds):
        target = floor + 1 + index
        prev = cats.get(beds[index - 1][0], floor) if index else floor
        if cats.get(bed, 0) <= prev:
            manager.patch(bed, {"position": target})
            cats[bed] = target
