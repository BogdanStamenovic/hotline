"""Starting a genuinely separate agent from a channel.

The bug this closes: a pane is named after the conversation key, so a channel's
own session is a singleton. `tmuxen.spawn` returns the existing pane when one is
already there, which means "new session" could close nothing it would not
immediately re-open as the same session. Bogdan asked #general for a new agent,
was told it had started over, and was then answered by the session that was
already running.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import FakeWorld

import hotline.pool as pool_module
from hotline.agents import Registry
from hotline.pool import SessionPool
from hotline.router import parse_utterance


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
        return 4242

    def exists(self, channel_id: int) -> bool:
        return True


@pytest.fixture
def guild(monkeypatch: pytest.MonkeyPatch) -> FakeChannels:
    channels = FakeChannels()
    monkeypatch.setattr("hotline.channels.from_env", lambda: channels)
    return channels


# ---- saying it ------------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "new agent stylize the app on port 8000",
        "start an agent to stylize the app on port 8000",
        "spawn another agent for stylize the app on port 8000",
    ],
)
def test_the_phrasings_that_should_start_an_agent(said: str) -> None:
    route = parse_utterance(said)
    assert route.action == "new_agent"
    assert route.target and "stylize the app" in route.target


@pytest.mark.parametrize("said", ["the new agent is broken", "new session", "agents"])
def test_the_phrasings_that_should_not(said: str) -> None:
    """A sentence *about* agents is a question, not a command to make one."""
    assert parse_utterance(said).action != "new_agent"


# ---- doing it -------------------------------------------------------------


async def test_it_is_a_different_session_from_the_channel_own_one(
    world: FakeWorld, guild: FakeChannels
) -> None:
    """The whole bug: the channel's own session is a singleton, so a new agent
    must not be keyed on the channel."""
    p = SessionPool()
    await p.ask("discord-general", "hello")
    mine = world.spawned[-1]

    _, reply = await p.ask("discord-general", "new agent stylize the app")

    assert world.spawned[-1] != mine, "it reused the channel's pane"
    assert "Started" in reply.text


async def test_two_agents_started_together_do_not_collide(
    world: FakeWorld, guild: FakeChannels
) -> None:
    p = SessionPool()
    await p.ask("discord-general", "new agent do the first thing")
    await p.ask("discord-general", "new agent do the second thing")

    assert len(set(world.spawned)) == len(world.spawned), "two agents shared a pane"
    assert len(Registry().agents) == 2


async def test_it_gets_its_own_channel_named_after_itself(
    world: FakeWorld, guild: FakeChannels
) -> None:
    p = SessionPool()

    _, reply = await p.ask("discord-general", "new agent stylize the app on port 8000")

    assert guild.created, "an agent he starts must get a thread"
    _name, topic = guild.created[-1]
    assert topic == "stylize the app on port 8000"
    assert "4242" in reply.text, "he must be told where to talk to it"


async def test_the_task_is_handed_over_not_just_recorded(
    world: FakeWorld, guild: FakeChannels
) -> None:
    """He asked for an agent to do a thing, not for an idle session."""
    p = SessionPool()

    await p.ask("discord-general", "new agent stylize the app on port 8000")

    assert ("stylize the app on port 8000") in [text for _, text in world.delivered]


async def test_the_task_arrives_labelled_as_coming_from_hotline(
    world: FakeWorld, guild: FakeChannels
) -> None:
    from hotline.provenance import parse

    p = SessionPool()
    await p.ask("discord-general", "new agent stylize the app")

    _, wire = world.wire[-1]
    record = parse(wire)
    assert record is not None and record["kind"] == "system"


async def test_with_no_task_it_asks_rather_than_spawning(
    world: FakeWorld, guild: FakeChannels
) -> None:
    """An agent with no task gets a name and a channel and no idea what to do."""
    p = SessionPool()

    _, reply = await p.ask("discord-general", "new agent")

    assert world.spawned == [], "nothing should have been started"
    assert "Say what it should work on" in reply.text


async def test_resuming_a_live_agent_by_its_agent_name_connects_instead(
    world: FakeWorld, guild: FakeChannels
) -> None:
    """An agent and the session it lives in have different names -- `hotline-80`
    versus `data-88`. Matching only session names meant `resume hotline-80` found
    nothing live, declined to revive something that was alive, and fell through
    to being delivered as an ordinary message: Bogdan's request to resume an
    agent was answered by that agent, with no explanation."""
    from hotline.ccsocks import discover

    p = SessionPool()
    await p.ask("discord-general", "hello")
    live = discover()
    assert live, "the fake world should have left a session behind"
    # The agent name deliberately differs from the session name it lives in --
    # that difference is the whole bug.
    Registry().declare(live[0].session_id, "hotline-80", "the build")

    _, reply = await p.ask("discord-general", "resume hotline-80")

    assert "never stopped" in reply.text
    assert "nothing to resurrect" in reply.text
