"""Connection commands and sticky routing.

Both came directly from Bogdan, over the Discord bridge, mid-build: "I want to be
able to type normally like this and that is then routed automatically to the
running session if a conversation already exists. Also if I say `session list` it
should say a session list so I can choose where to connect."

They live in the router and the pool rather than in the Discord bot, because the
phone needs them too -- "session list" spoken aloud is the same request.

The interesting cases are the ones where a control phrase is *not* a control
phrase. `list the files in ~/data` and `connect the dots in this diagram` must
reach a session, and getting that wrong means an ordinary question silently turns
into a connection error.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import make_session
from test_pool import FakeSession

import hotline.pool as pool_module
from hotline.fresh import Reply
from hotline.pool import SessionPool
from hotline.router import parse_utterance


@pytest.fixture(autouse=True)
def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.created = 0
    FakeSession.live = []
    monkeypatch.setattr(pool_module, "FreshSession", FakeSession)


@pytest.fixture
def three(fake_claude: Path) -> Path:
    make_session(fake_claude, 100, "data-d6", "/home/bodas/data", "aaa", started_at=1000)
    make_session(fake_claude, 200, "data-13", "/home/bodas/data", "bbb", started_at=2000)
    make_session(fake_claude, 300, "uxo-7f", "/home/bodas/data/uxonews", "ccc", started_at=3000)
    return fake_claude


@pytest.mark.parametrize(
    "utterance,action",
    [
        ("session list", "list"),
        ("Session list:", "list"),
        ("sessions", "list"),
        ("list sessions", "list"),
        ("what sessions are running", "list"),
        ("detach", "detach"),
        ("disconnect", "detach"),
        ("where am i", "where"),
        ("connect data-13", "connect"),
        ("connect to the one in uxonews", "connect"),
        ("switch to 2", "connect"),
    ],
)
def test_control_phrases_are_recognised(utterance: str, action: str) -> None:
    route = parse_utterance(utterance)
    assert route.mode == "control"
    assert route.action == action


@pytest.mark.parametrize(
    "utterance",
    [
        "list the files in ~/data",
        "what is in ~/data",
        "sessions are hard to name",
        "new session, list the files",
        "join data-13, what's failing?",
        "where am i going wrong in this function",
    ],
)
def test_ordinary_questions_are_not_control(utterance: str) -> None:
    assert parse_utterance(utterance).mode != "control"


async def test_session_list_numbers_them(three: Path) -> None:
    pool = SessionPool()
    _, reply = await pool.ask("k", "session list")
    assert reply.subtype == "control"
    assert "1. uxo-7f" in reply.text
    assert "3. data-d6" in reply.text
    assert "connect 2" in reply.text
    assert FakeSession.created == 0
    await pool.close()


async def test_list_with_nothing_live(fake_claude: Path) -> None:
    pool = SessionPool()
    _, reply = await pool.ask("k", "sessions")
    assert "No live Claude sessions" in reply.text
    await pool.close()


async def test_connect_by_number_uses_the_listed_order(three: Path) -> None:
    """`connect 2` means the second line of the list, not pid 2."""
    pool = SessionPool()
    _, reply = await pool.ask("k", "connect 2")
    assert "data-13" in reply.text
    assert pool.conversations["k"].attached_to == "data-13"
    await pool.close()


async def test_connect_by_name_and_by_directory(three: Path) -> None:
    pool = SessionPool()
    await pool.ask("k", "connect data-13")
    assert pool.conversations["k"].attached_to == "data-13"
    await pool.ask("k", "connect to the one in uxonews")
    assert pool.conversations["k"].attached_to == "uxo-7f"
    await pool.close()


async def test_connecting_makes_plain_messages_sticky(
    three: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual request: connect once, then just talk."""
    pool = SessionPool()
    seen: list[tuple[str, str]] = []

    async def fake_attach(spec, text, narrator=None, timeout=300.0):
        seen.append((spec, text))
        return Reply(text=f"{spec} says hi", session_id="remote")

    monkeypatch.setattr(pool.router, "ask_session", fake_attach)

    await pool.ask("k", "connect data-13")
    route, reply = await pool.ask("k", "is the build green?")
    assert route.mode == "attach"
    assert route.target == "data-13"
    assert reply.text == "data-13 says hi"
    assert seen == [("data-13", "is the build green?")]
    # Sticky routing must not spawn a pooled subprocess nobody talks to.
    assert FakeSession.created == 0
    await pool.close()


