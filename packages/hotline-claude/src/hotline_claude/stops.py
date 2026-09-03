"""The reply channel: a `Stop` hook writing to a spool that anyone can watch.

Claude Code fires a `Stop` hook when a session finishes a turn, handing it
`{"session_id": ...}` on stdin. That is the structural turn-complete signal --
better than tailing JSONL for `stop_reason`, and far better than scraping a TUI.

The hook does not talk to a daemon. It touches `<runtime>/stops/<session-id>` and
appends a line to `<runtime>/stops.jsonl`, and that is all. Three reasons:

* It has to be fast and it must never fail. Every turn of every session on this
  machine runs it, including Bogdan's own work. A file write cannot refuse a
  connection, and there is no daemon whose absence breaks his sessions.
* A file spool is readable by the CLI *and* by the daemon, so `hotline` works
  standalone with nothing else running.
* The spool lives under /run, so it is empty after a reboot -- which is correct,
  because a stop event from a previous boot means nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from .config import POLL_INTERVAL, stops_dir, stops_log


def record_stop(session_id: str) -> None:
    """Called by the hook. Must not raise: a crash here is a crash in Bogdan's turn."""
    try:
        directory = stops_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.time_ns()
        marker = directory / session_id.replace("/", "_")
        tmp = marker.with_suffix(".tmp")
        tmp.write_text(str(stamp))
        os.replace(tmp, marker)
        with stops_log().open("a") as fh:
            fh.write(json.dumps({"session_id": session_id, "ns": stamp}) + "\n")
    except Exception:  # noqa: BLE001,S110 - see docstring; a raise here breaks a real turn
        pass


def stop_stamp(session_id: str) -> int:
    """Monotone-ish token for "how many stops has this session had".

    Content, not mtime: a filesystem with coarse mtime granularity would make two
    stops inside the same tick indistinguishable.
    """
    try:
        return int((stops_dir() / session_id.replace("/", "_")).read_text())
    except (OSError, ValueError):
        return 0


async def wait_for_stop(session_id: str, since: int, timeout: float) -> bool:
    """Block until this session records a stop newer than `since`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_stamp(session_id) > since:
            return True
        await asyncio.sleep(POLL_INTERVAL)
    return False


HOOK_SCRIPT = '''#!/usr/bin/env python3
"""hotline Stop hook -- records turn completion to a spool. Installed by
`hotline install-hook`; see hotline/stops.py for why this writes a file rather
than talking to a daemon. Must always exit 0."""
import json, os, sys, time

try:
    payload = json.load(sys.stdin)
    sid = str(payload.get("session_id") or "").replace("/", "_")
    if sid:
        root = os.environ.get("HOTLINE_RUNTIME") or os.path.join(
            os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid(), "hotline")
        d = os.path.join(root, "stops")
        os.makedirs(d, exist_ok=True)
        ns = time.time_ns()
        tmp = os.path.join(d, sid + ".tmp")
        with open(tmp, "w") as fh:
            fh.write(str(ns))
        os.replace(tmp, os.path.join(d, sid))
        with open(os.path.join(root, "stops.jsonl"), "a") as fh:
            fh.write(json.dumps({"session_id": sid, "ns": ns}) + "\\n")
except Exception:
    pass
sys.exit(0)
'''


def hook_path() -> Path:
    from .config import claude_home

    return claude_home() / "hooks" / "hotline-stop.py"


def install_hook() -> tuple[Path, bool]:
    """Write the hook script and register it in settings.json.

    Returns (path, changed). Registration is additive and idempotent: Bogdan may
    have his own Stop hooks and clobbering them would be unforgivable.
    """
    from .config import settings_path

    path = hook_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK_SCRIPT)
    path.chmod(0o755)

    settings_file = settings_path()
    try:
        settings = json.loads(settings_file.read_text())
    except (OSError, ValueError):
        settings = {}

    hooks = settings.setdefault("hooks", {})
    stop_entries = hooks.setdefault("Stop", [])
    command = str(path)
    for entry in stop_entries:
        for hook in entry.get("hooks", []):
            if hook.get("command") == command:
                return path, False
    stop_entries.append(
        {"matcher": "", "hooks": [{"type": "command", "command": command, "timeout": 5}]}
    )
    settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    return path, True
