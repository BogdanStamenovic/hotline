"""Reviving an agent whose session is gone.

The interesting cases are the ones where the agent did NOT finish tidily. A
session killed by a crash, an OOM or a daemon restart never writes a handoff,
and those are exactly the ones worth reviving -- so "no handoff" has to mean
"read the transcript instead", not "refuse".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hotline import revive
from hotline.agents import Registry
from hotline.errors import ReplyTimeout


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    return Registry(path=tmp_path / "agents.json")


class FakeChannels:
    """Just enough Discord to answer "is that channel still there?"."""

    def __init__(self, present: set[int]) -> None:
        self.present = present
        self.created: list[str] = []

    def exists(self, channel_id: int) -> bool:
        return int(channel_id) in self.present

    def create_text(self, name: str, topic: str = "", parent_id: int | None = None) -> int:
        self.created.append(name)
        return 9999


# ---- what a replacement is handed ------------------------------------------


def test_a_killed_agent_is_seeded_from_its_transcript(
    registry: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case that matters: nothing was written down, but the record survives."""
    corpse = tmp_path / "sid-old.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(revive, "transcript_path", lambda sid: corpse)
    agent = registry.declare("sid-old", "data-f3", "mirror the ollama server")

    brief = revive.brief_for(agent)

    assert brief is not None
    assert not brief.from_handoff
    assert str(corpse) in brief.seed
    assert "KILLED" in brief.seed, "the replacement must know it is reading a corpse"
    assert "verify" in brief.seed.lower(), "claims in a transcript are not results"


