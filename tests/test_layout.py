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


def _finish(registry, agent, days_ago: float) -> None:
    agent.completed_at = time.time() - days_ago * 86400
    registry.save()


def test_a_finished_passing_agent_is_purged_three_days_after_done(world, tmp_path) -> None:
    fake, manager, registry, teams = world
    gone = _agent(fake, registry, "qwen-arch-agent-e8", "s1")
    teams.join("qwen-arch-agent", gone.name, keeper=False)
    _finish(registry, gone, 3 + 1 / 86400)
    now = time.time()
    manager.history = lambda cid: ({"name": "agent-qwen-arch-agent-e8", "id": str(cid)}, [])  # type: ignore[method-assign]

    cid = gone.channel_id
    swept = registry.sweep(
        manager, now=now, live_ids=set(), seen={}, archive_dir=tmp_path / "arch"
    )

    assert swept == ["qwen-arch-agent-e8"]
    assert fake.by_id(cid) is None
    assert (tmp_path / "arch" / f"agent-qwen-arch-agent-e8-{cid}.json").exists()


def test_an_asleep_unfinished_agent_never_expires(world, tmp_path) -> None:
    """11-01: meme-trend and pm-fed-combo were asleep (idle, not --done) with wake
    tasks due, and the old "3 days since last seen" rule deleted both records,
    which is what made them unwakeable. Sleep is not completion."""
    fake, manager, registry, _teams = world
    asleep = _agent(fake, registry, "pm-fed-combo", "s1")
    now = time.time()
    manager.history = lambda cid: ({"name": "x"}, [])  # type: ignore[method-assign]
    swept = registry.sweep(
        manager, now=now, live_ids=set(), seen={"pm-fed-combo": now - 30 * 86400},
        archive_dir=tmp_path,
    )
    assert swept == []
    assert Registry(registry.path).by_name("pm-fed-combo") is not None
    assert fake.by_id(asleep.channel_id)


def test_negative_cases_keep_the_channel(world, tmp_path) -> None:
    """A keeper finished long ago, an unfinished agent, one finished an hour ago: all kept."""
    fake, manager, registry, teams = world
    now = time.time()
    old = now - 10 * 86400
    keeper = _agent(fake, registry, "tells-09", "s1")
    teams.join("tells", keeper.name, keeper=True)
    _finish(registry, keeper, 10)
    alive = _agent(fake, registry, "data-10", "s2")
    recent = _agent(fake, registry, "data-f8", "s3")
    _finish(registry, recent, 1 / 24)
    seen = {"tells-09": old, "data-10": old, "data-f8": now - 3600}
    manager.history = lambda cid: ({"name": "x"}, [])  # type: ignore[method-assign]

    assert registry.sweep(manager, now=now, live_ids={"s2"}, seen=seen, archive_dir=tmp_path) == []
    # Without liveness info nothing changes either: only a --done can expire.
    assert registry.sweep(manager, now=now) == []
    for a in (keeper, alive, recent):
        assert fake.by_id(a.channel_id)


def test_a_failed_archive_keeps_channel_and_record(world, tmp_path) -> None:
    fake, manager, registry, _teams = world
    gone = _agent(fake, registry, "data-f8", "s1")
    _finish(registry, gone, 4)

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


def test_an_already_deleted_channel_is_forgotten_not_retried(world, tmp_path) -> None:
    """24 such agents were retried every ten minutes from 10-22 (14k journal lines)."""
    fake, manager, registry, _teams = world
    gone = _agent(fake, registry, "api-f4", "s1")
    _finish(registry, gone, 4)
    fake.channels = [c for c in fake.channels if str(c["id"]) != str(gone.channel_id)]

    def unknown(cid):
        raise OSError("404 Unknown Channel")

    manager.history = unknown  # type: ignore[method-assign]
    now = time.time()
    swept = registry.sweep(
        manager, now=now, live_ids=set(), seen={"api-f4": now - 4 * 86400}, archive_dir=tmp_path
    )
    assert swept == ["api-f4"]
    assert Registry(registry.path).by_name("api-f4") is None


def test_an_unreadable_guild_forgets_nothing(world, tmp_path) -> None:
    """If the listing fails, absence is unknown, so the record and its id are kept."""
    fake, manager, registry, _teams = world
    gone = _agent(fake, registry, "api-f4", "s1")
    _finish(registry, gone, 4)
    fake.channels = [c for c in fake.channels if str(c["id"]) != str(gone.channel_id)]

    def down(*a, **k):
        raise OSError("discord down")

    manager.history = down  # type: ignore[method-assign]
    manager.all = down  # type: ignore[method-assign]
    now = time.time()
    swept = registry.sweep(
        manager, now=now, live_ids=set(), seen={"api-f4": now - 4 * 86400}, archive_dir=tmp_path
    )
    assert swept == []
    assert Registry(registry.path).by_name("api-f4").channel_id == gone.channel_id


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