async def test_detach_returns_to_a_fresh_session(
    three: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SessionPool()

    async def fake_attach(spec, text, narrator=None, timeout=300.0):
        return Reply(text="remote", session_id="remote")

    monkeypatch.setattr(pool.router, "ask_session", fake_attach)
    await pool.ask("k", "connect data-13")
    _, reply = await pool.ask("k", "detach")
    assert "Detached" in reply.text
    assert pool.conversations["k"].attached_to is None

    route, _ = await pool.ask("k", "hello")
    assert route.mode == "fresh"
    assert FakeSession.created == 1
    await pool.close()


async def test_connect_to_something_that_is_not_a_session_is_just_a_question(
    three: Path,
) -> None:
    """`connect the dots in this diagram` is not a connection command. Falling
    through is what stops the feature from eating ordinary sentences."""
    pool = SessionPool()
    route, reply = await pool.ask("k", "connect the dots in this diagram")
    assert route.mode == "fresh"
    assert reply.text == "answer-1"
    assert pool.conversations["k"].attached_to is None
    await pool.close()


async def test_new_session_with_nothing_attached_is_not_swallowed(three: Path) -> None:
    """"new session" reads as detach, but with nothing attached it should still
    start a turn rather than reply with a no-op acknowledgement."""
    pool = SessionPool()
    route, reply = await pool.ask("k", "new session")
    assert route.mode == "fresh"
    assert reply.text == "answer-1"
    await pool.close()


async def test_where_am_i(three: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pool = SessionPool()

    async def fake_attach(spec, text, narrator=None, timeout=300.0):
        return Reply(text="remote", session_id="remote")

    monkeypatch.setattr(pool.router, "ask_session", fake_attach)
    _, reply = await pool.ask("k", "where am i")
    assert "Not connected" in reply.text
    await pool.ask("k", "connect uxo-7f")
    _, reply = await pool.ask("k", "where am i")
    assert "uxo-7f" in reply.text
    await pool.close()


async def test_the_connection_is_per_conversation(
    three: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discord and the phone are different callers; one connecting must not drag
    the other along."""
    pool = SessionPool()

    async def fake_attach(spec, text, narrator=None, timeout=300.0):
        return Reply(text=spec, session_id="remote")

    monkeypatch.setattr(pool.router, "ask_session", fake_attach)
    await pool.ask("discord", "connect data-13")
    route, _ = await pool.ask("phone", "hello there")
    assert route.mode == "fresh"
    assert pool.conversations["phone"].attached_to is None
    await pool.close()


async def test_an_explicit_connection_beats_an_inferred_target(
    three: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"what are you working on" infers the *newest* session. While deliberately
    connected to an older one, that inference must not hijack the message."""
    pool = SessionPool()
    seen: list[str] = []

    async def fake_attach(spec, text, narrator=None, timeout=300.0):
        seen.append(spec)
        return Reply(text="ok", session_id="remote")

    monkeypatch.setattr(pool.router, "ask_session", fake_attach)
    await pool.ask("k", "connect data-d6")  # the oldest
    route, _ = await pool.ask("k", "what are you working on")
    assert route.target == "data-d6"
    assert seen == ["data-d6"]
    await pool.close()


async def test_an_inferred_target_still_works_with_no_connection(
    three: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SessionPool()
    seen: list[str] = []

    async def fake_attach(spec, text, narrator=None, timeout=300.0):
        seen.append(spec)
        return Reply(text="ok", session_id="remote")

    monkeypatch.setattr(pool.router, "ask_session", fake_attach)
    route, _ = await pool.ask("k", "what are you working on")
    assert route.mode == "attach"
    assert seen == ["newest"]
    await pool.close()


@pytest.mark.parametrize("utterance", ["help", "Help", "commands", "what can you do"])
async def test_help_is_answered_by_the_system(three: Path, utterance: str) -> None:
    """Bogdan typed control words and watched them reach a model as chat instead.
    Commands the system knows about must never leak through."""
    pool = SessionPool()
    route, reply = await pool.ask("k", utterance)
    assert route.mode == "control"
    assert "session list" in reply.text
    assert FakeSession.created == 0
    await pool.close()


async def test_detach_with_nothing_attached_is_still_answered(three: Path) -> None:
    pool = SessionPool()
    route, reply = await pool.ask("k", "detach")
    assert route.mode == "control"
    assert "Not connected" in reply.text
    assert FakeSession.created == 0
    await pool.close()


async def test_resources_reports_real_numbers(three: Path) -> None:
    pool = SessionPool()
    _, reply = await pool.ask("k", "resources")
    assert "RAM:" in reply.text
    assert "MB available" in reply.text
    await pool.close()


@pytest.mark.parametrize(
    "utterance",
    ["help me understand this function", "what can you do about the failing test"],
)
async def test_help_shaped_questions_still_reach_a_session(three: Path, utterance: str) -> None:
    pool = SessionPool()
    route, _ = await pool.ask("k", utterance)
    assert route.mode == "fresh"
    await pool.close()


async def test_connect_by_number_uses_the_list_you_were_shown(
    three: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`connect 1` must mean the first line of the list you just saw.

    Bogdan typed `connect 1`, a relay session happened to be newest at that
    instant, and he spent ten minutes being answered by something that could not
    see the build. Numbering computed fresh on every command is numbering that
    changes underneath the person using it.
    """
    pool = SessionPool()
    _, listing = await pool.ask("k", "session list")
    assert "1. uxo-7f" in listing.text

    # A new session appears and would now be number 1 in a freshly computed list.
    make_session(three, 400, "newcomer-zz", "/tmp", "ddd", started_at=9000)

    _, reply = await pool.ask("k", "connect 1")
    assert "uxo-7f" in reply.text
    assert "newcomer-zz" not in reply.text
    assert pool.conversations["k"].attached_to == "uxo-7f"
    await pool.close()


async def test_connecting_to_a_number_whose_session_died_says_so(
    three: Path,
) -> None:
    pool = SessionPool()
    await pool.ask("k", "session list")
    (three / "sessions" / "300.sock").unlink()  # uxo-7f goes away
    _, reply = await pool.ask("k", "connect 1")
    assert "has since exited" in reply.text
    assert pool.conversations["k"].attached_to is None
    await pool.close()
