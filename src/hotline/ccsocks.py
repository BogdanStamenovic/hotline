"""Discovery of, and injection into, live Claude Code sessions.

Every running `claude` writes `~/.claude/sessions/<pid>.json` describing itself and
a sibling `<pid>.<64hex>.key` holding a `peerToken`. The descriptor names an
AF_UNIX socket that speaks newline-delimited JSON. Writing a `user` message to it
puts that message in the session's inbox exactly as if a peer session had sent it.

Two things here are load-bearing and were both learned the hard way:

* The token in the key file is `peerToken`, which is **not** the value of
  `$CLAUDE_CODE_MESSAGING_TOKEN` in the session's own environment. Those are
  different secrets; the env one is for the session's own children.

* A descriptor is only trustworthy if `procStart` still matches field 22 of
  `/proc/<pid>/stat`. Pids are recycled. Without this check a stale descriptor
  eventually points at whatever unrelated process inherited the number, and we
  would connect to it and write a command into it.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import sessions_dir
from .errors import InjectFailed


@dataclass(frozen=True)
class LiveSession:
    pid: int
    session_id: str
    cwd: str
    name: str
    socket_path: str
    token: str
    started_at: int
    kind: str
    status: str | None

    @property
    def cwd_leaf(self) -> str:
        return Path(self.cwd).name


def _proc_starttime(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat, in clock ticks since boot.

    Parsed from after the last ')' because comm is parenthesised and may itself
    contain spaces or parentheses -- splitting the whole line is a classic bug.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    try:
        rest = raw[raw.rindex(")") + 2 :]
        return rest.split()[19]
    except (ValueError, IndexError):
        return None


def _read_token(dirpath: Path, pid: int) -> str | None:
    for key in dirpath.glob(f"{pid}.*.key"):
        try:
            return str(json.loads(key.read_text())["peerToken"])
        except (OSError, ValueError, KeyError):
            continue
    return None


def status_of(pid: int) -> str | None:
    """Re-read just the live status field of a session's descriptor.

    The CLI keeps `status` current ("busy" while a turn runs, "idle" after), which
    makes it a second, independent way to tell that a turn has finished -- worth
    having, because the Stop hook is the kind of thing that can be uninstalled,
    fail to be registered on a session that started before it existed, or simply
    not fire.
    """
    try:
        return json.loads((sessions_dir() / f"{pid}.json").read_text()).get("status")
    except (OSError, ValueError):
        return None


def discover(include_self: bool = False) -> list[LiveSession]:
    """Every live, verified session, newest first.

    Silently skips descriptors that are unparseable, whose process is gone, or
    whose `procStart` no longer matches -- all three mean "not a session we may
    talk to", and none of them are worth an exception.
    """
    out: list[LiveSession] = []
    self_pid = os.getpid()
    directory = sessions_dir()
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return []

    for path in entries:
        try:
            desc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        pid = desc.get("pid")
        sock = desc.get("messagingSocketPath")
        sid = desc.get("sessionId")
        if not isinstance(pid, int) or not sock or not sid:
            continue
        if desc.get("spare"):
            continue
        if not include_self and pid == self_pid:
            continue
        recorded = desc.get("procStartFt") or desc.get("procStart")
        if recorded is not None and _proc_starttime(pid) != str(recorded):
            continue
        if not Path(sock).exists():
            continue
        token = _read_token(directory, pid)
        if token is None:
            continue
        out.append(
            LiveSession(
                pid=pid,
                session_id=sid,
                cwd=desc.get("cwd") or "?",
                name=desc.get("name") or f"pid-{pid}",
                socket_path=sock,
                token=token,
                started_at=int(desc.get("startedAt") or 0),
                kind=desc.get("kind") or "interactive",
                status=desc.get("status"),
            )
        )
    out.sort(key=lambda s: s.started_at, reverse=True)
    return out


async def inject(session: LiveSession, text: str, timeout: float = 5.0) -> None:
    """Put `text` in a live session's inbox as a user message.

    One connect, both frames, then EOF -- the receiver treats the connection as a
    single delivery rather than a standing channel, and holding it open just makes
    the far side wait for a close that isn't coming.
    """
    payload = (
        json.dumps({"type": "auth", "token": session.token})
        + "\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        + "\n"
    )
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(session.socket_path), timeout
        )
    except (TimeoutError, OSError) as exc:
        raise InjectFailed(f"cannot connect to {session.name} at {session.socket_path}: {exc}") from exc
    try:
        writer.write(payload.encode())
        await asyncio.wait_for(writer.drain(), timeout)
        writer.write_eof()
        # The far side sends nothing back on this socket. Draining to EOF is only
        # so we notice a connection that was refused after accept.
        try:
            await asyncio.wait_for(reader.read(), timeout)
        except TimeoutError:
            pass
    except OSError as exc:
        raise InjectFailed(f"write to {session.name} failed: {exc}") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
