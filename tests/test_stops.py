from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from hotline.stops import install_hook, record_stop, stop_stamp, wait_for_stop


def test_stamp_is_zero_before_any_stop(fake_claude: Path) -> None:
    assert stop_stamp("sess-1") == 0


def test_stamp_advances_on_each_stop(fake_claude: Path) -> None:
    record_stop("sess-1")
    first = stop_stamp("sess-1")
    assert first > 0
    record_stop("sess-1")
    assert stop_stamp("sess-1") > first
    assert stop_stamp("sess-2") == 0


def test_stops_are_logged(fake_claude: Path) -> None:
    record_stop("sess-1")
    from hotline.config import stops_log

    entries = [json.loads(line) for line in stops_log().read_text().splitlines()]
    assert entries[0]["session_id"] == "sess-1"


async def test_wait_returns_on_a_newer_stop(fake_claude: Path) -> None:
    record_stop("sess-1")
    baseline = stop_stamp("sess-1")

    async def later() -> None:
        await asyncio.sleep(0.05)
        record_stop("sess-1")

    task = asyncio.create_task(later())
    assert await wait_for_stop("sess-1", baseline, timeout=5.0) is True
    await task


async def test_wait_times_out_without_a_new_stop(fake_claude: Path) -> None:
    record_stop("sess-1")
    assert await wait_for_stop("sess-1", stop_stamp("sess-1"), timeout=0.3) is False


def test_hook_script_records_a_stop(fake_claude: Path, monkeypatch) -> None:
    """End-to-end through the real script, since that is what Claude Code runs."""
    path, changed = install_hook()
    assert changed
    result = subprocess.run(
        [sys.executable, str(path)],
        input=json.dumps({"session_id": "sess-9"}),
        capture_output=True,
        text=True,
        check=False,
        env={**dict(__import__("os").environ)},
    )
    assert result.returncode == 0
    assert stop_stamp("sess-9") > 0


def test_hook_script_never_fails(fake_claude: Path) -> None:
    path, _ = install_hook()
    for junk in ("", "not json", "[]", '{"session_id": null}'):
        result = subprocess.run(
            [sys.executable, str(path)], input=junk, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, junk


def test_install_hook_preserves_existing_stop_hooks(fake_claude: Path) -> None:
    settings = fake_claude / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "mine"}]}]
                }
            }
        )
    )
    install_hook()
    _, changed = install_hook()
    assert not changed
    entries = json.loads(settings.read_text())["hooks"]["Stop"]
    commands = [h["command"] for e in entries for h in e["hooks"]]
    assert "mine" in commands
    assert len(commands) == 2
