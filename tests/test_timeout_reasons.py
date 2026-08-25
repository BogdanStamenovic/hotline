"""What a timed-out message says about why.

"Not in the transcript yet" has three different causes and the message asserted
one of them. Twice in one night it told a caller the target was "most likely
being held" and to set `crossSessionInbound` -- which was already set, on a
session that had received the message and acted on it. The real cause was a
message queued behind a turn already in flight, which looks identical from
outside and needs the opposite response: wait, rather than change a setting.

The rule these pin down is that each branch reports a condition it has actually
checked, and the one that knows nothing says so instead of guessing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import make_session

import hotline.router as router_module
from hotline.ccsocks import discover
from hotline.router import Watch, _why_no_reply


@pytest.fixture
def session(fake_claude: Path):
    make_session(
        fake_claude,
        4242,
        "target",
        "/home/bodas",
        "sid-target",
        started_at=1,
        tmux="hl-target:@0.%0",
    )
    found = [s for s in discover() if s.pid == 4242]
    assert found
    return found[0]


def watch_for(session, **over) -> Watch:
    fields = {"session": session, "offset": 0, "stamp": 0.0, "marker": "hello"}
    fields.update(over)
    return Watch(**fields)


def set_inbound(home: Path, value: object) -> None:
    path = home / "settings.json"
    path.write_text(json.dumps({} if value is None else {"crossSessionInbound": value}))


def test_a_message_that_landed_says_it_landed(session, monkeypatch) -> None:
    monkeypatch.setattr(router_module, "mid_turn", lambda s, **k: False)

    why = _why_no_reply(watch_for(session, saw_marker=True), session)

    assert "did receive the message" in why


def test_a_busy_target_is_reported_as_queued_not_lost(session, monkeypatch) -> None:
    """The case that was being misdiagnosed."""
    monkeypatch.setattr(router_module, "mid_turn", lambda s, **k: True)

    why = _why_no_reply(watch_for(session), session)

    assert "queued behind" in why
    assert "crossSessionInbound" not in why, "that advice is wrong here"
    assert "Do not resend" in why, "resending queues a second copy"


def test_busy_at_delivery_counts_even_if_it_is_idle_now(session, monkeypatch) -> None:
    """It may well have gone idle by the time the wait gives up; the message was
    still queued behind the turn that was running when it arrived."""
    monkeypatch.setattr(router_module, "mid_turn", lambda s, **k: False)

    why = _why_no_reply(watch_for(session, was_busy=True), session)

    assert "queued behind" in why


def test_an_idle_target_with_inbound_unset_gets_the_setting_advice(
    session, monkeypatch, fake_claude: Path
) -> None:
    monkeypatch.setattr(router_module, "mid_turn", lambda s, **k: False)
    set_inbound(fake_claude, None)

    why = _why_no_reply(watch_for(session), session)

    assert "crossSessionInbound" in why
    assert "'unset'" in why, "say what it currently is, not just what to set it to"


def test_advice_is_not_given_for_a_setting_that_already_has_that_value(
    session, monkeypatch, fake_claude: Path
) -> None:
    """The specific way this was wrong: being told to set something already set."""
    monkeypatch.setattr(router_module, "mid_turn", lambda s, **k: False)
    set_inbound(fake_claude, "accept")

    why = _why_no_reply(watch_for(session), session)

    assert "already" in why
    assert "I do not know why" in why, "admitting ignorance beats a wrong cause"
    assert "hl-target" in why, "point at where the answer actually is"


def test_it_names_the_pane_so_the_caller_can_go_and_look(
    session, monkeypatch, fake_claude: Path
) -> None:
    monkeypatch.setattr(router_module, "mid_turn", lambda s, **k: True)

    assert "tmux attach -t hl-target" in _why_no_reply(watch_for(session), session)


# ---- a latched "busy" used to be permanent ---------------------------------
#
# `mid_turn` returned True the moment the descriptor said "busy", before
# consulting anything else. A descriptor whose status latched -- a session killed
# mid-turn, a crash between the write and the clear -- was therefore permanently
# "working": every route to it produced a stand-in reporting on a turn that had
# ended long ago, and the caller never reached it at all.


def test_a_stale_busy_descriptor_is_not_mid_turn(session, fake_claude: Path) -> None:
    """Nothing that has not touched its transcript in the window is mid-turn,
    whatever its descriptor claims."""
    import os
    import time as time_module

    from hotline.router import MID_TURN_WINDOW, mid_turn

    # transcript_path globs `projects/*/<id>.jsonl` -- a project SUBDIRECTORY.
    # Writing it one level up makes the path resolve to None, which is a
    # different reason for the same answer and would let the stale-status test
    # pass without exercising the fix at all.
    transcript = fake_claude / "projects" / "-home-bodas" / "sid-target.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("{}\n")
    stale = time_module.time() - (MID_TURN_WINDOW + 60)
    os.utime(transcript, (stale, stale))

    busy = session.__class__(**{**session.__dict__, "status": "busy"})

    assert not mid_turn(busy), "a latched status must not outlive the turn"


def test_a_busy_session_that_is_actually_writing_is_mid_turn(session, fake_claude: Path) -> None:
    """The fast path still has to work, or every live turn loses its stand-in."""
    from hotline.router import mid_turn

    # transcript_path globs `projects/*/<id>.jsonl` -- a project SUBDIRECTORY.
    # Writing it one level up makes the path resolve to None, which is a
    # different reason for the same answer and would let the stale-status test
    # pass without exercising the fix at all.
    transcript = fake_claude / "projects" / "-home-bodas" / "sid-target.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("{}\n")  # written just now

    busy = session.__class__(**{**session.__dict__, "status": "busy"})

    assert mid_turn(busy)
