"""Idle agents go to sleep, and a message to a sleeping agent wakes it.

Bogdan's rule (10-26): an agent he talks to that is *fully* idle -- nothing
pending, nothing being done, no tool still running -- is shut down to sleep, and
writing in a sleeping agent's channel wakes it again. The operator (whoever
holds sys-admin, today hotline-80) never sleeps.

**Sleep is cheap because waking is a real resume.** `claude --resume <id>` in
the agent's own working directory brings back the same session id with its whole
context, so the registry record, the channel and every binding still point at
it. The handoff/transcript revive in `revive.py` is for agents whose session is
not coming back; this is for ones that were only put down for a while.

**"Fully idle" is checked, not assumed.** Before this, `pool._close_orphans`
killed any hotline-spawned pane whose transcript had not been written for four
hours. A session waiting on a harness-tracked background task (a
`run_in_background` Bash, a Monitor, a background subagent) writes nothing while
it waits, so that rule killed pm-maker2, pm-sot-live and manager-2 with work
still pending. Each condition below is a separate reason a session is busy, and
`busy_reason` names the one it found, so a session that is kept awake says why.

Only panes hotline spawned (`hl-*`) are put to sleep. A session in a terminal he
opened himself is one he may be sitting at, and nothing here can wake it back
into his terminal.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .agents import Agent, Registry
from .ccsocks import LiveSession, discover
from .config import sessions_dir
from .errors import HotlineError
from .transcript import transcript_path

# Thirty minutes of full idleness. Short because waking costs seconds and loses
# nothing; not shorter because he often answers a few minutes after a reply, and
# a wake on every message would put a cold start in front of each one.
IDLE_AFTER = 30 * 60.0

# The CLI's Bash and Monitor tools run each command as a direct child shell that
# sources the session's shell snapshot; a background one stays a child until it
# exits. MCP servers are children too, and are not work, so the marker matters.
_TOOL_SHELL_MARK = "/.claude/shell-snapshots/"


def _descriptor(pid: int) -> dict[str, Any]:
    try:
        return json.loads((sessions_dir() / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return {}


def tool_children(pid: int, proc: Path = Path("/proc")) -> list[str]:
    """Command lines of the tool shells still running under session `pid`."""
    found: list[str] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            # Field 4 is the parent pid; the command name (field 2) may contain
            # spaces and parentheses, so split after its closing paren.
            ppid = int(stat[stat.rindex(")") + 2 :].split()[1])
            if ppid != pid:
                continue
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, ValueError, IndexError):
            continue
        if _TOOL_SHELL_MARK in cmd:
            found.append(cmd.strip())
    return found


def _newest_write(session_id: str) -> float | None:
    """Latest mtime of the transcript or any of its subagents' transcripts."""
    path = transcript_path(session_id)
    if path is None:
        return None
    stamps: list[float] = []
    for candidate in [path, *path.with_suffix("").glob("subagents/*.jsonl")]:
        try:
            stamps.append(candidate.stat().st_mtime)
        except OSError:
            continue
    return max(stamps) if stamps else None


def busy_reason(
    session: LiveSession,
    now: float | None = None,
    idle_after: float = IDLE_AFTER,
    proc: Path = Path("/proc"),
) -> str | None:
    """Why this session must stay awake, or None if it is fully idle.

    The descriptor's `statusUpdatedAt` dates the moment the turn ended, which is
    the right clock: a transcript can sit unwritten through a long tool call.
    The transcript and subagent mtimes are checked as well, because a background
    subagent writes its own file while the parent shows idle.
    """
    moment = time.time() if now is None else now
    desc = _descriptor(session.pid)
    status = desc.get("status", session.status)
    if status != "idle":
        return f"status is {status!r}"
    since = desc.get("statusUpdatedAt")
    if isinstance(since, (int, float)) and moment - since / 1000.0 < idle_after:
        return f"idle only {int(moment - since / 1000.0)}s"
    shells = tool_children(session.pid, proc)
    if shells:
        return f"{len(shells)} tool shell(s) still running: {shells[0][-120:]}"
    newest = _newest_write(session.session_id)
    if newest is not None and moment - newest < idle_after:
        return f"transcript written {int(moment - newest)}s ago"
    # Putting a session down needs evidence it has been idle that long. With no
    # status clock and no transcript there is none, and the cost of guessing
    # wrong (killing live work) is far above the cost of a few idle megabytes.
    if not isinstance(since, (int, float)) and newest is None:
        return "no record of how long it has been idle"
    return None


