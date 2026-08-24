"""Creating and deleting agent channels.

The transport is faked; what is being tested is the policy around it. Two things
matter more than the rest: that re-declaring does not leave an agent with two
channels, and that nothing here can delete a channel hotline does not own.

That second one is not hypothetical caution. Deleting a Discord channel is
irreversible and takes every message in it with it, the bot has MANAGE_CHANNELS
on a guild containing Bogdan's real channels, and the ids being passed around
come out of a JSON file on disk.
"""

from __future__ import annotations

import pytest

from hotline.channels import PREFIX, TEXT, VOICE, Channels, slug
from hotline.errors import HotlineError


class FakeGuild:
    """A guild that records what was asked of it."""

    def __init__(self) -> None:
        self.channels: list[dict] = [
            {"id": "1", "name": "general", "type": TEXT},
            {"id": "2", "name": "General", "type": VOICE},
        ]
        self.calls: list[tuple[str, str]] = []
        self._next = 100

    def __call__(self, path: str, method: str = "GET", body: dict | None = None):
        self.calls.append((method, path))
        if method == "GET":
            return list(self.channels)
        if method == "POST":
            self._next += 1
            made = {"id": str(self._next), "name": body["name"], "type": body["type"]}
            if body.get("topic"):
                made["topic"] = body["topic"]
            self.channels.append(made)
            return made
        if method == "PATCH":
            cid = path.rsplit("/", 1)[-1]
            for c in self.channels:
                if c["id"] == cid:
                    c.update(body or {})
            return {}
        if method == "DELETE":
            cid = path.rsplit("/", 1)[-1]
            self.channels = [c for c in self.channels if c["id"] != cid]
            return {}
        raise AssertionError(method)


@pytest.fixture
def guild(monkeypatch: pytest.MonkeyPatch) -> FakeGuild:
    fake = FakeGuild()
    monkeypatch.setattr(Channels, "_call", lambda self, p, m="GET", b=None: fake(p, m, b))
    return fake


@pytest.fixture
def channels() -> Channels:
    return Channels("token", 42)


def test_names_are_prefixed_and_slugified() -> None:
    """Discord rewrites channel names anyway; doing it ourselves means the name we
    store is the name it will show, so looking it up later actually finds it."""
    assert slug("Voice Decoder") == f"{PREFIX}voice-decoder"
    assert slug("data-53") == f"{PREFIX}data-53"
    assert slug("!!!") == f"{PREFIX}unnamed"


def test_creating_a_text_channel(guild: FakeGuild, channels: Channels) -> None:
    cid = channels.create_text("builder", topic="rewriting the decoder")
    made = next(c for c in guild.channels if c["id"] == str(cid))
    assert made["name"] == f"{PREFIX}builder"
    assert made["topic"] == "rewriting the decoder"


def test_creating_is_idempotent(guild: FakeGuild, channels: Channels) -> None:
    """Re-declaring is a retask. An agent that reframes its work must not end up
    with two channels."""
    first = channels.create_text("builder")
    second = channels.create_text("builder")
    assert first == second
    assert sum(1 for c in guild.channels if c["name"] == f"{PREFIX}builder") == 1


def test_a_retask_updates_the_topic(guild: FakeGuild, channels: Channels) -> None:
    cid = channels.create_text("builder", topic="the old job")
    channels.retopic(cid, "the new job")
    assert next(c for c in guild.channels if c["id"] == str(cid))["topic"] == "the new job"


def test_deleting_an_agent_channel(guild: FakeGuild, channels: Channels) -> None:
    cid = channels.create_text("builder")
    assert channels.delete(cid) is True
    assert all(c["id"] != str(cid) for c in guild.channels)


def test_refuses_to_delete_a_channel_it_does_not_own(
    guild: FakeGuild, channels: Channels
) -> None:
    """The guard that stands between a caller bug and #general."""
    with pytest.raises(HotlineError, match="not an agent channel"):
        channels.delete(1)
    assert any(c["name"] == "general" for c in guild.channels)


def test_deleting_something_already_gone_is_not_an_error(
    guild: FakeGuild, channels: Channels
) -> None:
    """A `done` that runs twice, or after someone deleted the channel by hand,
    must not fail the completion."""
    assert channels.delete(9999) is False


def test_owned_lists_only_our_channels(guild: FakeGuild, channels: Channels) -> None:
    channels.create_text("one")
    channels.create_voice("two")
    assert sorted(c["name"] for c in channels.owned()) == [
        f"{PREFIX}one", f"{PREFIX}two",
    ]


def test_voice_and_text_with_the_same_name_are_separate(
    guild: FakeGuild, channels: Channels
) -> None:
    """`find` matches on name alone, so the type check is what stops a lazily
    created voice channel from being handed back as the agent's text channel."""
    text = channels.create_text("builder")
    voice = channels.create_voice("builder")
    assert text != voice
    kinds = sorted(c["type"] for c in guild.channels if c["name"] == f"{PREFIX}builder")
    assert kinds == [TEXT, VOICE]


def test_from_env_needs_both_a_token_and_a_guild() -> None:
    from hotline.channels import from_env

    assert from_env({}) is None
    assert from_env({"HOTLINE_BOT_TOKEN": "t"}) is None
    assert from_env({"HOTLINE_BOT_TOKEN": "t", "DISCORD_GUILD_ID": "nonsense"}) is None
    assert from_env({"HOTLINE_BOT_TOKEN": "t", "DISCORD_GUILD_ID": "42"}) is not None
