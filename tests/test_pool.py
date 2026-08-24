"""Conversation persistence, eviction, the busy stand-in, and the soft-timeout rejoin.

The rejoin is the one piece of behaviour here that is easy to get wrong in a way
nobody notices until a real call: a phone that gives up after a hundred seconds
must not cancel a turn that is doing actual work.

These fake `tmuxen.spawn` and the router's `deliver`/`collect` rather than a
subprocess, so everything between -- resolution, stickiness, spawning, eviction,
the busy path -- is the real code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from helpers import FakeWorld, make_session

import hotline.pool as pool_module
from hotline.errors import ClaudeLaunchFailed, SessionNotFound
from hotline.pool import SessionPool


@pytest.fixture
def world(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    return FakeWorld(fake_claude, monkeypatch, pool_module)


@pytest.fixture(autouse=True)
def no_standin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off by default. The tests that want it turn it on and fake the agent."""
    monkeypatch.setattr(SessionPool, "use_standin", False, raising=False)


async def test_one_session_serves_a_whole_conversation(world: FakeWorld) -> None:
    pool = SessionPool()
    _, first = await pool.ask("phone-1", "hello")
    _, second = await pool.ask("phone-1", "again")
    assert (first.text, second.text) == ("answer-1", "answer-2")
    assert world.spawned == ["hl-phone-1"]
    await pool.close()


async def test_the_session_is_in_tmux_and_says_where(world: FakeWorld) -> None:
    """tofix #1: a session you cannot attach to is write-only."""
    pool = SessionPool()
    await pool.ask("phone-1", "hello")
    _, reply = await pool.ask("phone-1", "where am i")
    assert "tmux attach -t hl-phone-1" in reply.text
    await pool.close()


async def test_different_callers_get_different_sessions(world: FakeWorld) -> None:
    pool = SessionPool()
    _, a = await pool.ask("phone-1", "hello")
    _, b = await pool.ask("phone-2", "hello")
    assert a.session_id != b.session_id
    assert world.spawned == ["hl-phone-1", "hl-phone-2"]
    await pool.close()


async def test_reaping_forgets_the_conversation_but_keeps_the_session(
    world: FakeWorld,
) -> None:
    """The fix for tofix #8, and for "I cannot save a session for later".

    Reaping used to kill the subprocess, which is how a conversation could vanish
    from under someone. Now it drops the binding only, and because the tmux name is
    derived from the caller key the next message walks back into the same session.
    """
    pool = SessionPool(idle_timeout=0.05)
    await pool.ask("phone-1", "hello")
    await asyncio.sleep(0.1)
    assert await pool.reap() == 1
    assert pool.conversations == {}
    assert world.killed == []

    _, reply = await pool.ask("phone-1", "still there?")
    assert world.spawned == ["hl-phone-1"]  # reconnected, not respawned
    assert reply.text == "answer-2"  # and it remembers the first turn
    await pool.close()


async def test_the_least_recently_used_is_evicted_at_capacity(world: FakeWorld) -> None:
    pool = SessionPool(max_sessions=2)
    await pool.ask("a", "hi")
    await asyncio.sleep(0.01)
    await pool.ask("b", "hi")
    await asyncio.sleep(0.01)
    await pool.ask("a", "hi again")  # refreshes a, making b the oldest
    await pool.ask("c", "hi")
    assert set(pool.conversations) == {"a", "c"}
    assert world.killed == ["hl-b"]  # eviction really does end it
    await pool.close()


async def test_a_dead_session_does_not_poison_the_key(world: FakeWorld) -> None:
    """A session that died must cost one turn, not the conversation."""
    pool = SessionPool()
    await pool.ask("phone-1", "hello")
    (world.home / "sessions" / "9001.json").unlink()
    world.spawned.clear()

    _, reply = await pool.ask("phone-1", "still there?")
    assert world.spawned == ["hl-phone-1"]
    assert reply.notice is not None and "had gone away" in reply.notice
    await pool.close()


async def test_a_vanished_attachment_is_reported_not_substituted(world: FakeWorld) -> None:
    """tofix #8, the half that matters most: never hand someone a stranger."""
    make_session(world.home, 500, "data-13", "/home/bodas/data", "ddd", started_at=5000)
    pool = SessionPool()
    _, reply = await pool.ask("phone-1", "connect data-13")
    assert "Connected" in reply.text

    (world.home / "sessions" / "500.json").unlink()
    with pytest.raises(SessionNotFound) as caught:
        await pool.ask("phone-1", "are you still there?")
    assert "is gone" in str(caught.value)
    assert pool.conversations["phone-1"].attached_to is None
    await pool.close()


