from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import make_session

from hotline.ccsocks import discover
from hotline.errors import AmbiguousSession, SessionNotFound
from hotline.router import Router, parse_utterance


def three(home: Path) -> None:
    make_session(home, 100, "data-d6", "/home/bodas/data", "aaa11111", started_at=1000)
    make_session(home, 200, "data-13", "/home/bodas/data", "bbb22222", started_at=2000)
    make_session(home, 300, "uxo-7f", "/home/bodas/data/uxonews", "ccc33333", started_at=3000)


def test_discovery_is_newest_first(fake_claude: Path) -> None:
    three(fake_claude)
    assert [s.name for s in discover()] == ["uxo-7f", "data-13", "data-d6"]


def test_discovery_skips_recycled_pids(fake_claude: Path) -> None:
    """A descriptor whose procStart no longer matches points at whatever process
    inherited the pid. Injecting there would write a command into a stranger."""
    make_session(fake_claude, 100, "data-d6", "/home/bodas/data", "aaa11111")
    desc = fake_claude / "sessions" / "100.json"
    payload = json.loads(desc.read_text())
    payload["procStart"] = "999999999"
    desc.write_text(json.dumps(payload))
    assert discover() == []


def test_discovery_skips_sessions_with_no_socket(fake_claude: Path) -> None:
    make_session(fake_claude, 100, "data-d6", "/home/bodas/data", "aaa11111")
    (fake_claude / "sessions" / "100.sock").unlink()
    assert discover() == []


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("data-13", "data-13"),
        ("DATA-13", "data-13"),
        ("200", "data-13"),
        ("bbb22", "data-13"),
        ("uxonews", "uxo-7f"),
        ("the one in uxonews", "uxo-7f"),
        ("newest", "uxo-7f"),
        ("the newest one", "uxo-7f"),
        ("oldest", "data-d6"),
        ("the older one", "data-d6"),
        ("second", "data-13"),
        ("session data-13", "data-13"),
        ("the session called data-13", "data-13"),
    ],
)
def test_resolve_accepts_how_people_actually_say_it(
    fake_claude: Path, spec: str, expected: str
) -> None:
    three(fake_claude)
    assert Router().resolve(spec).name == expected


def test_resolve_reports_ambiguity_rather_than_guessing(fake_claude: Path) -> None:
    """Picking one at random would land a bypassPermissions command in the wrong
    session, which is the single worst thing this program can do."""
    three(fake_claude)
    with pytest.raises(AmbiguousSession) as exc:
        Router().resolve("data")
    assert "data-13" in str(exc.value) and "data-d6" in str(exc.value)


def test_resolve_lists_candidates_when_nothing_matches(fake_claude: Path) -> None:
    three(fake_claude)
    with pytest.raises(SessionNotFound) as exc:
        Router().resolve("nonexistent")
    assert "uxo-7f" in str(exc.value)


def test_resolve_with_no_live_sessions(fake_claude: Path) -> None:
    with pytest.raises(SessionNotFound):
        Router().resolve("anything")


def test_ordinal_past_the_end_is_an_error(fake_claude: Path) -> None:
    three(fake_claude)
    with pytest.raises(SessionNotFound):
        Router().resolve("fifth")


@pytest.mark.parametrize(
    "utterance,mode,target,text",
    [
        ("what's in ~/data", "fresh", None, "what's in ~/data"),
        ("new session, list the files", "fresh", None, "list the files"),
        ("join data-13, what's failing?", "attach", "data-13", "what's failing?"),
        ("join data-13 what's failing?", "attach", "data-13", "what's failing?"),
        ("attach to uxonews, is the build green", "attach", "uxonews", "is the build green"),
        ("ask watchdog, anything broken?", "agent", "watchdog", "anything broken?"),
        ("what are you working on", "attach", "newest", "What are you working on right now?"),
    ],
)
def test_parse_utterance(utterance: str, mode: str, target: str | None, text: str) -> None:
    route = parse_utterance(utterance)
    assert (route.mode, route.target, route.text) == (mode, target, text)


def test_ambiguous_phrasing_defaults_to_fresh() -> None:
    """Guessing 'attach' wrongly puts a command in a session Bogdan is using.
    Guessing 'fresh' wrongly just costs a new subprocess."""
    for utterance in ("rejoin the party", "asking for a friend", "joins are slow in postgres"):
        assert parse_utterance(utterance).mode == "fresh"
