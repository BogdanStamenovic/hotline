"""Idle agents sleep only when fully idle, and a message wakes them with their own session."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from helpers import make_session

from hotline import sleep as sleep_mod
from hotline.agents import Registry
from hotline.ccsocks import LiveSession, discover
from hotline.errors import SessionNotFound
from hotline.router import Router

NOW = 2_000_000_000.0


def _session(home: Path, pid: int = 4242, sid: str = "sid-idle", status: str = "idle",
             since: float | None = NOW - 3600, tmux: str = "hl-worker:@0.%0") -> LiveSession:
    make_session(home, pid, "worker", "/tmp", session_id=sid, status=status, tmux=tmux)
    desc = home / "sessions" / f"{pid}.json"
    data = json.loads(desc.read_text())
    if since is not None:
        data["statusUpdatedAt"] = int(since * 1000)
    desc.write_text(json.dumps(data))
    return next(s for s in discover() if s.pid == pid)


def _transcript(home: Path, sid: str, cwd: str, mtime: float | None = None) -> Path:
    path = home / "projects" / ("-" + cwd.strip("/").replace("/", "-")) / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "summary", "leafUuid": "x"}) + "\n"
        + json.dumps({"type": "user", "cwd": cwd, "sessionId": sid}) + "\n"
    )
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


def _proc(tmp_path: Path, children: dict[int, tuple[int, str]]) -> Path:
    """A fake /proc: pid -> (ppid, cmdline)."""
    root = tmp_path / "proc"
    for pid, (ppid, cmd) in children.items():
        d = root / str(pid)
        d.mkdir(parents=True)
        # The comm field may hold spaces and parens; the parser must cope.
        (d / "stat").write_text(f"{pid} (zsh (x) y) S {ppid} 1 1 0")
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())
    root.mkdir(exist_ok=True)
    return root


# ---- what counts as fully idle -------------------------------------------


def test_fully_idle_session_may_sleep(fake_claude: Path, tmp_path: Path) -> None:
    s = _session(fake_claude)
    _transcript(fake_claude, s.session_id, "/tmp", mtime=NOW - 3600)
    assert sleep_mod.busy_reason(s, now=NOW, proc=_proc(tmp_path, {})) is None


def test_mid_turn_is_busy(fake_claude: Path, tmp_path: Path) -> None:
    s = _session(fake_claude, status="busy")
    assert "busy" in sleep_mod.busy_reason(s, now=NOW, proc=_proc(tmp_path, {}))


def test_recently_idle_is_not_yet_asleep(fake_claude: Path, tmp_path: Path) -> None:
    s = _session(fake_claude, since=NOW - 60)
    assert "idle only" in sleep_mod.busy_reason(s, now=NOW, proc=_proc(tmp_path, {}))


def test_a_background_tool_shell_keeps_it_awake(fake_claude: Path, tmp_path: Path) -> None:
    """The bug that killed pm-maker2 and manager-2: idle prompt, work still running."""
    s = _session(fake_claude)
    _transcript(fake_claude, s.session_id, "/tmp", mtime=NOW - 3600)
    proc = _proc(tmp_path, {
        7001: (s.pid, "/usr/bin/zsh -c source /home/b/.claude/shell-snapshots/snap.sh && sleep 999"),
    })
    assert "tool shell" in sleep_mod.busy_reason(s, now=NOW, proc=proc)


def test_mcp_servers_are_not_work(fake_claude: Path, tmp_path: Path) -> None:
    s = _session(fake_claude)
    _transcript(fake_claude, s.session_id, "/tmp", mtime=NOW - 3600)
    proc = _proc(tmp_path, {
        7002: (s.pid, "/home/b/.venv/bin/python /home/b/.venv/bin/use-computer-mcp"),
        7003: (1, "/usr/bin/zsh -c source /home/b/.claude/shell-snapshots/snap.sh"),  # not ours
    })
    assert sleep_mod.busy_reason(s, now=NOW, proc=proc) is None


def test_a_subagent_still_writing_keeps_it_awake(fake_claude: Path, tmp_path: Path) -> None:
    s = _session(fake_claude)
    t = _transcript(fake_claude, s.session_id, "/tmp", mtime=NOW - 3600)
    sub = t.with_suffix("") / "subagents" / "agent-1.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text("{}\n")
    import os

    os.utime(sub, (NOW - 30, NOW - 30))
    assert "transcript written" in sleep_mod.busy_reason(s, now=NOW, proc=_proc(tmp_path, {}))


def test_no_evidence_of_idleness_means_awake(fake_claude: Path, tmp_path: Path) -> None:
    s = _session(fake_claude, since=None)
    assert "no record" in sleep_mod.busy_reason(s, now=NOW, proc=_proc(tmp_path, {}))


# ---- who is eligible -------------------------------------------------------


def test_only_hotline_panes_and_never_the_operator(fake_claude: Path) -> None:
    make_session(fake_claude, 1, "op", "/tmp", session_id="sid-op", tmux="hl-op:@0.%0")
    make_session(fake_claude, 2, "w", "/tmp", session_id="sid-w", tmux="hl-w:@0.%0")
    make_session(fake_claude, 3, "mine", "/tmp", session_id="sid-mine", tmux="his-term:@0.%0")
    make_session(fake_claude, 4, "bound", "/tmp", session_id="sid-b", tmux="hl-bound:@0.%0")
    reg = Registry()
    reg.declare("sid-op", "hotline-80", "operator")
    reg.agents["sid-op"].authority = "sys-admin"
    reg.declare("sid-w", "data-c1", "general agent")
    reg.save()
    got = sleep_mod.candidates(discover(), Registry(), bound={"bound"}, prefix="hl-")
    assert sorted(s.name for s, _ in got) == ["w"]
    assert got[0][1] is not None and got[0][1].name == "data-c1"


# ---- waking ----------------------------------------------------------------


def test_cwd_comes_from_the_transcript(fake_claude: Path) -> None:
    _transcript(fake_claude, "sid-x", "/home/bodas/data/elsewhere")
    assert sleep_mod.cwd_of("sid-x") == "/home/bodas/data/elsewhere"
    assert sleep_mod.cwd_of("sid-missing") is None


def test_sleeping_means_unfinished_and_not_live(fake_claude: Path) -> None:
    reg = Registry()
    a = reg.declare("sid-a", "data-a", "x")
    # No transcript is still asleep: wake revives it from its handoff instead.
    assert sleep_mod.sleeping(a, live_ids=set())
    assert not sleep_mod.sleeping(a, live_ids={"sid-a"})  # it is awake
    reg.complete("sid-a")
    assert not sleep_mod.sleeping(Registry().by_name("data-a"), live_ids=set())


async def test_wake_resumes_in_the_agents_own_directory(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hotline-80's ask: a session from a different cwd must resume from that cwd."""
    other = tmp_path / "other-project"
    other.mkdir()
    _transcript(fake_claude, "sid-a", str(other))
    agent = Registry().declare("sid-a", "data-a", "x")
    calls: list[dict[str, object]] = []

    async def spawn(key, cwd=None, name=None, resume=None, timeout=90.0):
        calls.append({"key": key, "cwd": cwd, "resume": resume})
        make_session(fake_claude, 77, key, cwd, session_id=resume, tmux=f"hl-{key}:@0.%0")
        return next(s for s in discover() if s.pid == 77)

    monkeypatch.setattr("hotline.tmuxen.spawn", spawn)
    s = await sleep_mod.wake(agent)
    assert s.session_id == "sid-a"
    assert calls == [{"key": "data-a", "cwd": str(other), "resume": "sid-a"}]


