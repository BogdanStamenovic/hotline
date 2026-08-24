"""The escalation ladder, driven by a fake clock.

Timing is the entire behaviour here and real time would make these tests either
slow or flaky, so the clock, the sleep and the siren are all injected. The HTTP
layer is faked at `_request` so the ladder is exercised without touching Discord.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

import hotline.pager as pager_module
from hotline.pager import (
    DEFAULT_LADDER,
    SPAM_EVERY,
    SPAM_START,
    Pager,
    PagerError,
    PageResult,
    build_ladder,
)


class FakeDiscord:
    """Enough of the Discord REST surface for the ladder to run against."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.next_id = 1000
        self.replies: dict[str, list[dict]] = {}
        self.dm_allowed = True
        self.fail_sends = False

    def __call__(self, path, token, method="GET", body=None, timeout=20.0):
        if method == "POST" and path.startswith("/channels/") and path.endswith("/messages"):
            if self.fail_sends:
                raise PagerError("send failed")
            channel = path.split("/")[2]
            self.sent.append((channel, body["content"]))
            self.next_id += 1
            return {"id": str(self.next_id)}
        if method == "POST" and path == "/users/@me/channels":
            if not self.dm_allowed:
                raise PagerError("cannot dm")
            return {"id": "dm-1"}
        if method == "GET" and "/messages?after=" in path:
            channel = path.split("/")[2]
            return self.replies.pop(channel, [])
        raise AssertionError(f"unexpected call {method} {path}")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def discord(monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    fake = FakeDiscord()
    monkeypatch.setattr(pager_module, "_request", fake)
    return fake


def build(discord: FakeDiscord, clock: Clock, siren_ok: bool = True):
    fired: list[int] = []

    def siren() -> bool:
        fired.append(1)
        return siren_ok

    pager = Pager("token", "user-1", "chan-1", siren=siren, sleep=clock.sleep, now=clock.now)
    return pager, fired


def reply_from(user_id: str, text: str) -> dict:
    return {"author": {"id": user_id}, "content": text}


def test_answer_in_the_channel_ends_the_page(discord: FakeDiscord) -> None:
    clock = Clock()
    pager, fired = build(discord, clock)
    discord.replies["chan-1"] = [reply_from("user-1", "yes, go ahead")]
    result = pager.page("may I push?", timeout=600)
    assert result.answered
    assert result.reply == "yes, go ahead"
    assert fired == []
    assert any("Got it" in text for _c, text in discord.sent)


def test_the_mention_is_explicitly_allowed(discord: FakeDiscord) -> None:
    """An @mention that does not actually notify is the whole feature failing
    silently, so allowed_mentions is not left to Discord's defaults."""
    clock = Clock()
    pager, _ = build(discord, clock)
    discord.replies["chan-1"] = [reply_from("user-1", "ok")]
    pager.page("question", timeout=60)
    assert "<@user-1>" in discord.sent[0][1]


def test_it_also_dms(discord: FakeDiscord) -> None:
    clock = Clock()
    pager, _ = build(discord, clock)
    discord.replies["chan-1"] = [reply_from("user-1", "ok")]
    result = pager.page("question", timeout=60)
    assert "dm" in result.escalations
    assert any(channel == "dm-1" for channel, _ in discord.sent)


def test_a_closed_dm_does_not_fail_the_page(discord: FakeDiscord) -> None:
    """He may have DMs from server members turned off. That is not an error."""
    clock = Clock()
    discord.dm_allowed = False
    pager, _ = build(discord, clock)
    discord.replies["chan-1"] = [reply_from("user-1", "ok")]
    result = pager.page("question", timeout=60)
    assert result.answered
    assert "dm" not in result.escalations


def test_an_answer_by_dm_counts(discord: FakeDiscord) -> None:
    clock = Clock()
    pager, _ = build(discord, clock)
    discord.replies["dm-1"] = [reply_from("user-1", "answered on my phone")]
    result = pager.page("question", timeout=600)
    assert result.answered
    assert result.reply == "answered on my phone"


def test_somebody_elses_message_is_not_an_answer(discord: FakeDiscord) -> None:
    clock = Clock()
    pager, _ = build(discord, clock)
    discord.replies["chan-1"] = [reply_from("someone-else", "I'll take this one")]
    result = pager.page("question", timeout=30)
    assert not result.answered


def test_it_escalates_on_schedule_and_then_gives_up(discord: FakeDiscord) -> None:
    clock = Clock()
    pager, fired = build(discord, clock)
    result = pager.page("question", timeout=1800)
    assert not result.answered
    # Quiet for two minutes, then a mention every 30s for the rest of the page.
    assert result.escalations.count("nudge") == int((1800 - SPAM_START) // SPAM_EVERY)
    assert result.escalations.count("siren") == 2
    assert len(fired) == 2
    # The last thing he sees is that nobody is waiting on him any more.
    assert "giving up" in discord.sent[-1][1]


def test_the_dm_goes_out_before_the_channel_post(discord: FakeDiscord) -> None:
    """The DM is what reaches his lock screen, so it must not queue behind the post."""
    clock = Clock()
    pager, _ = build(discord, clock)
    result = pager.page("question", timeout=30)
    assert result.escalations[:2] == ["dm", "post"]


def test_the_spam_cadence_is_regular(discord: FakeDiscord) -> None:
    steps = build_ladder(700)
    mentions = [t for t, action in steps if action == "nudge"]
    assert mentions[0] == SPAM_START
    assert all(b - a == SPAM_EVERY for a, b in pairwise(mentions))
    assert mentions[-1] < 700


def test_no_wait_posts_the_page_and_says_nothing_else(discord: FakeDiscord) -> None:
    """`--no-wait` used to be faked with a 0.1s timeout, so it posted the page and
    then immediately posted "giving up after 0 minutes with no answer" -- telling
    him nobody wanted his attention in the same breath as asking for it."""
    clock = Clock()
    pager, fired = build(discord, clock)
    result = pager.page("question", wait=False)
    assert result.escalations == ["dm", "post"]
    assert not result.answered
    assert not any("giving up" in text for _, text in discord.sent)
    assert not fired


def test_waiting_is_still_the_default(discord: FakeDiscord) -> None:
    """The fix must not turn every page into a fire-and-forget."""
    clock = Clock()
    pager, _ = build(discord, clock)
    result = pager.page("question", timeout=30)
    assert any("giving up" in text for _, text in discord.sent)
    assert result.waited_seconds >= 30


def test_a_broken_siren_is_recorded_not_fatal(discord: FakeDiscord) -> None:
    """PipeWire could be down. That is worth knowing about; it is not worth
    abandoning the page over."""
    clock = Clock()
    pager, _ = build(discord, clock, siren_ok=False)
    result = pager.page("question", timeout=1800)
    assert "siren-failed" in result.escalations
    assert "siren" not in result.escalations


def test_the_siren_can_be_disabled(discord: FakeDiscord) -> None:
    clock = Clock()
    pager, fired = build(discord, clock)
    quiet = [step for step in DEFAULT_LADDER if step[1] != "siren"]
    pager.page("question", timeout=1800, ladder=quiet)
    assert fired == []


def test_an_undeliverable_page_raises(discord: FakeDiscord) -> None:
    """If the very first post fails there is no page at all, and the caller has to
    know that rather than believing a human was asked."""
    clock = Clock()
    discord.fail_sends = True
    pager, _ = build(discord, clock)
    with pytest.raises(PagerError):
        pager.page("question", timeout=60)


def test_from_env_names_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HOTLINE_BOT_TOKEN", "DISCORD_USER_ID", "DISCORD_TEXT_CHANNEL_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PagerError) as exc:
        pager_module.from_env()
    for name in ("HOTLINE_BOT_TOKEN", "DISCORD_USER_ID", "DISCORD_TEXT_CHANNEL_ID"):
        assert name in str(exc.value)


def test_result_defaults() -> None:
    assert PageResult(answered=False).escalations == []


def test_a_long_message_is_split_not_truncated(discord: FakeDiscord) -> None:
    """The bug this exists to prevent: a status message was cut at 1900 chars and
    delivered looking complete. Bogdan read it and had no way to know the end was
    missing. Silent truncation is worse than an error."""
    clock = Clock()
    pager, _ = build(discord, clock)
    body = "sentence. " * 600  # ~6000 chars
    discord.replies["chan-1"] = [reply_from("user-1", "ok")]
    pager.page(body, timeout=60)

    posted = [text for channel, text in discord.sent if channel == "chan-1"]
    delivered = "".join(posted)
    assert len(posted) > 1
    assert all(len(part) <= 2000 for part in posted)
    # Nothing was dropped: every sentence survives somewhere.
    assert delivered.count("sentence.") == 600
    assert "(1/" in posted[0]


def test_send_returns_the_first_part_as_the_reply_anchor(discord: FakeDiscord) -> None:
    """Replies are counted from `after=<id>`, so it has to be the first part or a
    reply arriving between parts would be missed."""
    clock = Clock()
    pager, _ = build(discord, clock)
    first = pager.send("chan-1", "x " * 3000)
    ids = sorted(int(i) for i in [first])
    assert int(first) == 1001  # the first POST of the batch
    assert ids
