"""Filesystem locations and tunables.

Everything hotline needs to find is derived here so the rest of the package never
hardcodes a path. All of these are overridable by environment variable, which is
what makes the test suite able to run against a fake home.
"""

from __future__ import annotations

import os
from pathlib import Path

CLAUDE_BIN = os.environ.get("HOTLINE_CLAUDE_BIN", "/opt/claude-code/bin/claude")


def claude_home() -> Path:
    return Path(os.environ.get("HOTLINE_CLAUDE_HOME", Path.home() / ".claude"))


def sessions_dir() -> Path:
    """Where the CLI drops one `<pid>.json` descriptor per live session."""
    return claude_home() / "sessions"


def projects_dir() -> Path:
    """Where transcripts live, one `<session-id>.jsonl` per session."""
    return claude_home() / "projects"


def settings_path() -> Path:
    return claude_home() / "settings.json"


def runtime_dir() -> Path:
    """Spool root. Deliberately under /run so it is cleared on reboot -- a stop
    event from before a reboot is meaningless and should not be honoured."""
    env = os.environ.get("HOTLINE_RUNTIME")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(xdg) / "hotline"


def stops_dir() -> Path:
    return runtime_dir() / "stops"


def stops_log() -> Path:
    return runtime_dir() / "stops.jsonl"


# How often the reply waiter re-checks the spool. inotify would avoid the poll but
# would cost a dependency (or a ctypes shim) for a saving measured in milliseconds
# against turns that take seconds.
POLL_INTERVAL = 0.15

DEFAULT_REPLY_TIMEOUT = 300.0

# How long a target's transcript must stop growing, with its descriptor reporting
# "idle", before we accept the reply without ever having seen a stop event. Long
# enough not to trip on the pause between a tool result and the next token; short
# enough that a caller on a phone does not notice it.
QUIET_SECONDS = 2.0


def load_env(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Read a KEY=VALUE file into os.environ without overwriting what is already set.

    Real environment wins, so a systemd unit or a shell export can override the
    file without editing it. Values are not unquoted or expanded -- a bot token is
    an opaque string and the moment this starts interpreting `$` it will corrupt one.
    """
    target = Path(path) if path else Path(__file__).resolve().parent.parent.parent / ".env"
    found: dict[str, str] = {}
    try:
        lines = target.read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        found[key] = value
        os.environ.setdefault(key, value)
    return found
