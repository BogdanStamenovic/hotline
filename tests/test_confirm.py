"""Holding a message in #general until Bogdan says where it is going.

Bogdan asked for this after messages of his landed in sessions he did not mean,
including a bare "Resume" that silently spawned a whole new agent. The point is
that he is told the destination BEFORE the message is delivered, not after.

The interesting cases are the ones where he does not answer the question: a
"yes" must not be delivered as a message, and neither must a held message he has
visibly moved on from.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import FakeWorld

import hotline.pool as pool_module
from hotline.pool import SessionPool

GENERAL = "discord-general"


@pytest.fixture
def world(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    return FakeWorld(fake_claude, monkeypatch, pool_module)


@pytest.fixture(autouse=True)
def no_standin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SessionPool, "use_standin", False, raising=False)


@pytest.fixture(autouse=True)
def no_enrol(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-enrolment talks to Discord; it has its own tests."""
    monkeypatch.setattr(SessionPool, "_enrol", lambda self, *a, **k: None)


def pool() -> SessionPool:
    return SessionPool(confirm_keys={GENERAL})


async def test_the_first_message_is_held_and_not_delivered(world: FakeWorld) -> None:
    _, reply = await pool().ask(GENERAL, "run the deploy")

    assert "Send it?" in reply.text
    assert "run the deploy" in reply.text, "he must see WHAT he is confirming"
    assert world.delivered == [], "nothing may reach a session before he says yes"


async def test_yes_sends_the_held_message_not_the_word_yes(world: FakeWorld) -> None:
    p = pool()
    await p.ask(GENERAL, "run the deploy")

    _, reply = await p.ask(GENERAL, "yes")

    assert world.delivered, "the held message should have been delivered"
    assert world.delivered[-1][1] == "run the deploy"
    assert "yes" not in [text for _, text in world.delivered]


async def test_no_drops_it_and_delivers_nothing(world: FakeWorld) -> None:
    p = pool()
    await p.ask(GENERAL, "rm -rf the wrong thing")

    _, reply = await p.ask(GENERAL, "no")

    assert "Dropped" in reply.text
    assert world.delivered == []


async def test_a_new_message_replaces_the_held_one(world: FakeWorld) -> None:
    """He typed something else instead of answering: he has changed his mind, and
    delivering the old text is exactly the failure this guard exists to stop."""
    p = pool()
    await p.ask(GENERAL, "the first thing")

    _, reply = await p.ask(GENERAL, "actually, the second thing")

    assert world.delivered == [], "still nothing sent"
    assert "the second thing" in reply.text
    assert "the first thing" not in reply.text


async def test_it_asks_once_per_target_not_once_per_message(world: FakeWorld) -> None:
    """Asking every time would make the channel unusable."""
    p = pool()
    await p.ask(GENERAL, "first")
    await p.ask(GENERAL, "yes")

    _, reply = await p.ask(GENERAL, "second")

    assert "Send it?" not in reply.text
    assert [text for _, text in world.delivered] == ["first", "second"]


async def test_other_channels_are_never_held(world: FakeWorld) -> None:
    """A per-agent channel IS that agent -- there is no question to ask."""
    _, reply = await pool().ask("discord-agent-channel", "get on with it")

    assert "Send it?" not in reply.text
    assert [text for _, text in world.delivered] == ["get on with it"]


async def test_a_control_command_is_never_held(world: FakeWorld) -> None:
    """`help` is answered by hotline, not sent anywhere, so there is nothing to
    confirm -- and holding it would make the channel impossible to steer."""
    _, reply = await pool().ask(GENERAL, "help")

    assert "Send it?" not in reply.text
    assert "Commands" in reply.text


async def test_connecting_somewhere_new_asks_again(world: FakeWorld) -> None:
    """The confirmation described a target he is no longer pointed at."""
    p = pool()
    await p.ask(GENERAL, "first")
    await p.ask(GENERAL, "yes")
    # A second session for him to move to.
    other = await world.spawn("elsewhere")

    await p.ask(GENERAL, f"connect {other.name}")
    _, reply = await p.ask(GENERAL, "now this")

    assert "Send it?" in reply.text
    assert other.name in reply.text
