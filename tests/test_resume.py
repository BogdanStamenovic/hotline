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
