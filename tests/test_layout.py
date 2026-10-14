"""The sidebar layout and stopped-agent retention Bogdan asked for on 10-14.

* The operator's channel is outside every category, at the top.
* A team with no live member goes to one "asleep" category at the bottom,
  grouped there under a divider channel per team (Discord cannot nest
  categories), and
  its own category disappears while empty; waking a member brings it back.
* Passing agents are purged three days after they stop -- archived first --
  and keepers never are.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from test_teams import FakeGuild

from hotline.agents import Registry
from hotline.channels import CATEGORY, TEXT, Channels, bare
from hotline.layout import arrange
from hotline.router import parse_utterance
from hotline.seen import Seen
from hotline.teams import Teams


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake = FakeGuild()
    monkeypatch.setattr(Channels, "_call", lambda self, p, m="GET", b=None: fake(p, m, b))
    registry = Registry(tmp_path / "agents.json")
    return fake, Channels("token", 42), registry, Teams(tmp_path / "teams.json")


def _agent(fake: FakeGuild, registry: Registry, name: str, sid: str, parent: str | None = None):
    agent = registry.declare(sid, name, f"{name} work")
    cid = str(900 + len(fake.channels))
    fake.channels.append({"id": cid, "name": f"agent-{name}", "type": TEXT, "parent_id": parent})
    agent.channel_id = int(cid)
    registry.save()
    return agent


def _category_of(fake: FakeGuild, agent) -> str | None:
    channel = fake.by_id(agent.channel_id)
    parent = channel.get("parent_id") if channel else None
    return bare(fake.by_id(parent)["name"]) if parent else None


def test_operator_is_pinned_to_the_top_outside_any_category(world) -> None:
    fake, manager, registry, teams = world
    cat = manager.ensure_category("hotline")
    op = _agent(fake, registry, "hotline-80", "s-op", parent=str(cat))
    op.authority = "sys-admin"
    registry.save()
    teams.join("hotline", "hotline-80")

    arrange(manager, registry, {"s-op"}, teams=teams)

    channel = fake.by_id(op.channel_id)
    assert channel["parent_id"] is None and channel["position"] == 0
    # Its team held nobody else, so the empty heading went with it.
    assert not any(bare(c["name"]) == "hotline" for c in fake.categories())


def test_dead_team_goes_to_sleep_and_its_category_disappears(world) -> None:
    fake, manager, registry, teams = world
    site = _agent(fake, registry, "kinreply-site-fa", "s1")
    legal = _agent(fake, registry, "kinreply-legal-1d", "s2")
    teams.join("KinReply", site.name)
    teams.join("KinReply", legal.name)

    arrange(manager, registry, {"s1"}, teams=teams)
    assert _category_of(fake, site) == _category_of(fake, legal) == "kinreply"

    arrange(manager, registry, set(), teams=teams)
    assert _category_of(fake, site) == _category_of(fake, legal) == "asleep"
    assert not any(bare(c["name"]) == "kinreply" for c in fake.categories())
    # Nothing was deleted except the empty heading: both channels still exist.
    assert fake.by_id(site.channel_id) and fake.by_id(legal.channel_id)

    arrange(manager, registry, {"s2"}, teams=teams)
    assert _category_of(fake, site) == _category_of(fake, legal) == "kinreply"
    assert not any("asleep" in bare(c["name"]) for c in fake.categories())


def test_a_settled_sidebar_costs_one_read(world) -> None:
    fake, manager, registry, teams = world
    _agent(fake, registry, "tells-09", "s1")
    teams.join("tells", "tells-09")
    arrange(manager, registry, {"s1"}, teams=teams)
    fake.calls.clear()
    arrange(manager, registry, {"s1"}, teams=teams)
    assert [m for m, _, _ in fake.calls if m != "GET"] == []


def test_hand_made_categories_and_general_are_never_touched(world) -> None:
    fake, manager, registry, teams = world
    fake.channels.append({"id": "77", "name": "Text Channels", "type": CATEGORY})
    _agent(fake, registry, "data-10", "s1")
    arrange(manager, registry, set(), teams=teams)
    assert fake.by_id("77") and fake.by_id("1")["name"] == "general"
    assert fake.by_id("1").get("parent_id") is None


def test_loose_dead_agents_sleep_and_live_ones_stay_on_top(world) -> None:
    fake, manager, registry, teams = world
    dead = _agent(fake, registry, "data-f8", "s1")
    live = _agent(fake, registry, "data-10", "s2")
    arrange(manager, registry, {"s2"}, teams=teams)
    assert _category_of(fake, dead) == "asleep"
    assert _category_of(fake, live) is None


def test_dry_run_changes_nothing(world) -> None:
    fake, manager, registry, teams = world
    _agent(fake, registry, "data-f8", "s1")
    before = json.dumps(fake.channels, sort_keys=True)
    plan = arrange(manager, registry, set(), teams=teams, dry_run=True)
    assert plan.moves
    assert json.dumps(fake.channels, sort_keys=True) == before


# ---- retention of stopped agents ------------------------------------------


def test_stopped_passing_agent_is_purged_three_days_after_last_seen(world, tmp_path) -> None:
    fake, manager, registry, teams = world
    gone = _agent(fake, registry, "qwen-arch-agent-e8", "s1")
    teams.join("qwen-arch-agent", gone.name, keeper=False)
    now = time.time()
    seen = {"qwen-arch-agent-e8": now - 3 * 86400 - 1}
    fake.history = {}
    manager.history = lambda cid: ({"name": "agent-qwen-arch-agent-e8", "id": str(cid)}, [])  # type: ignore[method-assign]

    cid = gone.channel_id
    swept = registry.sweep(
        manager, now=now, live_ids=set(), seen=seen, archive_dir=tmp_path / "arch"
    )

    assert swept == ["qwen-arch-agent-e8"]
    assert fake.by_id(cid) is None
    assert (tmp_path / "arch" / f"agent-qwen-arch-agent-e8-{cid}.json").exists()


def test_negative_cases_keep_the_channel(world, tmp_path) -> None:
    """Keeper, still alive, under three days, or no liveness info: all kept."""
    fake, manager, registry, teams = world
    now = time.time()
    old = now - 10 * 86400
    keeper = _agent(fake, registry, "tells-09", "s1")
    teams.join("tells", keeper.name, keeper=True)
    alive = _agent(fake, registry, "data-10", "s2")
    recent = _agent(fake, registry, "data-f8", "s3")
    seen = {"tells-09": old, "data-10": old, "data-f8": now - 3600}
    manager.history = lambda cid: ({"name": "x"}, [])  # type: ignore[method-assign]

    assert registry.sweep(manager, now=now, live_ids={"s2"}, seen=seen, archive_dir=tmp_path) == []
    # Without liveness the old rule holds: only a --done can expire.
    assert registry.sweep(manager, now=now) == []
    for a in (keeper, alive, recent):
        assert fake.by_id(a.channel_id)


def test_a_failed_archive_keeps_channel_and_record(world, tmp_path) -> None:
    fake, manager, registry, _teams = world
    gone = _agent(fake, registry, "data-f8", "s1")

    def broken(cid):
        raise OSError("discord down")

    manager.history = broken  # type: ignore[method-assign]
    now = time.time()
    swept = registry.sweep(
        manager, now=now, live_ids=set(), seen={"data-f8": now - 4 * 86400}, archive_dir=tmp_path
    )
    assert swept == []
    assert fake.by_id(gone.channel_id)
    assert Registry(registry.path).by_name("data-f8") is not None


def test_unseen_agents_get_grace_not_purge(tmp_path) -> None:
    seen = Seen(tmp_path / "seen.json")
    now = 1000.0
    out = seen.update(alive=["a"], known=["a", "b"], now=now)
    assert out == {"a": now, "b": now}
    later = seen.update(alive=[], known=["a", "b"], now=now + 50)
    assert later == {"a": now, "b": now}
    assert seen.update(alive=[], known=["a"], now=now + 60) == {"a": now}


# ---- the wake command ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "target", "team"),
    [
        ("wake", None, False),
        ("wake kinreply", "kinreply", False),
        ("wake team KinReply", "KinReply", True),
        ("wake the KinReply team", "KinReply", True),
        ("wake up tells-09", "tells-09", False),
    ],
)
def test_wake_parses(text: str, target: str | None, team: bool) -> None:
    route = parse_utterance(text)
    assert (route.action, route.target, route.explicit) == ("wake", target, team)


def test_bare_wake_up_is_prose_for_the_agent() -> None:
    assert parse_utterance("wake up").action is None


def test_sleeping_teams_are_grouped_under_dividers_in_one_section(world) -> None:
    fake, manager, registry, teams = world
    site = _agent(fake, registry, "kinreply-site-fa", "s1")
    legal = _agent(fake, registry, "kinreply-legal-1d", "s5")
    tells = _agent(fake, registry, "tells-09", "s2")
    _agent(fake, registry, "data-f8", "s3")
    worker = _agent(fake, registry, "hotline-4e", "s4")
    teams.join("tells", tells.name)
    teams.join("KinReply", site.name)
    teams.join("KinReply", legal.name)
    teams.join("hotline", worker.name, keeper=False)

    arrange(manager, registry, {"s4"}, teams=teams)

    beds = [c for c in fake.categories() if "asleep" in bare(c["name"])]
    assert [bare(c["name"]) for c in beds] == ["asleep"]
    bed = beds[0]["id"]
    inside = sorted(
        (c for c in fake.channels if c.get("parent_id") == bed),
        key=lambda c: (c["position"], int(c["id"])),
    )
    assert [c["name"] for c in inside] == [
        "📁-kinreply",
        "agent-kinreply-site-fa",
        "agent-kinreply-legal-1d",
        "📁-tells",
        "agent-tells-09",
        "📁-no-team",
        "agent-data-f8",
    ]
    assert _category_of(fake, worker) == "hotline"
    pos = {bare(c["name"]): c["position"] for c in fake.categories()}
    assert pos["hotline"] < pos["asleep"]

    # Settled: a second pass writes nothing.
    fake.calls.clear()
    arrange(manager, registry, {"s4"}, teams=teams)
    assert [m for m, _, _ in fake.calls if m != "GET"] == []

    # tells wakes: its divider goes, the rest stays in order.
    arrange(manager, registry, {"s4", "s2"}, teams=teams)
    names = [c["name"] for c in fake.channels if c.get("parent_id") == bed]
    assert "📁-tells" not in names and "📁-kinreply" in names
    assert _category_of(fake, tells) == "tells"

    # Everyone wakes: no asleep section, no dividers anywhere.
    arrange(manager, registry, {"s1", "s2", "s3", "s4", "s5"}, teams=teams)
    assert not any(str(c["name"]).startswith("📁-") for c in fake.channels)
    assert not any("asleep" in bare(c["name"]) for c in fake.categories())