def exempt(agent: Agent | None) -> bool:
    """The operator never sleeps. His words: "just hotline 80"; the role, not the name,
    so a respawned operator that adopts the identity keeps the exemption."""
    return agent is not None and agent.privileged


def candidates(
    sessions: Iterable[LiveSession],
    registry: Registry,
    bound: set[str],
    prefix: str,
) -> list[tuple[LiveSession, Agent | None]]:
    """Sessions this policy may put to sleep: hotline-spawned, unbound, not the operator."""
    by_id = {a.session_id: a for a in registry.agents.values()}
    out = []
    for s in sessions:
        if not (s.tmux_session or "").startswith(prefix) or s.name in bound:
            continue
        agent = by_id.get(s.session_id)
        if exempt(agent):
            continue
        out.append((s, agent))
    return out


# ---- waking ---------------------------------------------------------------


def sleeping(agent: Agent, live_ids: set[str] | None = None) -> bool:
    """Registered, not finished, session not live, and resumable from its transcript."""
    if agent.done:
        return False
    if live_ids is None:
        live_ids = {s.session_id for s in discover()}
    return agent.session_id not in live_ids and transcript_path(agent.session_id) is not None


def cwd_of(session_id: str) -> str | None:
    """The directory the session ran in, from its transcript.

    `claude --resume` only finds a session from inside the project it belongs
    to, so this is not optional: resuming from anywhere else starts a stranger.
    """
    path = transcript_path(session_id)
    if path is None:
        return None
    try:
        with path.open() as f:
            for line in f:
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        return None
    return None


_waking: dict[str, asyncio.Future[LiveSession]] = {}


async def wake(agent: Agent, timeout: float = 120.0) -> LiveSession:
    """Resume a sleeping agent's own session in a hotline pane and return it.

    Concurrent wakes of the same agent (two messages a second apart) share one
    resume rather than racing two `claude --resume` of one transcript.
    """
    pending = _waking.get(agent.session_id)
    if pending is not None:
        return await asyncio.shield(pending)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[LiveSession] = loop.create_future()
    _waking[agent.session_id] = fut
    try:
        session = await _resume(agent, timeout)
        fut.set_result(session)
        return session
    except BaseException as exc:
        fut.set_exception(exc)
        fut.exception()  # retrieved here so a wake nobody else awaited is not "never retrieved"
        raise
    finally:
        _waking.pop(agent.session_id, None)


async def _resume(agent: Agent, timeout: float) -> LiveSession:
    from . import tmuxen

    cwd = cwd_of(agent.session_id)
    if cwd is None:
        raise HotlineError(f"{agent.name} has no transcript to resume from")
    if not os.path.isdir(cwd):
        raise HotlineError(f"{agent.name} ran in {cwd}, which no longer exists")
    session = await tmuxen.spawn(
        agent.name, cwd=cwd, name=agent.name, resume=agent.session_id, timeout=timeout
    )
    if session.session_id != agent.session_id:
        # The CLI started a new session instead of resuming ours (wrong project,
        # or the transcript was unreadable). Delivering into it would hand his
        # message to a stranger, which is the one outcome worse than failing.
        raise HotlineError(
            f"resuming {agent.name} came back as session {session.session_id[:8]}, "
            f"not {agent.session_id[:8]}; not delivering to it"
        )
    return session