async def test_soft_timeout_reports_pending_without_killing_the_turn(
    world: FakeWorld,
) -> None:
    pool = SessionPool()
    await pool.ask("phone-1", "warm up")
    world.delay = 0.5

    assert await pool.ask_soft("phone-1", "slow one", soft_timeout=0.05) is None

    conv = pool.conversations["phone-1"]
    assert conv.pending is not None and not conv.pending.done()

    # The work survives the caller giving up, and is there to be collected.
    outcome = await pool.ask_soft("phone-1", "are you done?", soft_timeout=5.0)
    assert outcome is not None
    _, reply = outcome
    assert reply.text == "answer-2"
    assert [text for _, text in world.delivered] == ["warm up", "slow one"]
    await pool.close()


async def test_a_fast_turn_never_reports_pending(world: FakeWorld) -> None:
    outcome = await SessionPool().ask_soft("phone-1", "quick", soft_timeout=5.0)
    assert outcome is not None


async def test_attach_mode_does_not_spawn_a_session(world: FakeWorld) -> None:
    """The session on the other end is the state; spawning one would be pointless."""
    make_session(world.home, 400, "data-13", "/home/bodas/data", "ccc", started_at=4000)
    pool = SessionPool()
    route, _reply = await pool.ask("phone-1", "join data-13, what's up")
    assert route.mode == "attach"
    assert world.delivered == [("data-13", "what's up")]
    assert world.spawned == []
    assert pool.conversations["phone-1"].own is None
    await pool.close()


async def test_close_leaves_sessions_running(world: FakeWorld) -> None:
    """A daemon restart must not cost anyone their context."""
    pool = SessionPool()
    await pool.ask("phone-1", "warm up")
    await pool.close()
    assert world.killed == []


async def test_close_cancels_work_in_flight(world: FakeWorld) -> None:
    pool = SessionPool()
    await pool.ask("phone-1", "warm up")
    world.delay = 5.0
    await pool.ask_soft("phone-1", "slow", soft_timeout=0.05)
    pending = pool.conversations["phone-1"].pending
    await pool.close()
    assert pending is not None and (pending.cancelled() or pending.done())


async def test_an_evicted_conversation_says_so_next_time(world: FakeWorld) -> None:
    pool = SessionPool(max_sessions=2)
    await pool.ask("a", "hi")
    await asyncio.sleep(0.01)
    await pool.ask("b", "hi")
    await asyncio.sleep(0.01)
    await pool.ask("a", "again")
    await pool.ask("c", "hi")
    _, reply = await pool.ask("b", "am i still here?")
    assert reply.notice is not None
    assert "least recently used" in reply.notice
    await pool.close()


async def test_bindings_survive_the_process_without_a_false_notice(
    world: FakeWorld,
) -> None:
    """A restart keeps the binding. It no longer costs the context, so it no longer
    claims to -- the session is in tmux and outlived the daemon."""
    make_session(world.home, 600, "data-13", "/home/bodas/data", "eee", started_at=6000)
    pool = SessionPool()
    await pool.ask("phone-1", "connect data-13")
    await pool.ask("phone-1", "hello")
    await pool.close()

    revived = SessionPool()
    assert revived.conversations["phone-1"].attached_to == "data-13"
    assert "phone-1" not in revived.retired
    await revived.close()


async def test_a_restart_that_did_lose_the_session_says_so(world: FakeWorld) -> None:
    pool = SessionPool()
    await pool.ask("phone-1", "hello")
    await pool.close()
    world.spawned.clear()  # `tmuxen.exists` now says the pane is gone

    revived = SessionPool()
    assert "did not survive" in revived.retired["phone-1"]
    await revived.close()


async def test_explode_still_raises(world: FakeWorld) -> None:
    pool = SessionPool()
    await pool.ask("phone-1", "hello")
    world.explode.add("hl-phone-1")
    with pytest.raises(ClaudeLaunchFailed):
        await pool.ask("phone-1", "boom")
    await pool.close()
