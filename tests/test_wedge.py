"""A session that holds its socket open but never takes another turn.

The fixtures here are the shape of the real 2026-09-19 failure: the operator
stopped producing assistant turns ten seconds into its life, and two relayed
messages from Bogdan sat in its queue unread for half an hour while every
liveness check reported it healthy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from hotline.wedge import scan


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _enqueue(at: float, text: str = "do the thing"):
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": _iso(at), "content": text}


def _remove(at: float, text: str = "do the thing"):
    return {
        "type": "queue-operation",
        "operation": "remove",
        "timestamp": _iso(at),
        "content": text,
        "reason": "absorbed_mid_turn",
    }


def _assistant(at: float):
    return {"type": "assistant", "timestamp": _iso(at), "message": {"content": []}}


NOW = 1_800_000_000.0


def test_message_unread_past_the_window_is_a_wedge(tmp_path):
    t = _write(tmp_path / "s.jsonl", [_assistant(NOW - 3000), _enqueue(NOW - 1200)])
    verdict = scan(t, now=NOW)
    assert verdict.wedged
    assert verdict.pending == 1
    assert verdict.waiting == pytest.approx(1200, abs=1)


def test_absorbed_message_is_not_a_wedge(tmp_path):
    t = _write(tmp_path / "s.jsonl", [_enqueue(NOW - 1200), _remove(NOW - 1190), _assistant(NOW - 1180)])
    assert not scan(t, now=NOW)


def test_fresh_message_is_given_time_to_be_picked_up(tmp_path):
    """A session mid-turn has not failed; it just has not reached a boundary."""
    t = _write(tmp_path / "s.jsonl", [_enqueue(NOW - 60)])
    verdict = scan(t, now=NOW)
    assert not verdict.wedged
    assert verdict.pending == 1


def test_a_long_tool_call_is_not_a_wedge_if_it_answers_afterwards(tmp_path):
    """The distinguishing signal: it took a turn *after* the message landed."""
    t = _write(tmp_path / "s.jsonl", [_enqueue(NOW - 1200), _assistant(NOW - 30)])
    verdict = scan(t, now=NOW)
    assert not verdict.wedged
    assert "answered since" in verdict.reason


def test_an_assistant_turn_from_before_the_message_does_not_clear_it(tmp_path):
    """The 2026-09-19 case exactly: it answered, then died, then was messaged."""
    t = _write(tmp_path / "s.jsonl", [_assistant(NOW - 1800), _enqueue(NOW - 1200)])
    assert scan(t, now=NOW).wedged


def test_two_identical_messages_need_two_removes(tmp_path):
    t = _write(tmp_path / "s.jsonl", [_enqueue(NOW - 1200), _enqueue(NOW - 1100), _remove(NOW - 1000)])
    verdict = scan(t, now=NOW)
    assert verdict.wedged
    assert verdict.pending == 1


def test_empty_queue_is_healthy(tmp_path):
    t = _write(tmp_path / "s.jsonl", [_assistant(NOW - 10)])
    assert not scan(t, now=NOW)


def test_a_truncated_final_line_is_not_a_crash(tmp_path):
    """The file is appended to while we read it; half a record is normal."""
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps(_enqueue(NOW - 1200)) + "\n" + '{"type":"assist')
    assert scan(t, now=NOW).wedged


def test_a_missing_transcript_is_not_a_wedge(tmp_path):
    assert not scan(tmp_path / "nope.jsonl", now=NOW)


def test_the_real_2026_09_19_failure(tmp_path):
    """Replayed from the actual transcript: verdict at each watchdog pass.

    Real timestamps: the last assistant turn at 17:14:46Z, his messages relayed
    at 17:19:57Z and 17:35:34Z, nothing after. The watchdog runs every 5 minutes.
    """
    died = datetime.fromisoformat("2026-09-19T17:14:46+00:00").timestamp()
    first = datetime.fromisoformat("2026-09-19T17:19:57+00:00").timestamp()
    t = _write(tmp_path / "s.jsonl", [_assistant(died), _enqueue(first, "his instruction")])

    def at(clock: str) -> bool:
        return scan(t, now=datetime.fromisoformat(clock).timestamp()).wedged

    assert not at("2026-09-19T17:20:35+00:00")  # +38s  -- too early to judge
    assert not at("2026-09-19T17:26:35+00:00")  # +6m   -- still inside the window
    assert at("2026-09-19T17:31:35+00:00")      # +11m  -- caught, 3rd pass
    assert at("2026-09-19T17:41:35+00:00")      # +21m  -- and stays caught
