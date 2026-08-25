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
    tmux: str | None = None,
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
                "tmux": tmux,
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
    # Convenience shape, not a faithful one: the CLI never writes text and a tool
    # call into the same assistant record (0 of 665 on this machine). It is kept
    # because it makes read_since()'s tool-ordering tests compact, and read_since
    # handles both shapes. Anything asserting on turn boundaries should use
    # separate entries instead.
    content: list[dict] = [{"type": "tool_use", "name": t, "input": {}} for t in tools or []]
    content.append({"type": "text", "text": text})
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {"role": "assistant", "content": content},
    }


class FakeWorld:
    """A pool with its transport replaced, one layer lower than the old fake.

    The pool used to be tested by swapping in a fake `FreshSession`, which stopped
    meaning anything once a session became a tmux pane reached over a socket. The
    seam moved down to where it should always have been: `tmuxen.spawn` really
    writes a descriptor into the fake `~/.claude`, and `Router.deliver` /
    `Router.collect` stand in for the socket and the transcript. So resolution,
    stickiness, eviction and the busy path all run their real code here, and only
    the two calls that would touch the machine are faked.
    """

    def __init__(self, home, monkeypatch, pool_module) -> None:
        self.home = home
        self.spawned: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.wire: list[tuple[str, str]] = []
        self.busy: set[str] = set()
        self.replies: dict[str, str] = {}
        self.delay = 0.0
        self.explode: set[str] = set()
        self.killed: list[str] = []
        self._pid = 9000
        self._turns: dict[str, int] = {}
        self._patch(monkeypatch, pool_module)

    # -- the two faked calls --------------------------------------------

    async def spawn(self, key, cwd=None, bypass=True):
        import hotline.tmuxen as tmuxen_module
        from hotline.ccsocks import discover

        name = tmuxen_module.tmux_name(key)
        for session in discover():
            if session.tmux_session == name:
                return session
        self._pid += 1
        self.spawned.append(name)
        make_session(
            self.home,
            self._pid,
            name,
            cwd or "/home/bodas",
            session_id=f"sid-{self._pid}",
            started_at=self._pid,
            tmux=f"{name}:@0.%0",
        )
        return next(s for s in discover() if s.pid == self._pid)

    async def deliver(self, spec, text, origin=None):
        from hotline.router import Watch

        session = self._router.resolve(spec)
        # Record what actually goes on the wire, header and all, so a test can
        # assert on the provenance a session really receives. `delivered` keeps
        # the bare text -- most tests care about the message, not its envelope --
        # and `wire` keeps the whole thing for the ones that care.
        wire = origin.wrap(text) if origin is not None else text
        self.delivered.append((session.name, text))
        self.wire.append((session.name, wire))
        return Watch(
            session=session,
            offset=0,
            stamp=0.0,
            marker=wire,
            was_busy=session.name in self.busy,
        )

    async def collect(self, watch, narrator=None, timeout=300.0):
        import asyncio

        from hotline.errors import ClaudeLaunchFailed
        from hotline.fresh import Reply

        name = watch.session.name
        if self.delay:
            await asyncio.sleep(self.delay)
        if name in self.explode:
            raise ClaudeLaunchFailed("that session died mid-turn")
        self._turns[name] = self._turns.get(name, 0) + 1
        text = self.replies.get(name) or f"answer-{self._turns[name]}"
        return Reply(text=text, session_id=watch.session.session_id, subtype="attached")

    async def kill_session(self, spec):
        session = self._router.resolve(spec)
        self.killed.append(session.name)
        (self.home / "sessions" / f"{session.pid}.json").unlink(missing_ok=True)
        return f"{session.name} stopped."

    # -- wiring ----------------------------------------------------------

    def _patch(self, monkeypatch, pool_module) -> None:
        import hotline.tmuxen as tmuxen_module
        from hotline.router import Router

        monkeypatch.setattr(pool_module.tmuxen, "spawn", self.spawn)
        monkeypatch.setattr(tmuxen_module, "exists", lambda name: name in self.spawned)
        monkeypatch.setattr(
            Router,
            "deliver",
            lambda r, spec, text, origin=None: self.deliver(spec, text, origin),
        )
        monkeypatch.setattr(
            Router,
            "collect",
            lambda r, watch, narrator=None, timeout=300.0: self.collect(watch, narrator, timeout),
        )
        monkeypatch.setattr(Router, "kill_session", lambda r, spec: self.kill_session(spec))
        self._router = Router()

    def turns_for(self, name: str) -> int:
        return self._turns.get(name, 0)
