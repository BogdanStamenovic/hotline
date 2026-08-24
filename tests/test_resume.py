"""Reviving an agent that is no longer running.

The interesting cases are the ones where the agent did NOT finish tidily. A
session killed by a crash, an OOM or a daemon restart never writes a handoff,
and those are exactly the ones worth reviving -- so "no handoff" has to mean
"read the transcript instead", not "refuse".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hotline import cli
from hotline.agents import Registry
from hotline.ccsocks import LiveSession


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

    def retopic(self, channel_id: int, topic: str) -> None:
        pass


@pytest.fixture
def revive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Drive `_resume` with the tmux spawn and the model call stubbed out."""
    seeds: list[str] = []

    async def fake_spawn(key: str, cwd: str | None = None, **kw: Any) -> LiveSession:
        return LiveSession(
            pid=1, session_id="sid-new", cwd="/tmp", name=key,
            socket_path="/tmp/x.sock", token="t", started_at=0,
            kind="interactive", status="idle", entrypoint="cli", tmux=f"hl-{key}",
        )

    class FakeReply:
        text = "understood"

    class FakeRouter:
        async def ask_session(self, name: str, seed: str, timeout: float = 0.0) -> Any:
            seeds.append(seed)
            return FakeReply()

    from hotline import tmuxen
    monkeypatch.setattr(tmuxen, "spawn", fake_spawn)
    monkeypatch.setattr(cli, "Router", FakeRouter)

    def run(registry: Registry, name: str, channels: FakeChannels | None) -> tuple[int, str]:
        monkeypatch.setattr(cli, "channels_from_env", lambda: channels)
        code = cli._resume(name, registry, None, lambda m: None)
        return code, (seeds[-1] if seeds else "")

    return run


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    return Registry(path=tmp_path / "agents.json")


def test_an_agent_killed_without_a_handoff_resumes_from_its_transcript(
    registry: Registry, revive: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case that matters: nothing was written down, but the record survives."""
    corpse = tmp_path / "sid-old.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(cli, "transcript_path", lambda sid: corpse)
    registry.declare("sid-old", "data-f3", "mirror the ollama server")

    code, seed = revive(registry, "data-f3", None)

    assert code == 0
    assert str(corpse) in seed
    assert "KILLED" in seed, "the replacement must know it is reading a corpse"
    assert "verify" in seed.lower(), "claims in a transcript are not results"


def test_a_handoff_is_still_preferred_when_there_is_one(
    registry: Registry, revive: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corpse = tmp_path / "sid-old.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(cli, "transcript_path", lambda sid: corpse)
    handoff = tmp_path / "handoff.md"
    handoff.write_text("the state is X")
    registry.declare("sid-old", "data-f3", "mirror the ollama server")
    registry.complete("sid-old", handoff=str(handoff))

    code, seed = revive(registry, "data-f3", None)

    assert code == 0
    assert "the state is X" in seed
    assert str(corpse) not in seed


def test_resuming_refuses_when_both_the_handoff_and_the_transcript_are_gone(
    registry: Registry, revive: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "transcript_path", lambda sid: None)
    registry.declare("sid-old", "data-f3", "mirror the ollama server")

    code, _ = revive(registry, "data-f3", None)

    assert code == 1


def test_a_live_channel_is_kept_rather_than_duplicated(
    registry: Registry, revive: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A killed agent still owns the thread Bogdan has been reading."""
    corpse = tmp_path / "sid-old.jsonl"
    corpse.write_text("{}\n")
    monkeypatch.setattr(cli, "transcript_path", lambda sid: corpse)
    agent = registry.declare("sid-old", "data-f3", "mirror the ollama server")
    agent.channel_id = 4242
    registry.save()
    channels = FakeChannels(present={4242})

    code, _ = revive(registry, "data-f3", channels)

    assert code == 0
    assert channels.created == [], "it already had a channel"
    assert registry.get("sid-new") is not None
    assert registry.get("sid-new").channel_id == 4242  # type: ignore[union-attr]


def test_a_deleted_channel_is_recreated(
    registry: Registry, revive: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--done` deletes the channel, so resuming a finished agent needs a new one."""
    handoff = tmp_path / "handoff.md"
    handoff.write_text("state")
    monkeypatch.setattr(cli, "transcript_path", lambda sid: None)
    agent = registry.declare("sid-old", "data-f3", "mirror the ollama server")
    agent.channel_id = 4242
    registry.complete("sid-old", handoff=str(handoff))
    channels = FakeChannels(present=set())

    code, _ = revive(registry, "data-f3", channels)

    assert code == 0
    assert channels.created == ["data-f3"]
    assert registry.get("sid-new").channel_id == 9999  # type: ignore[union-attr]
