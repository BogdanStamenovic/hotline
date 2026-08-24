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


# ---- which voice channel counts as ours ---------------------------------
#
# One call runs at a time -- one GPU, one Whisper, one Piper, and Bogdan can only
# stand in one room anyway -- so agents get a voice channel each and the hardware
# constraint resolves itself: whichever one he walks into becomes the call.
#
# NOT covered here, and it needs a human: the bot actually joining when he enters
# an agent's channel. That path runs inside py-cord's voice stack and the only
# honest way to exercise it was to have a second bot speak, which meant widening
# HOTLINE_VOICE_ALLOWED_IDS -- and that setting lets another bot talk into a
# root-equivalent shell, so it was removed rather than kept for testing.


@dataclass
class FakeVoiceChannel:
    id: int
    name: str


class VoiceGate(HotlineBot):
    def __init__(self, configured: int | None = 400000000000000004) -> None:
        self.user_id = USER
        self.voice_channel_id = configured
        self.log = lambda _m: None


@pytest.fixture
def voice_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot_module.discord, "VoiceChannel", FakeVoiceChannel)


def test_the_configured_channel_is_ours(voice_type: None) -> None:
    gate = VoiceGate()
    assert gate._is_ours(FakeVoiceChannel(400000000000000004, "General")) is True


def test_any_agent_channel_is_ours(voice_type: None) -> None:
    """An agent's own voice channel is created lazily and named by prefix, so the
    bot has to answer in one it was never configured with."""
    gate = VoiceGate()
    assert gate._is_ours(FakeVoiceChannel(999, "agent-builder")) is True


def test_someone_elses_voice_channel_is_not_ours(voice_type: None) -> None:
    gate = VoiceGate()
    assert gate._is_ours(FakeVoiceChannel(999, "Movie Night")) is False


def test_nothing_is_ours_when_it_is_not_a_voice_channel(voice_type: None) -> None:
    gate = VoiceGate()
    assert gate._is_ours(None) is False
    assert gate._is_ours(FakeChannel(400000000000000004)) is False


def test_agent_channels_still_count_with_nothing_configured(voice_type: None) -> None:
    """Voice per agent must not depend on DISCORD_VOICE_CHANNEL_ID being set."""
    gate = VoiceGate(configured=None)
    assert gate._is_ours(FakeVoiceChannel(999, "agent-builder")) is True
    assert gate._is_ours(FakeVoiceChannel(999, "General")) is False


def test_the_owning_agent_is_found_by_its_voice_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Walking into an agent's channel is how you say who you want to talk to."""
    from hotline.agents import Registry

    registry = Registry(path=tmp_path / "agents.json")
    agent = registry.declare("sid-1", "builder", "a job")
    agent.voice_channel_id = 777
    registry.save()
    monkeypatch.setattr("hotline.agents.agents_file", lambda: tmp_path / "agents.json")

    gate = VoiceGate()
    found = gate._agent_for_voice(777)
    assert found is not None and found.name == "builder"
    assert gate._agent_for_voice(778) is None


# ---- an agent's own text channel ----------------------------------------
#
# These channels were created, kept in step with the agent's task and deleted on
# `done` -- and never read. Every message typed into one failed the gate and was
# logged as ignored, so they were write-only and looked complete from the outside
# because the channel appeared. Voice got the binding; text never did.


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A registry on disk, since permitted() consults it."""
    import hotline.agents as agents_module

    path = tmp_path / "agents.json"
    monkeypatch.setattr(agents_module, "agents_file", lambda: path)
    return agents_module.Registry(path=path)


AGENT_CHANNEL = 500000000000000005


def test_an_agents_own_channel_is_permitted(registry) -> None:
    agent = registry.declare("sid-1", "builder", "a job")
    agent.channel_id = AGENT_CHANNEL
    registry.save()
    assert Gate().permitted(msg(USER, AGENT_CHANNEL, GUILD)) is True


def test_another_channel_is_still_refused_with_a_registry_present(registry) -> None:
    """The security property the old test pinned, kept: widening the gate to agent
    channels must not open it to every channel in the guild."""
    registry.declare("sid-1", "builder", "a job")  # no channel_id
    assert Gate().permitted(msg(USER, 999999999999999999, GUILD)) is False


def test_a_finished_agents_channel_is_refused(registry) -> None:
    """Its channel is deleted on `done`; if the id is reused, it is not ours."""
    agent = registry.declare("sid-1", "builder", "a job")
    agent.channel_id = AGENT_CHANNEL
    registry.complete("sid-1")
    assert Gate().permitted(msg(USER, AGENT_CHANNEL, GUILD)) is False


def test_anyone_else_in_an_agents_channel_is_still_refused(registry) -> None:
    """The author-id check is the security model and comes first."""
    agent = registry.declare("sid-1", "builder", "a job")
    agent.channel_id = AGENT_CHANNEL
    registry.save()
    assert Gate().permitted(msg(USER + 1, AGENT_CHANNEL, GUILD)) is False


def test_another_guild_is_still_refused_in_an_agent_channel(registry) -> None:
    agent = registry.declare("sid-1", "builder", "a job")
    agent.channel_id = AGENT_CHANNEL
    registry.save()
    assert Gate().permitted(msg(USER, AGENT_CHANNEL, GUILD + 1)) is False


def test_the_owning_agent_is_found_by_its_text_channel(registry) -> None:
    agent = registry.declare("sid-1", "builder", "a job")
    agent.channel_id = AGENT_CHANNEL
    registry.save()
    gate = Gate()
    found = gate._agent_for_text(AGENT_CHANNEL)
    assert found is not None and found.name == "builder"
    assert gate._agent_for_text(AGENT_CHANNEL + 1) is None


def test_text_and_voice_channels_do_not_cross(registry) -> None:
    """They are separate fields; matching the wrong one would route a typed
    message at the voice binding and vice versa."""
    agent = registry.declare("sid-1", "builder", "a job")
    agent.channel_id = AGENT_CHANNEL
    agent.voice_channel_id = AGENT_CHANNEL + 1
    registry.save()
    gate = Gate()
    assert gate._agent_for_text(AGENT_CHANNEL) is not None
    assert gate._agent_for_text(AGENT_CHANNEL + 1) is None
    assert gate._agent_for_voice(AGENT_CHANNEL + 1) is not None
    assert gate._agent_for_voice(AGENT_CHANNEL) is None