def test_a_handoff_is_preferred_when_there_is_one(
    registry: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corpse = tmp_path / "sid-old.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(revive, "transcript_path", lambda sid: corpse)
    handoff = tmp_path / "handoff.md"
    handoff.write_text("the state is X")
    agent = registry.declare("sid-old", "data-f3", "mirror")
    registry.complete("sid-old", handoff=str(handoff))

    brief = revive.brief_for(agent)

    assert brief is not None and brief.from_handoff
    assert "the state is X" in brief.seed
    assert str(corpse) not in brief.seed


def test_an_unreadable_handoff_falls_back_to_the_transcript(
    registry: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A handoff path that no longer resolves must not make an agent unrevivable."""
    corpse = tmp_path / "sid-old.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(revive, "transcript_path", lambda sid: corpse)
    agent = registry.declare("sid-old", "data-f3", "mirror")
    registry.complete("sid-old", handoff=str(tmp_path / "deleted.md"))

    brief = revive.brief_for(agent)

    assert brief is not None and not brief.from_handoff


def test_nothing_to_resume_from_returns_none(
    registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(revive, "transcript_path", lambda sid: None)
    agent = registry.declare("sid-old", "data-f3", "mirror")

    assert revive.brief_for(agent) is None


# ---- which agents are offered ----------------------------------------------


def test_a_live_agent_is_not_offered_for_resuming(
    registry: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Something still running should be connected to, not resurrected."""
    corpse = tmp_path / "c.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(revive, "transcript_path", lambda sid: corpse)
    registry.declare("alive", "runner", "still going")
    registry.declare("dead", "corpse", "not going")

    offered = [a.name for a in revive.resumable(registry, live_ids={"alive"})]

    assert offered == ["corpse"]


def test_the_offer_is_newest_first_and_capped(
    registry: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corpse = tmp_path / "c.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(revive, "transcript_path", lambda sid: corpse)
    for i in range(15):
        agent = registry.declare(f"sid-{i}", f"agent-{i}", "work")
        agent.declared_at = float(i)
    registry.save()

    offered = revive.resumable(registry, live_ids=set(), limit=10)

    assert len(offered) == 10
    assert offered[0].name == "agent-14"


def test_an_agent_with_nothing_left_is_not_offered(
    registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offering a revive that cannot happen is worse than a shorter list."""
    monkeypatch.setattr(revive, "transcript_path", lambda sid: None)
    registry.declare("sid", "ghost", "work")

    assert revive.resumable(registry, live_ids=set()) == []


# ---- moving the record onto the new session --------------------------------


def test_a_live_channel_is_carried_over_not_duplicated(registry: Registry) -> None:
    """A killed agent still owns the thread Bogdan has been reading."""
    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.channel_id = 4242
    registry.save()
    channels = FakeChannels(present={4242})

    revived = revive.rehome(registry, agent, "sid-new", channels)

    assert channels.created == [], "it already had a channel"
    assert revived.channel_id == 4242
    assert revived.session_id == "sid-new"
    assert registry.get("sid-old") is None, "the corpse must stop resolving"


def test_a_deleted_channel_is_recreated(registry: Registry) -> None:
    """`--done` deletes the channel, so resuming a finished agent needs a new one."""
    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.channel_id = 4242
    registry.save()
    channels = FakeChannels(present=set())

    revived = revive.rehome(registry, agent, "sid-new", channels)

    assert channels.created == ["data-f3"]
    assert revived.channel_id == 9999


def test_reviving_keeps_the_name_and_the_task(registry: Registry) -> None:
    agent = registry.declare("sid-old", "data-f3", "mirror the ollama server")

    revived = revive.rehome(registry, agent, "sid-new", None)

    assert revived.name == "data-f3", "resuming by name must give back that name"
    assert revived.task == "mirror the ollama server"
    assert not revived.done, "a revived agent is working again"


# ---- the shared revive, which the CLI and the daemon both call --------------
#
# `tmuxen.spawn` is faked throughout. A test that really spawned would put a
# `claude` on this box for every run of the suite, and the thing under test here
# is the sequencing and what comes back, not tmux.


class FakeSession:
    def __init__(self, session_id: str = "sid-new", name: str = "data-f3"):
        self.session_id = session_id
        self.name = name
        self.tmux = "hl-data-f3:@1.%1"


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def spawn(key, cwd=None, bypass=True, timeout=90.0, name=None):
        calls.append({"key": key, "cwd": cwd, "name": name})
        return FakeSession()

    import hotline.tmuxen as tmuxen_module

    monkeypatch.setattr(tmuxen_module, "spawn", spawn)
    return calls


@pytest.fixture
def corpse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "sid-old.jsonl"
    path.write_text("{}\n")
    monkeypatch.setattr(revive, "transcript_path", lambda sid: path)
    return path


async def test_resume_spawns_under_the_agents_own_name(
    registry: Registry, spawns: list[dict], corpse: Path
) -> None:
    """The identity is the point of resuming. A session that comes back called
    `hl-data-f3` has lost the thing you resumed it by."""
    registry.declare("sid-old", "data-f3", "mirror the ollama server")

    resumed = await revive.resume("data-f3", registry, cwd="/tmp")

    assert spawns == [{"key": "data-f3", "cwd": "/tmp", "name": "data-f3"}]
    assert resumed.agent.name == "data-f3"
    assert resumed.agent.session_id == "sid-new"
    assert resumed.tmux == "hl-data-f3"
    assert registry.get("sid-old") is None, "the corpse must stop resolving"


async def test_resume_reports_that_it_read_a_corpse(
    registry: Registry, spawns: list[dict], corpse: Path
) -> None:
    """A replacement working from a transcript is in a materially weaker
    position than one working from a handoff, and every caller has to be able to
    say so rather than reporting a plain success."""
    registry.declare("sid-old", "data-f3", "mirror")

    resumed = await revive.resume("data-f3", registry)

    assert not resumed.from_handoff
    assert "KILLED" in resumed.brief.seed


async def test_resume_carries_a_live_channel_over(
    registry: Registry, spawns: list[dict], corpse: Path
) -> None:
    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.channel_id = 4242
    registry.save()

    resumed = await revive.resume("data-f3", registry, channels=FakeChannels(present={4242}))

    assert resumed.kept_channel
    assert resumed.agent.channel_id == 4242
    assert resumed.channel_error is None


async def test_a_recreated_channel_is_not_reported_as_kept(
    registry: Registry, spawns: list[dict], corpse: Path
) -> None:
    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.channel_id = 4242
    registry.save()

    resumed = await revive.resume("data-f3", registry, channels=FakeChannels(present=set()))

    assert not resumed.kept_channel
    assert resumed.agent.channel_id == 9999


async def test_a_discord_failure_keeps_the_session_and_says_so(
    registry: Registry, spawns: list[dict], corpse: Path
) -> None:
    """The session is already running by then. Losing it because a chat service
    would not answer would be the worse of the two outcomes by a distance."""

    class Broken:
        def exists(self, channel_id):
            raise revive.HotlineError("discord said no")

        def create_text(self, name, topic="", parent_id=None):
            raise revive.HotlineError("discord said no")

    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.channel_id = 4242
    registry.save()

    resumed = await revive.resume("data-f3", registry, channels=Broken())

    assert resumed.channel_error == "discord said no"
    assert resumed.agent.session_id == "sid-new", "the record still moved onto the new session"


async def test_resume_refuses_an_unknown_name(registry: Registry, spawns: list[dict]) -> None:
    with pytest.raises(revive.NoSuchAgent):
        await revive.resume("nobody", registry)
    assert spawns == [], "nothing may be spawned before the name is known"


async def test_resume_refuses_an_agent_with_nothing_left(
    registry: Registry, spawns: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spawning first and discovering there is no brief afterwards would leave a
    bare `claude` sitting in a pane with nothing to do."""
    monkeypatch.setattr(revive, "transcript_path", lambda sid: None)
    registry.declare("sid-old", "ghost", "work")

    with pytest.raises(revive.NothingToResumeFrom):
        await revive.resume("ghost", registry)
    assert spawns == []


# ---- what `declare` drops on the way through -------------------------------
#
# `rehome` rebuilds the record by calling `registry.declare`, which constructs an
# Agent from its own arguments. Every field not in that signature is dropped
# silently. These pin the two that matter, because the damage from losing them
# does not show up here -- it shows up one resume later, in a different session,
# looking like something else entirely.


def test_reviving_keeps_the_handoff_pointer(registry: Registry) -> None:
    """Losing it makes the NEXT resume read a corpse instead of the handoff.

    `brief_for` prefers `agent.handoff` and falls back to the raw transcript with
    "you are taking over from a session that was KILLED". If the pointer is
    dropped on the first revive, the second one takes that fallback while a
    current handoff sits on disk unread -- and reports success either way.
    """
    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.handoff = "/home/bodas/data/hotline/handoff.md"
    registry.save()

    revived = revive.rehome(registry, agent, "sid-new", None)

    assert revived.handoff == "/home/bodas/data/hotline/handoff.md"


def test_reviving_keeps_the_voice_channel(registry: Registry) -> None:
    """Otherwise the channel keeps existing and nothing points at it any more."""
    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.voice_channel_id = 777
    registry.save()

    revived = revive.rehome(registry, agent, "sid-new", None)

    assert revived.voice_channel_id == 777


def test_the_carried_fields_survive_a_reload(registry: Registry) -> None:
    """Set on the object but never saved is the same bug with a longer fuse."""
    agent = registry.declare("sid-old", "data-f3", "mirror")
    agent.handoff = "/tmp/h.md"
    registry.save()

    revive.rehome(registry, agent, "sid-new", None)

    reloaded = Registry(registry.path)
    assert reloaded.get("sid-new") is not None
    assert reloaded.get("sid-new").handoff == "/tmp/h.md"


# ---- who the brief is actually addressed to --------------------------------
#
# `resume` spawns with `--name <agent>`, so the session renames itself to the
# agent's identity a moment after it starts. The `LiveSession` captured at spawn
# still holds the name the descriptor had BEFORE that rename. Addressing the
# brief to that stale name resolves to nothing, and the failure is silent in the
# worst way: the agent comes up with no idea what it is resuming, while the
# resume prints success, because "started but did not answer" is indistinguish-
# able from a slow first turn.
#
# Found by `data-1e` when a resumed `hotline-ios` sat there having been told
# nothing at all.


def test_the_brief_is_addressed_to_something_that_survives_a_rename(
    registry: Registry, monkeypatch: pytest.MonkeyPatch, corpse: Path
) -> None:
    """The agent renames itself, so the spawn-time display name is not an address."""
    from hotline import cli as cli_module

    # The rename, modelled: spawned as `data-9c`, comes up calling itself
    # `hotline-ios`, which is the name the registry and Discord both use.
    async def spawn(key, cwd=None, bypass=True, timeout=90.0, name=None):
        return FakeSession(session_id="sid-new", name="data-9c")

    import hotline.tmuxen as tmuxen_module

    monkeypatch.setattr(tmuxen_module, "spawn", spawn)
    registry.declare("sid-old", "hotline-ios", "building the iOS app")

    asked: list[str] = []

    async def capture(self, spec, text, *a, **kw):
        asked.append(spec)
        raise ReplyTimeout("never mind the answer; the address is the test")

    monkeypatch.setattr(cli_module.Router, "ask_session", capture)
    monkeypatch.setattr(cli_module, "channels_from_env", lambda: None)
    monkeypatch.setattr(cli_module, "Registry", lambda: registry)

    cli_module._resume("hotline-ios", registry, None, lambda m: None)

    assert asked, "the brief must actually be sent somewhere"
    assert asked[0] != "data-9c", (
        "addressed to the pre-rename display name, which resolves to nothing -- "
        "the agent would come up with no brief and the resume would report success"
    )
    assert asked[0] == "sid-new", "the session id is the address that cannot go stale"


def test_reviving_keeps_the_standing_role(registry: Registry) -> None:
    """`adopt` keeps it and `resume` did not, and both are respawns.

    A demoted `hotline-80` comes back with its name, its channel and its task,
    and stops being sys-admin without anything announcing it -- so its headers
    read as an ordinary peer to every recipient, and the standing role Bogdan
    granted quietly stops applying halfway through its own life.
    """
    registry.declare("sid-old", "hotline-80", "the build")
    registry.grant("hotline-80", "sys-admin", "msg-1", "chan-1")

    revived = revive.rehome(registry, registry.by_name("hotline-80"), "sid-new", None)

    assert revived.privileged
    assert (revived.granted_by, revived.granted_in) == ("msg-1", "chan-1")


def test_the_grant_receipt_travels_with_the_role(registry: Registry) -> None:
    """A role without the message that granted it is the bare claim
    `--provenance` exists to refuse -- worse than no role, because it looks
    checkable and is not."""
    registry.declare("sid-old", "hotline-80", "the build")
    registry.grant("hotline-80", "sys-admin", "msg-1", "chan-1")

    revived = revive.rehome(registry, registry.by_name("hotline-80"), "sid-new", None)

    assert revived.authority == "sys-admin"
    assert revived.granted_by, "a role with no receipt must not be resurrectable"
