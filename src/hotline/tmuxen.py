"""Spawning a Claude session that Bogdan can walk up to and take over.

The old fresh session was a `claude --output-format stream-json` driven over
pipes. It answered questions perfectly well and was, from a human's point of
view, a ghost: no pane, no way to attach, no way to kill it by name, and nothing
to look at when it went quiet. That was `tofix.md` #1.

A process driven over pipes cannot also be a terminal you type into, so this is
not a thing that could be patched -- the transport had to change. A session is
now an ordinary interactive `claude` running in a detached tmux session, and we
talk to it the same way we talk to a session Bogdan started himself: inject on
the AF_UNIX socket, read the answer back out of the transcript.

The happy discovery that made this cheap: **the CLI already records its own tmux
target in its descriptor** (`"tmux": "hl-discord:@6.%6"`). There was no registry
to invent. `LiveSession.tmux` just had to start reading a field that was already
being written.

What this buys, beyond a pane to attach to: one mechanism instead of two, so
`session kill` means the same thing everywhere, a fresh session survives the
daemon restarting, and the reply path is the transcript reader -- which returns
the final assistant message rather than a stream of raw output.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from time import monotonic

from .ccsocks import LiveSession, discover
from .config import CLAUDE_BIN
from .errors import ClaudeLaunchFailed

# tmux session names may not contain '.' or ':' -- both are target syntax.
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")

PREFIX = "hl-"


def tmux_name(key: str) -> str:
    """A predictable pane name, so `tmux attach -t hl-discord` reaches Discord's session.

    Naming after the conversation rather than the pid is the whole point. Bogdan
    wants to attach to "the one I was just talking to", and he knows which channel
    he was talking on; he does not know what pid it got.
    """
    slug = _UNSAFE.sub("-", key).strip("-").lower() or "session"
    return f"{PREFIX}{slug[:32]}"


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args], capture_output=True, text=True, timeout=15, check=check
    )


def exists(name: str) -> bool:
    return _tmux("has-session", "-t", f"={name}", check=False).returncode == 0


def kill(name: str) -> bool:
    return _tmux("kill-session", "-t", f"={name}", check=False).returncode == 0


def capture(target: str, lines: int = 60) -> str:
    """The last `lines` of what a pane is showing.

    This is the stand-in agent's best evidence: a wedged session and a session
    thinking hard look identical from the outside, but the pane usually says which
    it is -- a spinner and a tool name, or a permission prompt nobody answered.
    """
    result = _tmux(
        "capture-pane", "-p", "-t", target, "-S", f"-{lines}", check=False
    )
    if result.returncode != 0:
        return ""
    return "\n".join(line.rstrip() for line in result.stdout.splitlines()).strip()


def _by_tmux_session(name: str) -> LiveSession | None:
    for session in discover(include_programmatic=True):
        target = session.tmux or ""
        # The descriptor stores "session:@window.%pane"; match the session part.
        if target.split(":", 1)[0] == name:
            return session
    return None


async def spawn(
    key: str,
    cwd: str | None = None,
    bypass: bool = True,
    timeout: float = 90.0,
    name: str | None = None,
) -> LiveSession:
    """Start a claude in its own tmux session and wait until it can be messaged.

    Returns only once the descriptor exists, which is also the point at which the
    socket is listening -- so callers never race the CLI's own startup.
    """
    target = tmux_name(key)
    if exists(target):
        # Reuse a live one; a dead pane left behind by a crash is replaced.
        session = _by_tmux_session(target)
        if session is not None:
            return session
        kill(target)

    argv = [CLAUDE_BIN]
    if bypass:
        argv += ["--permission-mode", "bypassPermissions"]
    if name:
        # The CLI's own display name, which is what `session list`, the session
        # picker and the terminal title all show. Without it a resumed agent comes
        # back as `hotline-36` and stops being recognisable as the thing you
        # resumed -- which is the whole of its identity.
        argv += ["--name", name]
    try:
        _tmux(
            "new-session", "-d", "-s", target,
            *(("-c", cwd) if cwd else ()),
            # -e keeps this out of the tmux server's global environment, which is
            # shared with every pane Bogdan has open.
            "-e", "HOTLINE_SPAWNED=1",
            *argv,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ClaudeLaunchFailed(f"could not start tmux session {target}: {exc}") from exc

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        session = _by_tmux_session(target)
        if session is not None:
            return session
        if not exists(target):
            raise ClaudeLaunchFailed(
                f"{target} exited before registering. Pane said:\n{capture(target)}"
            )
        await asyncio.sleep(0.25)

    pane = capture(target)
    kill(target)
    raise ClaudeLaunchFailed(
        f"{target} started but never registered a session descriptor within "
        f"{timeout:.0f}s. Pane said:\n{pane}"
    )