async def test_a_stranger_is_closed_and_the_agent_revived_from_its_handoff(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never deliver into the stranger; fall back to a seeded revive (his 10-26 ask)."""
    from types import SimpleNamespace

    _transcript(fake_claude, "sid-a", str(tmp_path))
    agent = Registry().declare("sid-a", "data-a", "x")
    killed: list[str] = []
    injected: list[str] = []

    async def spawn(key, cwd=None, name=None, resume=None, timeout=90.0):
        make_session(fake_claude, 78, key, cwd, session_id="sid-NEW", tmux=f"hl-{key}:@0.%0")
        return next(s for s in discover() if s.pid == 78)

    revived = SimpleNamespace(session_id="sid-REVIVED", name="data-a")

    async def revive(name, registry, cwd=None, channels=None):
        assert name == "data-a" and cwd == str(tmp_path)
        return SimpleNamespace(session=revived, brief=SimpleNamespace(seed="SEED"))

    async def inject(session, text):
        injected.append(text)

    monkeypatch.setattr("hotline.tmuxen.spawn", spawn)
    monkeypatch.setattr("hotline.tmuxen.kill", lambda name: killed.append(name) or True)
    monkeypatch.setattr("hotline.revive.resume", revive)
    monkeypatch.setattr("hotline.ccsocks.inject", inject)
    s = await sleep_mod.wake(agent)
    assert s is revived
    assert killed == ["hl-data-a"]
    assert injected == ["SEED"]


async def test_no_transcript_goes_straight_to_revive(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a restart with the transcript gone, the handoff is what is left."""
    from types import SimpleNamespace

    agent = Registry().declare("sid-gone", "data-g", "x")
    spawned: list[str] = []

    async def spawn(*a, **k):
        spawned.append("x")
        raise AssertionError("must not try to resume without a transcript")

    async def revive(name, registry, cwd=None, channels=None):
        return SimpleNamespace(session=SimpleNamespace(session_id="sid-new"),
                               brief=SimpleNamespace(seed="SEED"))

    async def inject(session, text):
        pass

    monkeypatch.setattr("hotline.tmuxen.spawn", spawn)
    monkeypatch.setattr("hotline.revive.resume", revive)
    monkeypatch.setattr("hotline.ccsocks.inject", inject)
    s = await sleep_mod.wake(agent)
    assert s.session_id == "sid-new" and spawned == []


async def test_two_messages_share_one_wake(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _transcript(fake_claude, "sid-a", str(tmp_path))
    agent = Registry().declare("sid-a", "data-a", "x")
    spawns = 0

    async def spawn(key, cwd=None, name=None, resume=None, timeout=90.0):
        nonlocal spawns
        spawns += 1
        await asyncio.sleep(0.05)
        make_session(fake_claude, 79, key, cwd, session_id=resume, tmux=f"hl-{key}:@0.%0")
        return next(s for s in discover() if s.pid == 79)

    monkeypatch.setattr("hotline.tmuxen.spawn", spawn)
    a, b = await asyncio.gather(sleep_mod.wake(agent), sleep_mod.wake(agent))
    assert spawns == 1 and a.session_id == b.session_id == "sid-a"


async def test_router_wakes_a_sleeping_agent_by_name(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_session(fake_claude, 5, "someone-else", "/tmp", session_id="sid-z")  # something is live
    _transcript(fake_claude, "sid-a", str(tmp_path))
    Registry().declare("sid-a", "data-a", "x")
    woke: list[str] = []

    async def wake(agent, timeout=120.0):
        woke.append(agent.name)
        make_session(fake_claude, 80, agent.name, str(tmp_path), session_id=agent.session_id)
        return next(s for s in discover() if s.pid == 80)

    monkeypatch.setattr(sleep_mod, "wake", wake)
    s = await Router().resolve_waking("data-a")
    assert woke == ["data-a"] and s.session_id == "sid-a"
    # And by the session id the Discord binding uses.
    (fake_claude / "sessions" / "80.json").unlink()
    s = await Router().resolve_waking("sid-a")
    assert woke == ["data-a", "data-a"]


async def test_router_does_not_wake_the_finished_or_the_unknown(fake_claude: Path) -> None:
    make_session(fake_claude, 5, "someone-else", "/tmp", session_id="sid-z")
    _transcript(fake_claude, "sid-done", "/tmp")
    reg = Registry()
    reg.declare("sid-done", "data-done", "x")
    reg.complete("sid-done")
    with pytest.raises(SessionNotFound, match="finished"):
        await Router().resolve_waking("data-done")
    with pytest.raises(SessionNotFound):
        await Router().resolve_waking("nobody-by-this-name")


def test_idle_after_is_thirty_minutes() -> None:
    # Pinned so a change to it is a decision, not an accident.
    assert sleep_mod.IDLE_AFTER == 1800.0
    assert time.time() < NOW  # NOW is safely in the future for mtime arithmetic


async def test_an_unregistered_session_put_to_sleep_still_wakes_by_name(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """meme-trend had wake tasks addressed to it by name and no registry record."""
    make_session(fake_claude, 6, "meme-trend", str(tmp_path), session_id="sid-meme",
                 tmux="hl-meme-trend:@0.%0")
    s = next(x for x in discover() if x.pid == 6)
    sleep_mod.remember(s)
    (fake_claude / "sessions" / "6.json").unlink()
    make_session(fake_claude, 5, "someone-else", "/tmp", session_id="sid-z")
    woke: list[str] = []

    async def wake(agent, timeout=120.0):
        woke.append(agent.session_id)
        make_session(fake_claude, 81, agent.name, str(tmp_path), session_id=agent.session_id)
        return next(x for x in discover() if x.pid == 81)

    monkeypatch.setattr(sleep_mod, "wake", wake)
    got = await Router().resolve_waking("meme-trend")
    assert woke == ["sid-meme"] and got.session_id == "sid-meme"


def test_a_never_sleep_agent_is_not_a_candidate(fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOTLINE_STATE", str(tmp_path / "state"))
    make_session(fake_claude, 1, "keep", "/tmp", session_id="sid-k", tmux="hl-keep:@0.%0")
    make_session(fake_claude, 2, "other", "/tmp", session_id="sid-o", tmux="hl-other:@0.%0")
    reg = Registry()
    reg.declare("sid-k", "manager-5", "manager")
    reg.declare("sid-o", "data-c1", "general agent")
    reg.save()
    before = sleep_mod.candidates(discover(), Registry(), bound=set(), prefix="hl-")
    assert sorted(a.name for _, a in before if a) == ["data-c1", "manager-5"]
    sleep_mod.set_never_sleep("manager-5")
    after = sleep_mod.candidates(discover(), Registry(), bound=set(), prefix="hl-")
    assert [a.name for _, a in after if a] == ["data-c1"]
    sleep_mod.set_never_sleep("manager-5", on=False)
    assert "manager-5" not in sleep_mod.never_sleep()
