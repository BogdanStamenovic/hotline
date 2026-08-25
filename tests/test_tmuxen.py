"""Spawning a session into tmux.

The real work here shells out to tmux and boots a `claude`, which these do not
do. What they pin down is the argv, because two of the mistakes in this file were
invisible from the outside: a session that came up under the wrong name, and a
reused pane that should have been replaced.
"""

from __future__ import annotations

import pytest

from hotline import tmuxen
from hotline.errors import ClaudeLaunchFailed, HotlineError, SessionNotFound


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
    # Spawning goes through the scoped wrapper, and without this the tests shell
    # out to a real systemd-run -- which they did, silently, until it started
    # failing and swallowed the launch error these assert on.
    monkeypatch.setattr(tmuxen, "_detached_tmux", fake)
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


# ---- typing at a live session ----------------------------------------------
#
# Nothing below shells out to tmux and nothing below touches a real session.
# The point of these is the guard, and a guard that is exercised against a real
# pane is a guard that has already failed once to be worth testing.


class FakeSession:
    def __init__(self, pid: int, name: str = "agent", tmux: str | None = "hl-agent:@1.%1"):
        self.pid = pid
        self.name = name
        self.tmux = tmux
        self.session_id = f"sid-{pid}"


@pytest.fixture
def pane(monkeypatch: pytest.MonkeyPatch, tmux: FakeTmux) -> FakeSession:
    """One live session, in a tmux session that exists."""
    session = FakeSession(pid=424242)
    monkeypatch.setattr(tmuxen, "exists", lambda name: name == "hl-agent")
    monkeypatch.setattr(tmuxen, "_by_tmux_session", lambda name, include_self=False: (
        session if name == "hl-agent" else None
    ))
    return session


async def test_interrupt_sends_escape_and_nothing_else(
    tmux: FakeTmux, pane: FakeSession
) -> None:
    """Escape, because SIGINT terminates rather than cancels. If this ever
    becomes a signal, the session it was meant to spare is gone."""
    reached = await tmuxen.interrupt("hl-agent:@1.%1")

    assert reached is pane
    assert tmux.commands == [["send-keys", "-t", "hl-agent:@1.%1", "Escape"]]


async def test_interrupt_refuses_a_tmux_session_that_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmux: FakeTmux
) -> None:
    monkeypatch.setattr(tmuxen, "exists", lambda name: False)
    with pytest.raises(SessionNotFound):
        await tmuxen.interrupt("hl-agent:@1.%1")
    assert tmux.commands == [], "nothing may be typed at a pane that is not there"


async def test_interrupt_refuses_a_pane_no_claude_claims(
    monkeypatch: pytest.MonkeyPatch, tmux: FakeTmux
) -> None:
    """The pane could be one of his shells. A best-effort Escape into it is
    exactly the mistake this refuses to make."""
    monkeypatch.setattr(tmuxen, "exists", lambda name: True)
    monkeypatch.setattr(tmuxen, "_by_tmux_session", lambda name, include_self=False: None)
    with pytest.raises(SessionNotFound):
        await tmuxen.interrupt("hl-mystery:@0.%0")
    assert tmux.commands == []


async def test_interrupt_refuses_hotline_itself(
    monkeypatch: pytest.MonkeyPatch, tmux: FakeTmux
) -> None:
    """The shared `refuse_if_self` check, reached through the tmux path.

    It only works because the lookup asks for `include_self=True`; `discover()`
    hides our own pid by default, which would make this guard unreachable.
    """
    import os

    me = FakeSession(pid=os.getpid(), name="hotline")
    monkeypatch.setattr(tmuxen, "exists", lambda name: True)
    monkeypatch.setattr(tmuxen, "_by_tmux_session", lambda name, include_self=False: me)

    with pytest.raises(HotlineError, match="hotline itself"):
        await tmuxen.interrupt("hl-agent:@1.%1")
    assert tmux.commands == []


async def test_interrupt_looks_the_target_up_with_include_self(
    monkeypatch: pytest.MonkeyPatch, tmux: FakeTmux
) -> None:
    """Pinned separately because it is the difference between a real guard and
    a decorative one, and it is invisible from the outside."""
    asked: list[bool] = []

    def lookup(name, include_self=False):
        asked.append(include_self)
        return FakeSession(pid=424242)

    monkeypatch.setattr(tmuxen, "exists", lambda name: True)
    monkeypatch.setattr(tmuxen, "_by_tmux_session", lookup)
    await tmuxen.interrupt("hl-agent:@1.%1")

    assert asked == [True]


async def test_a_slash_command_is_typed_then_entered(
    tmux: FakeTmux, pane: FakeSession
) -> None:
    """Two keystrokes, in that order, with the text first.

    `ccsocks.inject("/compact")` was observed delivering literal text that the
    model could not act on. The pty is the channel that actually runs it.
    """
    await tmuxen.send_command("hl-agent:@1.%1", "/compact", settle=0)

    assert tmux.commands == [
        ["send-keys", "-t", "hl-agent:@1.%1", "/compact"],
        ["send-keys", "-t", "hl-agent:@1.%1", "Enter"],
    ]


async def test_a_slash_command_is_refused_for_a_pane_no_claude_claims(
    monkeypatch: pytest.MonkeyPatch, tmux: FakeTmux
) -> None:
    monkeypatch.setattr(tmuxen, "exists", lambda name: True)
    monkeypatch.setattr(tmuxen, "_by_tmux_session", lambda name, include_self=False: None)
    with pytest.raises(SessionNotFound):
        await tmuxen.send_command("hl-mystery:@0.%0", "/compact", settle=0)
    assert tmux.commands == []


async def test_a_failing_tmux_is_raised_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, pane: FakeSession
) -> None:
    """A silent no-op here would report a cancelled turn that never cancelled."""

    class Failing:
        def __call__(self, *args: str, check: bool = True):
            class Result:
                returncode = 1
                stdout = ""
                stderr = "no such pane"

            return Result()

    monkeypatch.setattr(tmuxen, "_tmux", Failing())
    with pytest.raises(HotlineError, match="no such pane"):
        await tmuxen.interrupt("hl-agent:@1.%1")


def test_the_session_list_is_one_call_not_one_per_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A roster recomputes on every request and on every long-poll pass. One
    `has-session` per agent per pass is a subprocess storm for a question
    `list-sessions` answers once."""

    class Listing:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, *args: str, check: bool = True):
            self.calls += 1

            class Result:
                returncode = 0
                stdout = "hl-one\nhl-two\n\n"

            return Result()

    fake = Listing()
    monkeypatch.setattr(tmuxen, "_tmux", fake)
    assert tmuxen.sessions() == {"hl-one", "hl-two"}
    assert fake.calls == 1


def test_no_tmux_server_is_no_sessions_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failing:
        def __call__(self, *args: str, check: bool = True):
            class Result:
                returncode = 1
                stdout = ""
                stderr = "no server running"

            return Result()

    monkeypatch.setattr(tmuxen, "_tmux", Failing())
    assert tmuxen.sessions() == set()
