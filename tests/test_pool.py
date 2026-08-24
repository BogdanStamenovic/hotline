"""Conversation persistence, eviction, and the soft-timeout rejoin.

The rejoin is the one piece of behaviour here that is easy to get wrong in a way
nobody notices until a real call: a phone that gives up after a hundred seconds
must not cancel a turn that is doing actual work. These tests use a fake
FreshSession so the timing is deterministic rather than at the mercy of a real
model turn.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

import hotline.pool as pool_module
from hotline.errors import ClaudeLaunchFailed
from hotline.fresh import Reply
from hotline.pool import SessionPool


class FakeSession:
    """Stands in for a `claude` subprocess. Records how many were ever created."""

    created = 0
    live: ClassVar[list[FakeSession]] = []

    def __init__(self, cwd=None, bypass=True, append_system_prompt=None):
        FakeSession.created += 1
        self.ordinal = FakeSession.created
        self.session_id = f"claude-{self.ordinal}"
        self.proc = None
        self.closed = False
        self.history: list[str] = []
        self.delay = 0.0
        self.explode = False
        self.append_system_prompt = append_system_prompt
        FakeSession.live.append(self)

    async def start(self) -> None:
        return None

    async def ask(self, text, narrator=None, timeout=300.0):
        self.history.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.explode:
            raise ClaudeLaunchFailed("subprocess died")
        return Reply(text=f"answer-{len(self.history)}", session_id=self.session_id)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.created = 0
    FakeSession.live = []
    monkeypatch.setattr(pool_module, "FreshSession", FakeSession)


async def test_one_process_serves_a_whole_conversation() -> None:
    pool = SessionPool()
    _, first = await pool.ask("phone-1", "hello")
    _, second = await pool.ask("phone-1", "again")
    assert (first.text, second.text) == ("answer-1", "answer-2")
    assert FakeSession.created == 1
    await pool.close()


async def test_different_callers_get_different_processes() -> None:
    pool = SessionPool()
    _, a = await pool.ask("phone-1", "hello")
    _, b = await pool.ask("phone-2", "hello")
    assert a.session_id != b.session_id
    assert FakeSession.created == 2
    await pool.close()


async def test_idle_conversations_are_reaped() -> None:
    """A phone that walks out of range never says goodbye, so silence is the only
    signal a conversation has ended."""
    pool = SessionPool(idle_timeout=0.05)
    await pool.ask("phone-1", "hello")
    await asyncio.sleep(0.1)
    assert await pool.reap() == 1
    assert pool.conversations == {}
    assert FakeSession.live[0].closed
    await pool.close()


async def test_the_least_recently_used_is_evicted_at_capacity() -> None:
    pool = SessionPool(max_sessions=2)
    await pool.ask("a", "hi")
    await asyncio.sleep(0.01)
    await pool.ask("b", "hi")
    await asyncio.sleep(0.01)
    await pool.ask("a", "hi again")  # refreshes a, making b the oldest
    await pool.ask("c", "hi")
    assert set(pool.conversations) == {"a", "c"}
    await pool.close()


async def test_a_dead_subprocess_does_not_poison_the_key() -> None:
    pool = SessionPool()
    await pool.ask("phone-1", "hello")
    FakeSession.live[0].explode = True
    with pytest.raises(ClaudeLaunchFailed):
        await pool.ask("phone-1", "boom")
    _, reply = await pool.ask("phone-1", "still there?")
    assert reply.text == "answer-1"
    assert FakeSession.created == 2
    await pool.close()


async def test_soft_timeout_reports_pending_without_killing_the_turn() -> None:
    pool = SessionPool()
    await pool.ask("phone-1", "warm up")
    FakeSession.live[0].delay = 0.5

    assert await pool.ask_soft("phone-1", "slow one", soft_timeout=0.05) is None

    conv = pool.conversations["phone-1"]
    assert conv.pending is not None and not conv.pending.done()

    # The work survives the caller giving up, and is there to be collected.
    outcome = await pool.ask_soft("phone-1", "are you done?", soft_timeout=5.0)
    assert outcome is not None
    _, reply = outcome
    assert reply.text == "answer-2"
    assert FakeSession.live[0].history == ["warm up", "slow one"]
    await pool.close()


async def test_a_fast_turn_never_reports_pending() -> None:
    pool = SessionPool()
    outcome = await pool.ask_soft("phone-1", "quick", soft_timeout=5.0)
    assert outcome is not None
    assert pool.conversations["phone-1"].pending is None
    await pool.close()


async def test_attach_mode_does_not_allocate_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session on the other end is the state; a pooled subprocess would be
    a second, pointless one."""
    pool = SessionPool()

    async def fake_attach(spec, text, narrator=None, timeout=300.0):
        return Reply(text="from the other session", session_id="remote")

    monkeypatch.setattr(pool.router, "ask_session", fake_attach)
    route, reply = await pool.ask("phone-1", "join data-13, what's up")
    assert route.mode == "attach"
    assert reply.text == "from the other session"
    assert FakeSession.created == 0
    # A bookkeeping record is fine and is needed to remember a sticky connection;
    # what must not happen is a second subprocess for a session that already exists.
    assert pool.conversations["phone-1"].session is None
    await pool.close()


async def test_close_cancels_work_in_flight() -> None:
    pool = SessionPool()
    await pool.ask("phone-1", "warm up")
    FakeSession.live[0].delay = 5.0
    await pool.ask_soft("phone-1", "slow", soft_timeout=0.05)
    pending = pool.conversations["phone-1"].pending
    await pool.close()
    assert pending is not None and pending.cancelled() or pending.done()
