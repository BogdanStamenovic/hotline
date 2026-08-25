"""The stand-in, the relay, and `session kill`.

`tofix.md` #2, #3 and #5. Two of these are the same mechanism seen from opposite
ends: a message to a busy session must produce an answer immediately (the
stand-in, and the receipt that it was queued), and the real answer must arrive
later without anyone asking again (the relay).

The failure being tested against is *silence*. A message that lands in a busy
session's inbox and sits there is correct behaviour and is indistinguishable, from
the sender's chair, from the message having been dropped on the floor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from helpers import FakeWorld, make_session

import hotline.pool as pool_module
import hotline.standin as standin_module
from hotline.errors import SessionNotFound
from hotline.pool import SessionPool
from hotline.standin import Standing


@pytest.fixture
def world(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    return FakeWorld(fake_claude, monkeypatch, pool_module)


@pytest.fixture
def busy(world: FakeWorld, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """A session that is mid-turn, and a stand-in that does not cost a real agent."""
    make_session(
        world.home, 700, "data-13", "/home/bodas/data", "fff", started_at=7000, status="busy"
    )
    world.busy.add("data-13")
    asked: list[tuple[str, str]] = []

    async def fake_report(session, question, delivered, timeout=75.0):
        asked.append((session.name, question))
        return Standing(text="It is running the test suite.", delivered=delivered)

    monkeypatch.setattr(standin_module, "report", fake_report)
    monkeypatch.setattr(pool_module.standin, "report", fake_report)
    return asked


async def test_a_busy_session_answers_immediately_through_a_stand_in(
    world: FakeWorld, busy: list[tuple[str, str]]
) -> None:
    pool = SessionPool()
    _, reply = await pool.ask("k", "connect data-13")
    _, reply = await pool.ask("k", "how is it going?")

    assert reply.subtype == "standin"
    assert busy == [("data-13", "how is it going?")]
    # The receipt: the sender is told the message exists somewhere.
    assert "queued for data-13" in reply.text
    assert "running the test suite" in reply.text
    await pool.close()


async def test_the_message_is_still_delivered_to_the_busy_session(
    world: FakeWorld, busy: list[tuple[str, str]]
) -> None:
    """The stand-in reports; it does not intercept. The real session must still
    receive the message, or the receipt is a lie."""
    pool = SessionPool()
    await pool.ask("k", "connect data-13")
    await pool.ask("k", "how is it going?")
    assert world.delivered == [("data-13", "how is it going?")]
    await pool.close()


async def test_the_real_answer_is_relayed_when_it_lands(
    world: FakeWorld, busy: list[tuple[str, str]]
) -> None:
    """The other half of the promise. Without this the stand-in offers a relay
    that nothing in the system is able to perform."""
    relayed: list[tuple[str, str]] = []

    async def deliver(key: str, text: str) -> None:
        relayed.append((key, text))

    world.replies["data-13"] = "the suite is green"
    pool = SessionPool(deliver=deliver)
    await pool.ask("k", "connect data-13")
    await pool.ask("k", "how is it going?")

    conv = pool.conversations["k"]
    await asyncio.gather(*conv.relays)
    assert relayed == [("k", "data-13 has finished the message you sent it:\n\nthe suite is green")]
    await pool.close()


async def test_a_relay_that_never_answers_says_so(
    world: FakeWorld, busy: list[tuple[str, str]]
) -> None:
    relayed: list[str] = []

    async def deliver(key: str, text: str) -> None:
        relayed.append(text)

    world.explode.add("data-13")
    pool = SessionPool(deliver=deliver)
    await pool.ask("k", "connect data-13")
    await pool.ask("k", "how is it going?")
    await asyncio.gather(*pool.conversations["k"].relays)
    assert "never answered" in relayed[0]
    await pool.close()


async def test_an_idle_session_is_answered_directly_with_no_stand_in(
    world: FakeWorld, busy: list[tuple[str, str]]
) -> None:
    """The stand-in is for busy sessions only. Putting one in front of every
    message would double the latency of the common case to solve the rare one."""
    make_session(
        world.home, 800, "data-99", "/home/bodas/data", "ggg", started_at=8000, status="idle"
    )
    pool = SessionPool()
    await pool.ask("k", "connect data-99")
    _, reply = await pool.ask("k", "how is it going?")
    assert reply.subtype == "attached"
    assert busy == []
    await pool.close()


# ---- session kill ------------------------------------------------------


async def test_session_kill_by_name(world: FakeWorld) -> None:
    make_session(world.home, 900, "data-13", "/home/bodas/data", "hhh", started_at=9000)
    pool = SessionPool()
    _, reply = await pool.ask("k", "session kill data-13")
    assert reply.subtype == "control"
    assert world.killed == ["data-13"]
    await pool.close()


async def test_session_kill_by_the_number_you_were_shown(world: FakeWorld) -> None:
    make_session(world.home, 910, "old-one", "/home/bodas/data", "iii", started_at=100)
    make_session(world.home, 911, "new-one", "/home/bodas/data", "jjj", started_at=200)
    pool = SessionPool()
    _, listing = await pool.ask("k", "session list")
    assert "1. new-one" in listing.text
    await pool.ask("k", "session kill 2")
    assert world.killed == ["old-one"]
    await pool.close()


async def test_killing_the_session_you_are_connected_to_detaches_you(
    world: FakeWorld,
) -> None:
    make_session(world.home, 920, "data-13", "/home/bodas/data", "kkk", started_at=9200)
    pool = SessionPool()
    await pool.ask("k", "connect data-13")
    await pool.ask("k", "kill data-13")
    assert pool.conversations["k"].attached_to is None
    await pool.close()


async def test_killing_your_own_session_lets_the_next_message_open_a_new_one(
    world: FakeWorld,
) -> None:
    pool = SessionPool()
    await pool.ask("k", "hello")
    await pool.ask("k", "kill hl-k")
    assert pool.conversations["k"].own is None
    await pool.ask("k", "hello again")
    assert world.spawned == ["hl-k", "hl-k"]
    await pool.close()


@pytest.mark.parametrize(
    "utterance",
    [
        "kill the process listening on port 8080",
        "kill all the zombie processes",
        "stop the build",
        "end the call",
    ],
)
async def test_kill_shaped_sentences_that_are_not_commands_reach_a_session(
    world: FakeWorld, utterance: str
) -> None:
    """`kill` ends something someone may be sitting in front of. A fuzzy match
    here is a bug, not a kindness -- so anything whose target is not a real
    session falls through and is answered as an ordinary question."""
    pool = SessionPool()
    route, _reply = await pool.ask("k", utterance)
    assert route.mode == "own"
    assert world.killed == []
    await pool.close()


async def test_hotline_refuses_to_kill_itself(fake_claude: Path) -> None:
    """`session kill` resolves fuzzily, and "kill the hotline one" is an entirely
    natural thing to say to the process named hotline."""
    import os

    from hotline.ccsocks import discover, terminate
    from hotline.errors import HotlineError

    make_session(fake_claude, os.getpid(), "hotline-ac", "/home/bodas/data/hotline", "mmm")
    session = next(s for s in discover(include_self=True) if s.pid == os.getpid())
    with pytest.raises(HotlineError, match="refusing to kill"):
        await terminate(session)


# ---- bounding the sessions we leave running -----------------------------


async def test_orphaned_sessions_are_closed_eventually(
    world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reaping leaves sessions alive so they can be attached to later. Without a
    bound that is a slow leak -- each `claude` is a few hundred MB."""
    import time as time_module

    from helpers import assistant_entry, write_transcript

    pool = SessionPool(idle_timeout=0.05)
    await pool.ask("k", "hello")
    session = pool.conversations["k"].own
    write_transcript(world.home, "sid-9001", [assistant_entry("done")])

    await asyncio.sleep(0.1)
    await pool.reap()
    assert world.killed == []  # forgotten, but still running and attachable

    # Four hours later, with nobody bound to it.
    monkeypatch.setattr(time_module, "time", lambda: 1e12)
    await pool.reap()
    assert world.killed == [session]
    await pool.close()


