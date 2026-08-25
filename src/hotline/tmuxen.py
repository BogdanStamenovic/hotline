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
import logging
import re
import subprocess
from time import monotonic

from .ccsocks import LiveSession, discover, refuse_if_self
from .config import CLAUDE_BIN
from .errors import ClaudeLaunchFailed, HotlineError, SessionNotFound

log = logging.getLogger(__name__)

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
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=15, check=check)


def _detached_tmux(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a tmux command in its own systemd scope, so a server it has to start
    does not end up in ours.

    Whoever first runs `tmux new-session` when no server is listening becomes the
    server's parent, and the server inherits that process's cgroup. When that
    parent is `hotlined.service`, the default KillMode=control-group means every
    stop, restart or crash of the daemon kills the tmux server, which SIGHUPs
    every pane -- so one restart takes out every agent session on the machine,
    including whichever one asked for the restart. That is not hypothetical: it
    happened on 2026-08-24 and cost four sessions.

    A transient scope makes the placement deliberate instead of a race. When a
    server is already listening this costs one empty scope that `--collect`
    reaps immediately, so it is safe to use for every spawn rather than trying
    to detect which call will be the unlucky one.

    Falls back to a plain `tmux` if systemd-run is unavailable: an unprotected
    session beats no session, and `KillMode=process` on the unit covers this
    case too.
    """
    try:
        return subprocess.run(
            ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--", "tmux", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        log.warning("systemd-run unavailable (%s); starting tmux unscoped", exc)
        return _tmux(*args)


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
    result = _tmux("capture-pane", "-p", "-t", target, "-S", f"-{lines}", check=False)
    if result.returncode != 0:
        return ""
    return "\n".join(line.rstrip() for line in result.stdout.splitlines()).strip()


def _by_tmux_session(name: str, include_self: bool = False) -> LiveSession | None:
    for session in discover(include_self=include_self, include_programmatic=True):
        target = session.tmux or ""
        # The descriptor stores "session:@window.%pane"; match the session part.
        if target.split(":", 1)[0] == name:
            return session
    return None


# ---- typing at a live session ---------------------------------------------
#
# Everything below writes keystrokes into somebody's terminal. `_resolve` is the
# only thing standing between that and a pane Bogdan is sitting in front of, so
# it runs before every one of them, every time, rather than being hoisted by a
# caller that then holds a stale answer.


def _resolve(target: str) -> LiveSession:
    """The live Claude behind a tmux target, or a refusal.

    Three things have to hold before anything is typed, and all three are
    checked here so no caller can skip one:

    * the tmux session exists right now -- not when the roster was computed;
    * a `claude` currently registers that session as its own pane, which is what
      distinguishes an agent's pane from a shell of his that happens to be
      named similarly. A target nothing claims gets a refusal, never a
      best-effort keystroke;
    * it is not hotline itself. `include_self=True` on the lookup is what makes
      that check reachable at all -- `discover()` hides our own pid by default,
      so without it the guard could never fire and would be decoration.
    """
    name = target.split(":", 1)[0]
    if not name:
        raise SessionNotFound("no tmux target given")
    if not exists(name):
        raise SessionNotFound(f"tmux session {name} is gone")
    session = _by_tmux_session(name, include_self=True)
    if session is None:
        raise SessionNotFound(
            f"no live Claude session claims tmux target {target}; refusing to type into it"
        )
    refuse_if_self(session, "interrupt")
    return session


async def interrupt(target: str) -> LiveSession:
    """`tmux send-keys -t <target> Escape` -- cancel the current turn, keep the process.

    The only confirmed in-band way to cancel. **Not a signal**: the spike behind
    `SERVER-PLAN.md` found SIGINT terminates a session rather than cancelling its
    turn, in both tmux and headless modes, so there is no signal to reach for
    here and reaching for one would end the thing it is meant to spare.

    This is deliberately the *only* place that knows how a cancel is performed.
    Callers ask for it by name and know nothing of tmux or ptys, so the day
    `ccsocks` grows a cancel message this becomes a one-function swap rather
    than an audit of every endpoint.

    Returns the session it acted on, so a caller can report which process it
    actually reached instead of echoing the name it was given.
    """
    session = _resolve(target)
    result = _tmux("send-keys", "-t", target, "Escape", check=False)
    if result.returncode != 0:
        raise HotlineError(
            f"could not send Escape to {target}: {result.stderr.strip() or 'tmux failed'}"
        )
    return session


# The CLI's slash-command autocomplete opens on `/` and Enter accepts what is
# highlighted, so the two keystrokes cannot be one `send-keys` call: they need a
# beat between them for the menu to settle on the command that was typed. This
# is pacing a terminal UI, not waiting for work to finish -- the actual
# completion signal is watched for separately and is never a timer.
COMMAND_SETTLE = 0.4


async def send_command(target: str, command: str, settle: float = COMMAND_SETTLE) -> LiveSession:
    """Type a slash command into a session's pty and press Enter.

    `ccsocks.inject()` cannot do this and it is not a near miss: injecting
    `/compact` was observed landing as an ordinary peer user message, which the
    model then explained it had no tool to act on. The pty goes through the
    CLI's own input handling, which is what actually runs the command -- the
    same channel `interrupt` uses, and the reason anything built on this is
    limited to sessions that have a pane.
    """
    session = _resolve(target)
    typed = _tmux("send-keys", "-t", target, command, check=False)
    if typed.returncode != 0:
        raise HotlineError(
            f"could not type {command!r} into {target}: "
            f"{typed.stderr.strip() or 'tmux failed'}"
        )
    await asyncio.sleep(settle)
    entered = _tmux("send-keys", "-t", target, "Enter", check=False)
    if entered.returncode != 0:
        raise HotlineError(
            f"typed {command!r} into {target} but could not press Enter: "
            f"{entered.stderr.strip() or 'tmux failed'}"
        )
    return session


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
        _detached_tmux(
            "new-session",
            "-d",
            "-s",
            target,
            *(("-c", cwd) if cwd else ()),
            # -e keeps this out of the tmux server's global environment, which is
            # shared with every pane Bogdan has open.
            "-e",
            "HOTLINE_SPAWNED=1",
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
