"""Builders for a fake ~/.claude tree.

Kept in their own module rather than in conftest so the test files import them by
name instead of relying on pytest's conftest magic.
"""

from __future__ import annotations

import json
from pathlib import Path


def make_session(
    home: Path,
    pid: int,
    name: str,
    cwd: str,
    session_id: str,
    started_at: int = 1000,
    status: str | None = "idle",
) -> None:
    """Write a descriptor, a key file, and a stand-in socket.

    `procStart` is deliberately omitted: discover() only verifies it when present,
    so leaving it out exercises everything except the pid-recycling check, which
    has a test of its own that puts the field back with a wrong value.
    """
    sock = home / "sessions" / f"{pid}.sock"
    sock.write_text("")
    (home / "sessions" / f"{pid}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "sessionId": session_id,
                "cwd": cwd,
                "name": name,
                "startedAt": started_at,
                "kind": "interactive",
                "status": status,
                "messagingSocketPath": str(sock),
            }
        )
    )
    (home / "sessions" / f"{pid}.{'a' * 64}.key").write_text(
        json.dumps({"peerToken": f"token-{pid}", "procStart": "1"})
    )


def write_transcript(home: Path, session_id: str, entries: list[dict]) -> Path:
    project = home / "projects" / "-fake"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    with path.open("a") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


def user_entry(text: str, sidechain: bool = False) -> dict:
    return {
        "type": "user",
        "isSidechain": sidechain,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def assistant_entry(text: str, tools: list[str] | None = None, sidechain: bool = False) -> dict:
    # Tool blocks precede the text in a real assistant message, and their order is
    # the order they were called in -- which read_since() is expected to preserve.
    content: list[dict] = [{"type": "tool_use", "name": t, "input": {}} for t in tools or []]
    content.append({"type": "text", "text": text})
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {"role": "assistant", "content": content},
    }
