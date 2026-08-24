from __future__ import annotations

from pathlib import Path

from helpers import assistant_entry, user_entry, write_transcript

from hotline.transcript import read_since, size_of

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