# ---- 11-19: asleep overflowed Discord's 50-per-category cap ------------------

from hotline.layout import CATEGORY_CAP, pack


def _sizes(beds):
    return [sum(1 + len(c) for _, c in bed) for bed in beds]


def test_pack_keeps_teams_whole_and_never_exceeds_the_cap() -> None:
    groups = [(f"t{i}", list(range(i * 100, i * 100 + 9))) for i in range(8)]  # 8 x 10 slots
    beds = pack(groups)
    assert _sizes(beds) == [50, 30]
    assert all(len(c) == 9 for bed in beds for _, c in bed)


def test_pack_splits_a_team_bigger_than_a_bed() -> None:
    beds = pack([("small", [1, 2]), ("huge", list(range(100, 200)))])
    assert all(s <= CATEGORY_CAP for s in _sizes(beds))
    assert sorted(c for bed in beds for t, cs in bed if t == "huge" for c in cs) == list(range(100, 200))


def test_pack_under_the_cap_is_one_bed() -> None:
    assert len(pack([("a", [1, 2, 3]), ("b", [4])])) == 1
    assert pack([]) == []


def test_a_full_asleep_spills_into_asleep_2(world) -> None:
    """The live failure: 50 channels already in asleep, then more agents fall asleep."""
    fake, manager, registry, teams = world
    sleepers = [_agent(fake, registry, f"data-{i:02x}", f"s{i}") for i in range(60)]
    arrange(manager, registry, set(), teams=teams)
    beds = [c for c in fake.categories() if "asleep" in bare(c["name"])]
    assert sorted(bare(c["name"]) for c in beds) == ["asleep", "asleep 2"]
    for bed in beds:
        assert sum(1 for c in fake.channels if c.get("parent_id") == bed["id"]) <= CATEGORY_CAP
    assert all(_category_of(fake, a) in {"asleep", "asleep 2"} for a in sleepers)
    # Settled: a second pass writes nothing.
    fake.calls.clear()
    arrange(manager, registry, set(), teams=teams)
    assert [m for m, _, _ in fake.calls if m != "GET"] == []
    # Everyone wakes: both beds go away.
    arrange(manager, registry, {f"s{i}" for i in range(60)}, teams=teams)
    assert not any("asleep" in bare(c["name"]) for c in fake.categories())


def test_one_refused_move_does_not_freeze_the_rest(world, monkeypatch) -> None:
    fake, manager, registry, teams = world
    bad = _agent(fake, registry, "data-bad", "s1")
    good = _agent(fake, registry, "data-good", "s2")
    real = manager.patch

    def patch(cid, body):
        if int(cid) == bad.channel_id:
            raise RuntimeError("CHANNEL_PARENT_MAX_CHANNELS")
        return real(cid, body)

    monkeypatch.setattr(manager, "patch", patch)
    plan = arrange(manager, registry, set(), teams=teams)
    assert _category_of(fake, good) == "asleep"
    assert _category_of(fake, bad) is None
    assert any("data-bad" in f for f in plan.failed)


# ---- 11-19: ten days asleep retires an unfinished agent ----------------------

import sqlite3

from hotline.agents import pending_wake_targets


def _sweep_asleep(world, tmp_path, days, *, wake=frozenset(), live=frozenset(), keeper=False, done=False):
    fake, manager, registry, teams = world
    agent = _agent(fake, registry, "data-59", "s1")
    if keeper:
        teams.join("data", agent.name, keeper=True)
    if done:
        registry.complete("s1")
    now = time.time()
    manager.history = lambda cid: ({"name": "agent-data-59", "id": str(cid)}, [])  # type: ignore[method-assign]
    cid = agent.channel_id
    swept = registry.sweep(
        manager, now=now, live_ids=set(live), seen={"data-59": now - days * 86400},
        archive_dir=tmp_path / "arch", wake_targets=set(wake),
    )
    return swept, fake.by_id(cid), tmp_path / "arch" / f"agent-data-59-{cid}.json"


def test_ten_days_asleep_retires_an_unfinished_agent(world, tmp_path) -> None:
    swept, channel, archive = _sweep_asleep(world, tmp_path, 10.01)
    assert swept == ["data-59"] and channel is None and archive.exists()


