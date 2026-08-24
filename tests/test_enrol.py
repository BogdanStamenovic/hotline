"""Sessions Bogdan starts by talking must get a channel of their own.

Declaring used to be cooperative -- an agent registered itself if it thought to.
That works for agents spawned from a script that tells them to and fails for
exactly the ones he starts by talking, which have no idea they are supposed to.
The result was every such agent narrating into #general. He asked for the
opposite rule, so this is not cooperative any more.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import FakeWorld

import hotline.pool as pool_module
from hotline.agents import Registry
from hotline.pool import SessionPool


@pytest.fixture
def world(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    return FakeWorld(fake_claude, monkeypatch, pool_module)


@pytest.fixture(autouse=True)
def no_standin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SessionPool, "use_standin", False, raising=False)


class FakeChannels:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    def create_text(self, name: str, topic: str = "", parent_id: int | None = None) -> int:
        self.created.append((name, topic))
        return 1234

    def exists(self, channel_id: int) -> bool:
        return True


@pytest.fixture
def guild(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeChannels:
    channels = FakeChannels()
    monkeypatch.setattr("hotline.channels.from_env", lambda: channels)
    monkeypatch.setenv("HOTLINE_AGENTS_FILE", str(tmp_path / "agents.json"))
    return channels


async def test_a_session_he_starts_gets_registered_and_given_a_channel(
    world: FakeWorld, guild: FakeChannels
) -> None:
    await SessionPool().ask("discord-general", "look at the deploy logs")

    known = list(Registry().agents.values())
    assert len(known) == 1
    assert known[0].channel_id == 1234
    assert guild.created and guild.created[0][0] == known[0].name


async def test_the_opening_message_becomes_the_provisional_task(
    world: FakeWorld, guild: FakeChannels
) -> None:
    """It is the best guess available before the agent has done anything, and
    `--declare` retasks in place once it knows better."""
    await SessionPool().ask("discord-general", "look at the deploy logs")

    agent = next(iter(Registry().agents.values()))
    assert agent.task == "look at the deploy logs"


async def test_a_second_message_does_not_mint_a_second_agent(
    world: FakeWorld, guild: FakeChannels
) -> None:
    p = SessionPool()
    await p.ask("discord-general", "first")
    await p.ask("discord-general", "second")

    assert len(Registry().agents) == 1
    assert len(guild.created) == 1


async def test_a_discord_failure_costs_a_channel_not_the_session(
    world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A running session is worth more than a tidy registry."""
    monkeypatch.setenv("HOTLINE_AGENTS_FILE", str(tmp_path / "agents.json"))

    def explode() -> None:
        raise RuntimeError("discord is down")

    monkeypatch.setattr("hotline.channels.from_env", explode)

    _, reply = await SessionPool().ask("discord-general", "carry on regardless")

    assert reply.text, "the session still answered"
