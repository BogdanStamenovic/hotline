"""Spawning a session into tmux.

The real work here shells out to tmux and boots a `claude`, which these do not
do. What they pin down is the argv, because two of the mistakes in this file were
invisible from the outside: a session that came up under the wrong name, and a
reused pane that should have been replaced.
"""

from __future__ import annotations

import pytest

from hotline import tmuxen
from hotline.errors import ClaudeLaunchFailed


class FakeTmux:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, *args: str, check: bool = True):
        self.commands.append(list(args))

        class Result:
            returncode = 0
            stdout = ""

        return Result()

    @property
    def new_session(self) -> list[str]:
        return next(c for c in self.commands if c and c[0] == "new-session")


@pytest.fixture
def tmux(monkeypatch: pytest.MonkeyPatch) -> FakeTmux:
    fake = FakeTmux()
    monkeypatch.setattr(tmuxen, "_tmux", fake)
    monkeypatch.setattr(tmuxen, "exists", lambda name: False)
    return fake


def test_the_tmux_name_is_derived_from_the_conversation() -> None:
    """`tmux attach -t hl-discord` has to reach Discord's session. Naming after
    the conversation rather than the pid is the whole point -- you know which
    channel you were talking on, not what pid it got."""
    assert tmuxen.tmux_name("discord-123") == "hl-discord-123"
    assert tmuxen.tmux_name("Voice Call!") == "hl-voice-call"
    assert tmuxen.tmux_name("") == "hl-session"


async def test_spawn_passes_the_display_name(tmux: FakeTmux) -> None:
    """The bug this exists for: a local `name = tmux_name(key)` shadowed the
    `name` parameter, so `--name` got the tmux session name. A resumed agent came
    back called `hl-demo-res` instead of `demo-res`, losing the identity you
    resumed it by.
    """
    with pytest.raises(ClaudeLaunchFailed):  # never registers; the argv is the point
        await tmuxen.spawn("demo-res", cwd="/tmp", timeout=0.01, name="demo-res")
    argv = tmux.new_session
    assert "--name" in argv
    assert argv[argv.index("--name") + 1] == "demo-res"
    assert argv[argv.index("-s") + 1] == "hl-demo-res"


async def test_spawn_without_a_name_does_not_pass_the_flag(tmux: FakeTmux) -> None:
    with pytest.raises(ClaudeLaunchFailed):
        await tmuxen.spawn("plain", cwd="/tmp", timeout=0.01)
    assert "--name" not in tmux.new_session


async def test_bypass_is_on_by_default_and_can_be_turned_off(tmux: FakeTmux) -> None:
    with pytest.raises(ClaudeLaunchFailed):
        await tmuxen.spawn("a", timeout=0.01)
    assert "bypassPermissions" in tmux.new_session
    tmux.commands.clear()
    with pytest.raises(ClaudeLaunchFailed):
        await tmuxen.spawn("b", timeout=0.01, bypass=False)
    assert "bypassPermissions" not in tmux.new_session
