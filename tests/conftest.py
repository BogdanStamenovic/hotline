from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Nothing in the suite may touch the real agent registry.

    Autouse and unconditional, because the failure is silent and only shows up
    later as phantom agents in `hotline --agents`. It happened: auto-enrolling
    sessions the pool spawns meant the pool tests started declaring their fake
    tmux sessions into `~/.local/state/hotline/agents.json`, and four of them
    were sitting in the live registry before anyone looked. A test that reaches
    real state is a bug in the test, not a quirk of it.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    # And no test may ever hold a real bot token. This is not hypothetical: the
    # credentials are exported in the shell hotline is developed from, so
    # `channels.from_env()` handed the suite a LIVE Discord client, and once
    # sessions started auto-enrolling, the pool tests created real channels in
    # the real guild until Discord rate-limited them. Scrubbed here rather than
    # in the tests that touch Discord, because the tests that did the damage
    # were the ones with no idea they were touching it at all.
    for name in (
        "HOTLINE_BOT_TOKEN",
        "SENTINEL_BOT_TOKEN",
        "DISCORD_GUILD_ID",
        "DISCORD_USER_ID",
        "DISCORD_TEXT_CHANNEL_ID",
        "DISCORD_VOICE_CHANNEL_ID",
        "HOTLINE_VOICE_ALLOWED_IDS",
        "HOTLINE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    # And no test may post to the phone. `mirror.mirror_sent` defaults to
    # loopback, which on this machine is his LIVE hotline-ios daemon -- so the
    # first suite run after the mirror was wired into `Pager.page` put sixteen
    # fixture strings ("question", "may I push?", "sentence. sentence.") into
    # his real app, under the pager's default source of "an agent". Same shape
    # as the two failures above and found the same way: by looking, after the
    # fact, at the live state the tests had no idea they were writing to.
    monkeypatch.setenv("HOTLINE_MIRROR", "0")
    return state


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway ~/.claude plus a throwaway runtime spool.

    Both are pointed at by environment variable rather than by patching module
    globals, because that is the same path the real hook scripts take -- so the
    tests exercise the configuration surface instead of going around it.
    """
    home = tmp_path / "claude"
    (home / "sessions").mkdir(parents=True)
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOTLINE_CLAUDE_HOME", str(home))
    monkeypatch.setenv("HOTLINE_RUNTIME", str(tmp_path / "run"))
    return home
