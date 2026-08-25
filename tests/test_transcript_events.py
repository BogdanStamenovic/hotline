"""`events_since` -- the structured projection the map is built from.

`read_since` answers "what did it say"; this answers "what did it do". The two
share the file, the offset discipline and `_is_real_user_turn`, and these tests
exist mostly to pin the three places where the structured reader has to be
stricter than the reply reader: line boundaries, synthetic user records, and
saying so when it can no longer read the file at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers import assistant_entry, user_entry, write_transcript

from hotline.transcript import events_since, size_of

SID = "sess-1"


def kinds(slice_):
    return [(e.kind, e.tool or e.text) for e in slice_.events]


def stamped(entry: dict, at: str) -> dict:
    return {**entry, "timestamp": at}


def test_a_turn_comes_back_as_prompt_then_tools_then_text(fake_claude: Path) -> None:
    write_transcript(
        fake_claude,
        SID,
        [
            user_entry("fix the build"),
            {"type": "assistant", "isSidechain": False, "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "id": "t1",
                             "input": {"command": "make"}}]}},
            {"type": "user", "isSidechain": False, "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
            assistant_entry("built it"),
        ],
    )
    found = events_since(SID, 0)
    assert found.trustworthy
    assert kinds(found) == [
        ("user", "fix the build"), ("tool", "Bash"), ("assistant", "built it")
    ]
    # The tool result is a `type: "user"` record and must not read as a turn --
    # the same trap `_is_real_user_turn` already exists for.
    assert next(e for e in found.events if e.kind == "user").text == "fix the build"
    assert found.events[1].detail == {"command": "make"}
    assert found.events[1].tool_use_id == "t1"


def test_it_reads_only_what_is_new_and_stops_on_a_line_boundary(fake_claude: Path) -> None:
    """The offset is stored, so half a record consumed is a record lost forever.

    `read_since` gets away with `fh.tell()` because it re-reads the same tail
    every time; an incremental reader cannot."""
    write_transcript(fake_claude, SID, [user_entry("first")])
    offset = size_of(SID)

    path = fake_claude / "projects" / "-fake" / f"{SID}.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps(user_entry("second"))[:40])  # a line still being written

    partial = events_since(SID, offset)
    assert partial.events == []
    assert partial.offset == offset

    with path.open("a") as fh:
        fh.write(json.dumps(user_entry("second"))[40:] + "\n")
    complete = events_since(SID, offset)
    assert kinds(complete) == [("user", "second")]
    assert complete.offset == size_of(SID)


def test_records_the_cli_wrote_for_the_user_do_not_open_a_turn(fake_claude: Path) -> None:
    """A skill load, a slash command's echo and a subagent status line are all
    `type: "user"` with no tool_result. Counting them would open a phase per
    tool call on any session that uses skills."""
    write_transcript(
        fake_claude,
        SID,
        [
            user_entry("<task-notification>\n<task-id>abc</task-id>"),
            user_entry("<command-name>/compact</command-name>"),
            user_entry("<local-command-stdout>done</local-command-stdout>"),
            user_entry("Base directory for this skill: /home/bodas/.claude/skills/x"),
            user_entry("[Request interrupted by user]"),
            {"type": "user", "isCompactSummary": True, "isSidechain": False, "message": {
                "role": "user", "content": "This session is being continued from"}},
            user_entry("what he actually typed"),
        ],
    )
    assert kinds(events_since(SID, 0)) == [("user", "what he actually typed")]


def test_a_subagents_tools_arrive_flagged_and_in_time_order(fake_claude: Path) -> None:
    """Sidechain turns live in their own files on Claude Code 2.1.241.

    Every record in the main transcript reads `isSidechain: false`; each subagent
    gets `<session-id>/subagents/agent-*.jsonl` instead. Reading only the main
    file would make `via_subagent` dead code and the map silently thinner than
    the truth."""
    write_transcript(
        fake_claude,
        SID,
        [
            stamped(user_entry("delegate it"), "2026-08-26T10:00:00.000Z"),
            stamped({"type": "assistant", "isSidechain": False, "message": {
                "role": "assistant", "content": [
                    {"type": "tool_use", "name": "Agent", "id": "t1", "input": {}}]}},
                "2026-08-26T10:00:01.000Z"),
            stamped(assistant_entry("delegated"), "2026-08-26T10:00:09.000Z"),
        ],
    )
    subagents = fake_claude / "projects" / "-fake" / SID / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-aaa.jsonl").write_text(
        json.dumps(stamped({"type": "assistant", "isSidechain": True, "message": {
            "role": "assistant", "content": [
                {"type": "tool_use", "name": "Grep", "id": "s1", "input": {"pattern": "x"}}]}},
            "2026-08-26T10:00:05.000Z")) + "\n"
    )

    found = events_since(SID, 0)
    assert kinds(found) == [
        ("user", "delegate it"), ("tool", "Agent"), ("tool", "Grep"), ("assistant", "delegated")
    ]
    assert [e.is_sidechain for e in found.events] == [False, False, True, False]
    assert found.sidechains["agent-aaa.jsonl"] > 0

    # Second read: the subagent file's own offset is honoured, so nothing repeats.
    again = events_since(SID, found.offset, sidechains=found.sidechains)
    assert again.events == []


def test_a_compaction_arrives_with_its_real_numbers(fake_claude: Path) -> None:
    """§9.7. These are what the compact button reports instead of a generic tick."""
    write_transcript(
        fake_claude,
        SID,
        [{"type": "system", "subtype": "compact_boundary", "isSidechain": False,
          "content": "Conversation compacted",
          "compactMetadata": {"trigger": "manual", "preTokens": 48027,
                              "postTokens": 4070, "cumulativeDroppedTokens": 43957,
                              "durationMs": 71104}}],
    )
    event = events_since(SID, 0).events[0]
    assert event.kind == "compact"
    assert event.detail["pre_tokens"] == 48027
    assert event.detail["post_tokens"] == 4070
    assert event.detail["duration_ms"] == 71104


def test_an_unreadable_slice_says_so_instead_of_reading_empty(fake_claude: Path) -> None:
    """The loopback-doorbell failure shape. A read that produces nothing
    recognisable must not look like an agent that did nothing."""
    path = write_transcript(fake_claude, SID, [user_entry("real")])
    offset = size_of(SID)
    with path.open("a") as fh:
        fh.write("this is not json at all\nnor is this\n")

    found = events_since(SID, offset)
    assert found.events == []
    assert found.unparseable == 2
    assert found.recognised == 0
    assert found.trustworthy is False


def test_an_empty_read_is_trustworthy(fake_claude: Path) -> None:
    """Nothing new is not the same as nothing readable, and conflating them
    would degrade every idle session on the box."""
    write_transcript(fake_claude, SID, [user_entry("hello")])
    found = events_since(SID, size_of(SID))
    assert found.events == []
    assert found.trustworthy is True


def test_a_record_type_nobody_has_seen_yet_is_not_a_failure(fake_claude: Path) -> None:
    """The CLI adds record types constantly -- `frame-link`, `atis-latch`,
    `queue-operation` all appeared without warning. Treating an unknown `type`
    as corruption would raise a false alarm every release."""
    path = write_transcript(fake_claude, SID, [user_entry("hello")])
    with path.open("a") as fh:
        fh.write(json.dumps({"type": "something-invented-next-week", "v": 1}) + "\n")
    found = events_since(SID, 0)
    assert found.trustworthy is True
    assert found.unparseable == 0
    assert kinds(found) == [("user", "hello")]


def test_a_missing_transcript_is_empty_and_keeps_the_offset(fake_claude: Path) -> None:
    found = events_since("never-existed", 41)
    assert found.events == []
    assert found.offset == 41
    assert found.trustworthy is True


def test_a_big_read_is_truncated_at_a_line_boundary_and_resumes(fake_claude: Path) -> None:
    """First contact with a week-old transcript must not pull it all in at once."""
    write_transcript(fake_claude, SID, [user_entry(f"turn {i}") for i in range(50)])
    first = events_since(SID, 0, max_bytes=400)
    assert first.truncated is True
    assert first.overlong is False
    assert 0 < first.offset < size_of(SID)
    second = events_since(SID, first.offset, max_bytes=400)
    assert second.events
    seen = [e.text for e in first.events] + [e.text for e in second.events]
    assert seen == [f"turn {i}" for i in range(len(seen))]
