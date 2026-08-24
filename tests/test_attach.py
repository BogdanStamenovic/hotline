"""Attach mode's reply path, with the Stop hook deliberately absent.

The hook is a latency optimisation, not a correctness requirement. It can be
uninstalled, it can fail to be registered on a session that started before it
existed, and during this build it once silently failed to satisfy a waiter that
should have been satisfied. So the transcript itself is treated as ground truth
and quiescence is the fallback -- these tests pin that down by never recording a
stop event at all.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from helpers import assistant_entry, make_session, user_entry, write_transcript

import hotline.router as router_module
from hotline.errors import ReplyTimeout
from hotline.router import Router
from hotline.stops import record_stop

SID = "sess-attach"
PID = 500


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the quiescence window so the suite does not sit through real waits."""
    monkeypatch.setattr(router_module, "QUIET_SECONDS", 0.2)
    monkeypatch.setattr(router_module, "POLL_INTERVAL", 0.02)


def fake_reply(home: Path, marker: str, answer: str, delay: float, tools: list[str] | None = None):
    """Stand in for the real session: after `delay`, append our message and a reply.

    Tool calls are written as their own assistant records, ahead of the answer,
    because that is what the CLI actually does -- measured at 0 of 665 assistant
    records carrying both text and a tool call. The waiter now depends on that
    ordering to tell a step from an answer, so a fixture that bundled them was
    testing a shape that never occurs.
    """

    async def _inject(session, text, timeout=5.0):
        async def later() -> None:
            await asyncio.sleep(delay)
            records = [user_entry(marker)]
            records += [assistant_entry("", tools=[t]) for t in tools or []]
            records.append(assistant_entry(answer))
            write_transcript(home, SID, records)

        asyncio.get_running_loop().create_task(later())

    return _inject


async def test_reply_arrives_without_any_stop_event(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID)
    write_transcript(fake_claude, SID, [user_entry("earlier"), assistant_entry("earlier answer")])
    monkeypatch.setattr(router_module, "inject", fake_reply(fake_claude, "ping", "pong", 0.05))

    reply = await Router().ask_session("target-aa", "ping", timeout=5.0)
    assert reply.text == "pong"
    assert reply.subtype == "attached"


async def test_a_stop_event_is_the_fast_path(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID)
    monkeypatch.setattr(router_module, "inject", fake_reply(fake_claude, "ping", "pong", 0.05))

    async def stop_soon() -> None:
        await asyncio.sleep(0.08)
        record_stop(SID)

    task = asyncio.create_task(stop_soon())
    reply = await Router().ask_session("target-aa", "ping", timeout=5.0)
    assert reply.text == "pong"
    await task


async def test_does_not_return_a_reply_to_an_earlier_turn(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The target was already mid-turn. The first thing that lands is not ours."""
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID)

    async def _inject(session, text, timeout=5.0):
        async def later() -> None:
            await asyncio.sleep(0.05)
            write_transcript(fake_claude, SID, [assistant_entry("answer to the previous turn")])
            record_stop(SID)
            await asyncio.sleep(0.2)
            write_transcript(fake_claude, SID, [user_entry("ping"), assistant_entry("pong")])
            record_stop(SID)

        asyncio.get_running_loop().create_task(later())

    monkeypatch.setattr(router_module, "inject", _inject)
    reply = await Router().ask_session("target-aa", "ping", timeout=5.0)
    assert reply.text == "pong"


async def test_tool_calls_are_reported(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID)
    monkeypatch.setattr(
        router_module, "inject", fake_reply(fake_claude, "ping", "pong", 0.05, tools=["Bash"])
    )
    seen: list[str] = []
    reply = await Router().ask_session(
        "target-aa", "ping", narrator=lambda e: seen.append(e.detail), timeout=5.0
    )
    assert [e.tool for e in reply.events] == ["Bash"]
    assert seen == ["Bash"]


async def test_timeout_says_the_message_never_landed(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two failure modes need different advice, so they get different messages."""
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID)

    async def _inject(session, text, timeout=5.0):
        return None

    monkeypatch.setattr(router_module, "inject", _inject)
    with pytest.raises(ReplyTimeout) as exc:
        await Router().ask_session("target-aa", "ping", timeout=0.6)
    assert "crossSessionInbound" in str(exc.value)


async def test_timeout_says_it_landed_but_went_unanswered(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID)

    async def _inject(session, text, timeout=5.0):
        write_transcript(fake_claude, SID, [user_entry("ping")])

    monkeypatch.setattr(router_module, "inject", _inject)
    with pytest.raises(ReplyTimeout) as exc:
        await Router().ask_session("target-aa", "ping", timeout=0.6)
    assert "did receive the message" in str(exc.value)