async def test_a_session_someone_is_still_bound_to_is_never_orphaned(
    world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as time_module

    from helpers import assistant_entry, write_transcript

    pool = SessionPool()
    await pool.ask("k", "hello")
    write_transcript(world.home, "sid-9001", [assistant_entry("done")])
    monkeypatch.setattr(time_module, "time", lambda: 1e12)
    await pool.reap()
    assert world.killed == []
    await pool.close()


# ---- a binding has to outlive the thing it points at moving --------------


async def test_a_binding_survives_the_session_getting_a_new_pid(
    world: FakeWorld,
) -> None:
    """The bug Bogdan hit twice.

    The builder session's process was replaced mid-conversation. Resolution was
    by name, the old descriptor went away, and `ask` correctly refused to hand him
    a stranger -- which is the right safety and still meant his claim silently
    stopped working. Session ids survive a new pid; names and pids do not.
    """
    make_session(world.home, 300, "builder", "/home/bodas/data", "stable-id", started_at=3000)
    pool = SessionPool()
    await pool.ask("k", "connect builder")
    assert pool.conversations["k"].attached_id == "stable-id"

    # Same session, new process: different pid, and it even renames itself.
    (world.home / "sessions" / "300.json").unlink()
    make_session(
        world.home, 999, "builder-renamed", "/home/bodas/data", "stable-id", started_at=3000
    )

    route, reply = await pool.ask("k", "still there?")
    assert route.mode == "attach"
    assert world.delivered[-1] == ("builder-renamed", "still there?")
    assert reply.text == "answer-1"
    await pool.close()


async def test_the_route_reports_the_name_not_the_id(world: FakeWorld) -> None:
    """Telling a caller it is attached to "stable-id" teaches them nothing."""
    make_session(world.home, 301, "builder", "/home/bodas/data", "stable-id", started_at=3000)
    pool = SessionPool()
    await pool.ask("k", "connect builder")
    route, _ = await pool.ask("k", "hello")
    assert route.target == "builder"
    await pool.close()


async def test_a_binding_to_a_session_that_really_died_is_still_reported(
    world: FakeWorld,
) -> None:
    """Binding by id must not weaken the tofix #8 guarantee."""
    make_session(world.home, 302, "builder", "/home/bodas/data", "gone-id", started_at=3000)
    pool = SessionPool()
    await pool.ask("k", "connect builder")
    (world.home / "sessions" / "302.json").unlink()

    with pytest.raises(SessionNotFound, match="is gone"):
        await pool.ask("k", "still there?")
    assert pool.conversations["k"].attached_to is None
    assert pool.conversations["k"].attached_id is None
    await pool.close()