def test_nine_days_asleep_is_kept(world, tmp_path) -> None:
    swept, channel, _ = _sweep_asleep(world, tmp_path, 9.9)
    assert swept == [] and channel is not None


def test_a_pending_wake_task_keeps_a_long_sleeper(world, tmp_path) -> None:
    swept, channel, _ = _sweep_asleep(world, tmp_path, 30, wake={"data-59"})
    assert swept == [] and channel is not None


def test_a_live_agent_is_never_retired_as_asleep(world, tmp_path) -> None:
    swept, channel, _ = _sweep_asleep(world, tmp_path, 30, live={"s1"})
    assert swept == [] and channel is not None


def test_a_keeper_asleep_ten_days_goes_too(world, tmp_path) -> None:
    """11-19 "let them go aswell": keepers are not exempt from the asleep rule."""
    swept, channel, _ = _sweep_asleep(world, tmp_path, 30, keeper=True)
    assert swept == ["data-59"] and channel is None


def test_a_finished_keeper_asleep_ten_days_goes_too(world, tmp_path) -> None:
    swept, _, _ = _sweep_asleep(world, tmp_path, 10.01, keeper=True, done=True)
    assert swept == ["data-59"]


def test_a_keeper_asleep_nine_days_stays(world, tmp_path) -> None:
    swept, channel, _ = _sweep_asleep(world, tmp_path, 9.9, keeper=True, done=True)
    assert swept == [] and channel is not None


def test_a_finished_passing_agent_keeps_its_own_clock(world, tmp_path) -> None:
    """meme-trend: done with keep_days=30, asleep 17 days -- not retired early."""
    fake, _manager, registry, _ = world
    agent = _agent(fake, registry, "meme-trend", "s1")
    registry.complete("s1")
    agent = registry.get("s1")
    agent.keep_days = 30
    registry.save()
    now = time.time()
    assert registry.expired(now, live_ids=set(), seen={"meme-trend": now - 17 * 86400}, wake_targets=set()) == []


def test_unknown_wake_tasks_retire_nobody(world, tmp_path) -> None:
    fake, _manager, registry, _ = world
    _agent(fake, registry, "data-59", "s1")
    now = time.time()
    assert registry.expired(now, live_ids=set(), seen={"data-59": now - 30 * 86400}, wake_targets=None) == []


def test_wake_targets_are_read_from_pending_tasks(tmp_path) -> None:
    db = tmp_path / "wake.db"
    con = sqlite3.connect(db)
    con.execute("create table tasks (task text, status text)")
    con.executemany("insert into tasks values (?, ?)", [
        ('PATH=x hotline --to Manager-15 --no-wait "hi"', "pending"),
        ("hotline --to 'stizem-64' --no-wait 'x'", "pending"),
        ("hotline --to data-59 --no-wait x", "done"),
        ("setsid -f pmfed-report", "pending"),
    ])
    con.commit()
    con.close()
    assert pending_wake_targets(db) == {"manager-15", "stizem-64"}
    assert pending_wake_targets(tmp_path / "absent.db") == set()
    (tmp_path / "junk.db").write_text("not sqlite")
    assert pending_wake_targets(tmp_path / "junk.db") is None


def test_a_full_bed_empties_out_before_newcomers_arrive(world, monkeypatch) -> None:
    """The first live pass (11-19): asleep held 50, the new plan sent some of
    them on to asleep 2 and brought newcomers in -- and moved the newcomers
    first, into a bed Discord still counted as full."""
    fake, manager, registry, teams = world
    real = fake.__call__

    def capped(path, method="GET", body=None):
        if method == "PATCH" and isinstance(body, dict) and body.get("parent_id"):
            cid = path.rsplit("/", 1)[-1]
            held = [c for c in fake.channels if c.get("parent_id") == body["parent_id"] and c["id"] != cid]
            if len(held) >= CATEGORY_CAP:
                raise RuntimeError("CHANNEL_PARENT_MAX_CHANNELS")
        return real(path, method, body)

    monkeypatch.setattr(Channels, "_call", lambda self, p, m="GET", b=None: capped(p, m, b))
    bed = manager.ensure_category("asleep")
    # 50 teamless sleepers already filling asleep, then a team falls asleep.
    for i in range(50):
        _agent(fake, registry, f"data-{i:02x}", f"s{i}", parent=str(bed))
    late = _agent(fake, registry, "gamemode-0f", "g1")
    teams.join("gamemode", late.name)
    plan = arrange(manager, registry, set(), teams=teams)
    assert plan.failed == []
    assert _category_of(fake, late) == "asleep"