async def test_a_busy_target_is_not_mistaken_for_a_finished_one(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quiescence needs both halves: a quiet transcript AND an idle descriptor.
    A session thinking for a long time before its first token is quiet but busy."""
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID, status="busy")

    async def _inject(session, text, timeout=5.0):
        write_transcript(fake_claude, SID, [user_entry("ping")])

    monkeypatch.setattr(router_module, "inject", _inject)
    with pytest.raises(ReplyTimeout):
        await Router().ask_session("target-aa", "ping", timeout=0.6)


async def test_a_stop_that_beats_the_text_does_not_lose_the_answer(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race that handed one turn's caller another turn's answer.

    The Stop hook fires a beat before the final assistant text reaches the
    transcript. `stopped` is true, there is no text yet, and the loop advances its
    stamp past that stop -- so it can never fire again for this turn. The quiet
    fallback is supposed to catch that, and did not, because a freshly spawned
    session reports status "waiting" and the check only accepted "idle".

    Live, the turn spun for 226 seconds and then returned the answer to a
    completely different question that had been asked in the meantime.
    """
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID, status="waiting")

    async def _inject(session, text, timeout=5.0):
        async def later() -> None:
            # Our message lands, the stop fires, and only then does the answer
            # appear -- the exact order that broke it.
            write_transcript(fake_claude, SID, [user_entry("ping")])
            record_stop(SID)
            await asyncio.sleep(0.15)
            write_transcript(fake_claude, SID, [assistant_entry("pong")])

        asyncio.get_running_loop().create_task(later())

    monkeypatch.setattr(router_module, "inject", _inject)
    reply = await Router().ask_session("target-aa", "ping", timeout=5.0)
    assert reply.text == "pong"


async def test_a_busy_session_reporting_waiting_is_still_not_finished(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widening the quiet check must not make "busy" quiescent."""
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID, status="busy")

    async def _inject(session, text, timeout=5.0):
        write_transcript(fake_claude, SID, [user_entry("ping")])

    monkeypatch.setattr(router_module, "inject", _inject)
    with pytest.raises(ReplyTimeout):
        await Router().ask_session("target-aa", "ping", timeout=0.6)


async def test_an_opening_sentence_is_not_the_answer(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression that a relaying session caught by being relayed to.

    Leaving the stop armed fixed one turn being handed another turn's answer, and
    introduced a quieter failure in its place. A stop landing at or just after
    injection latches `stopped` forever, so the loop returned the first text the
    target emitted -- the opening sentence of a turn that had barely started.

    Measured live: a stop at 23:07:08, a text-only assistant record at 23:07:19,
    a tool_use at 23:07:22. The sender received the 23:07:19 sentence as the
    finished answer and had no way to know more was coming.
    """
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID, status="waiting")

    async def _inject(session, text, timeout=5.0):
        async def later() -> None:
            write_transcript(fake_claude, SID, [user_entry("ping")])
            # A stop lands immediately -- it belongs to the turn that just ended.
            record_stop(SID)
            await asyncio.sleep(0.1)
            write_transcript(fake_claude, SID, [assistant_entry("Let me look into that.")])
            await asyncio.sleep(0.3)
            write_transcript(fake_claude, SID, [assistant_entry("", tools=["Bash"])])
            await asyncio.sleep(0.3)
            write_transcript(fake_claude, SID, [assistant_entry("pong, the real answer")])

        asyncio.get_running_loop().create_task(later())

    monkeypatch.setattr(router_module, "inject", _inject)
    reply = await Router().ask_session("target-aa", "ping", timeout=5.0)
    assert reply.text == "pong, the real answer"


async def test_a_stale_tool_call_before_the_injection_does_not_block_forever(
    fake_claude: Path, quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`in_flight` is computed over the turn slice, not the whole tail.

    A tool call from a previous turn sits in the transcript forever. Judged over
    the whole file it would make every later turn look unfinished, and the waiter
    would never return anything again.
    """
    make_session(fake_claude, PID, "target-aa", "/home/bodas/data", SID)
    write_transcript(fake_claude, SID, [
        user_entry("an earlier question"),
        assistant_entry("", tools=["Bash"]),  # last record before our offset
    ])
    monkeypatch.setattr(router_module, "inject", fake_reply(fake_claude, "ping", "pong", 0.05))
    reply = await Router().ask_session("target-aa", "ping", timeout=5.0)
    assert reply.text == "pong"
