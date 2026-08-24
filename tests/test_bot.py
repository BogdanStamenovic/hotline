"""The Discord bridge's gate and its message splitting.

The gate is the security model. A message that gets past it drives a session with
`bypassPermissions` on a box with `%wheel NOPASSWD: ALL`, so "permitted" means
"root-equivalent". These tests exist because guild membership is *not* sufficient
and it would be very easy to write code where it accidentally is.

py-cord objects are faked rather than constructed: `discord.Message` needs a real
connection state, and none of that is what is under test.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

import hotline.bot as bot_module
from hotline.bot import HotlineBot, chunk
from hotline.config import page_claim

# Deliberately invented snowflakes. The real ids live only in .env -- putting
# them here once meant they were staged for a public push, and the pre-commit
# secret scan is what caught it.
USER = 100000000000000001
GUILD = 200000000000000002
CHANNEL = 300000000000000003


@dataclass
class FakeAuthor:
    id: int
    bot: bool = False


@dataclass
class FakeGuild:
    id: int


@dataclass
class FakeChannel:
    id: int


@dataclass
class FakeDM:
    id: int = 999


@dataclass
class FakeMessage:
    author: FakeAuthor
    channel: object
    guild: FakeGuild | None
    content: str = "hello"


class Gate(HotlineBot):
    """Only the gate, with none of py-cord's constructor."""

    def __init__(self, guild_id: int | None = GUILD, channel_id: int | None = CHANNEL) -> None:
        self.user_id = USER
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.log = lambda _m: None


@pytest.fixture(autouse=True)
def dm_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """`permitted` uses isinstance against discord.DMChannel; point that at the fake."""
    monkeypatch.setattr(bot_module.discord, "DMChannel", FakeDM)


def msg(user: int, channel: int, guild: int | None, bot: bool = False) -> FakeMessage:
    return FakeMessage(
        author=FakeAuthor(id=user, bot=bot),
        channel=FakeChannel(id=channel),
        guild=FakeGuild(id=guild) if guild else None,
    )


def test_bogdan_in_the_right_channel_is_allowed() -> None:
    assert Gate().permitted(msg(USER, CHANNEL, GUILD))


def test_anyone_else_is_refused_even_in_the_right_channel() -> None:
    """The one that matters. Being in the server must not be enough."""
    assert not Gate().permitted(msg(USER + 1, CHANNEL, GUILD))


def test_bots_are_refused() -> None:
    """The pager posts as this same bot; answering itself would loop."""
    assert not Gate().permitted(msg(USER, CHANNEL, GUILD, bot=True))


def test_another_guild_is_refused() -> None:
    assert not Gate().permitted(msg(USER, CHANNEL, GUILD + 1))


def test_another_channel_in_the_right_guild_is_refused() -> None:
    assert not Gate().permitted(msg(USER, CHANNEL + 1, GUILD))


def test_dms_from_bogdan_are_allowed() -> None:
    message = FakeMessage(author=FakeAuthor(id=USER), channel=FakeDM(), guild=None)
    assert Gate().permitted(message)


def test_dms_from_anyone_else_are_refused() -> None:
    message = FakeMessage(author=FakeAuthor(id=USER + 1), channel=FakeDM(), guild=None)
    assert not Gate().permitted(message)


def test_unconfigured_guild_and_channel_do_not_open_the_gate_to_others() -> None:
    """Leaving the ids unset widens which channels work; it must never widen *who*."""
    gate = Gate(guild_id=None, channel_id=None)
    assert gate.permitted(msg(USER, 1, 2))
    assert not gate.permitted(msg(USER + 1, 1, 2))


# ---- the page claim ----------------------------------------------------


def test_no_claim_means_the_bridge_is_listening(fake_claude) -> None:
    assert not HotlineBot.page_outstanding()


def test_an_active_claim_mutes_the_bridge(fake_claude) -> None:
    path = page_claim()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()))
    assert HotlineBot.page_outstanding()


def test_a_stale_claim_is_ignored(fake_claude) -> None:
    """A pager killed mid-page must not mute the bridge forever."""
    path = page_claim()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time() - bot_module.CLAIM_MAX_AGE - 10))
    assert not HotlineBot.page_outstanding()


def test_a_corrupt_claim_is_ignored(fake_claude) -> None:
    path = page_claim()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a number")
    assert not HotlineBot.page_outstanding()


# ---- chunking ----------------------------------------------------------


def test_short_answers_are_one_part() -> None:
    assert chunk("hello") == ["hello"]


def test_an_empty_answer_still_says_something() -> None:
    """Discord rejects an empty message, so silence would look like a crash."""
    assert chunk("   ") == ["(no answer)"]


def test_long_answers_split_under_the_limit() -> None:
    """Discord silently rejects an oversized message -- a merely long answer would
    vanish entirely, which is the worst failure for a thing that delivers answers."""
    parts = chunk("word " * 2000)
    assert len(parts) > 1
    assert all(len(part) <= 1900 for part in parts)


def test_splitting_prefers_paragraph_boundaries() -> None:
    text = ("a" * 1000) + "\n\n" + ("b" * 1000)
    parts = chunk(text)
    assert parts[0] == "a" * 1000
    assert parts[1] == "b" * 1000


def test_unbroken_text_is_still_split() -> None:
    parts = chunk("x" * 5000)
    assert all(len(part) <= 1900 for part in parts)
    assert "".join(parts) == "x" * 5000
