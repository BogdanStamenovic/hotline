from __future__ import annotations

from pathlib import Path

from helpers import assistant_entry, user_entry, write_transcript

from hotline.transcript import read_since, size_of, turn_in_flight

SID = "sess-1"


def test_returns_nothing_for_an_unknown_session(fake_claude: Path) -> None:
    assert size_of("missing") == 0
    assert read_since("missing", 0).text == ""


def test_reads_only_what_came_after_the_offset(fake_claude: Path) -> None:
    write_transcript(fake_claude, SID, [user_entry("old question"), assistant_entry("old answer")])
    offset = size_of(SID)
    write_transcript(fake_claude, SID, [user_entry("new question"), assistant_entry("new answer")])
    turn = read_since(SID, offset, marker="new question")
    assert turn.text == "new answer"


def test_ignores_subagent_output(fake_claude: Path) -> None:
    """Sidechain entries are a subagent's internal report. Speaking one back to the
    caller instead of the actual answer is the failure this guards against."""
    write_transcript(
        fake_claude,
        SID,
        [
            user_entry("go"),
            assistant_entry("subagent chatter", sidechain=True),
            assistant_entry("the real answer"),
        ],
    )
    assert read_since(SID, 0, marker="go").text == "the real answer"


def test_waits_for_its_own_message_before_believing_a_reply(fake_claude: Path) -> None:
    """If the session was already mid-turn, the first stop belongs to that turn.
    Without the marker check we would speak somebody else's answer."""
    write_transcript(fake_claude, SID, [assistant_entry("answer to an earlier turn")])
    turn = read_since(SID, 0, marker="my question")
    assert turn.saw_marker is False
    assert turn.text == ""

    write_transcript(fake_claude, SID, [user_entry("my question"), assistant_entry("my answer")])
    turn = read_since(SID, 0, marker="my question")
    assert turn.saw_marker is True
    assert turn.text == "my answer"


def test_collects_tool_names(fake_claude: Path) -> None:
    write_transcript(
        fake_claude, SID, [user_entry("go"), assistant_entry("done", tools=["Bash", "Read"])]
    )
    assert read_since(SID, 0, marker="go").tools == ["Bash", "Read"]


def test_last_assistant_message_wins(fake_claude: Path) -> None:
    write_transcript(
        fake_claude,
        SID,
        [user_entry("go"), assistant_entry("thinking out loud"), assistant_entry("final")],
    )
    assert read_since(SID, 0, marker="go").text == "final"


def test_survives_a_truncated_last_line(fake_claude: Path) -> None:
    """The transcript is being appended to while we read it, so a partial final
    line is normal, not exceptional."""
    path = write_transcript(fake_claude, SID, [user_entry("go"), assistant_entry("ok")])
    with path.open("a") as fh:
        fh.write('{"type":"assistant","message":{"rol')
    assert read_since(SID, 0, marker="go").text == "ok"


# ---- is a session part-way through a turn? ------------------------------
#
# Two cheaper signals were tried in the live build and both were wrong. The
# descriptor's `status` never changes for a tmux-spawned session. And "wrote
# recently with no stop since" is systematically true right after every turn,
# because the Stop hook fires before the turn's final transcript write -- it
# called every session busy for two minutes, and the first live `session kill`
# was answered by a stand-in instead of being executed.


def test_a_finished_turn_is_not_in_flight(fake_claude: Path) -> None:
    write_transcript(fake_claude, "s1", [user_entry("hello"), assistant_entry("hi")])
    assert turn_in_flight("s1") is False


def test_an_unanswered_question_is_in_flight(fake_claude: Path) -> None:
    write_transcript(fake_claude, "s1", [assistant_entry("hi"), user_entry("and now?")])
    assert turn_in_flight("s1") is True


def test_an_outstanding_tool_call_is_in_flight(fake_claude: Path) -> None:
    """A model that says "let me check" and then calls a tool has answered nothing.

    Without this the session looks finished for the whole of its tool call, purely
    because the turn happened to open with a sentence.
    """
    write_transcript(fake_claude, "s1", [
        user_entry("what kernel?"),
        assistant_entry("Let me check that.", tools=["Bash"]),
    ])
    assert turn_in_flight("s1") is True


def test_a_tool_result_is_not_a_new_question(fake_claude: Path) -> None:
    """Tool results are written as `type: "user"` too. Counting them as questions
    would make every tool call look like an unanswered one."""
    write_transcript(fake_claude, "s1", [
        user_entry("what kernel?"),
        assistant_entry("", tools=["Bash"]),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "Linux"}]}},
        assistant_entry("Linux"),
    ])
    assert turn_in_flight("s1") is False


def test_a_subagent_turn_does_not_count(fake_claude: Path) -> None:
    write_transcript(fake_claude, "s1", [
        user_entry("hello"),
        assistant_entry("done"),
        user_entry("subagent prompt", sidechain=True),
    ])
    assert turn_in_flight("s1") is False


def test_no_transcript_is_not_in_flight(fake_claude: Path) -> None:
    assert turn_in_flight("never-existed") is False
