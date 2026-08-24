"""Command-line interface for hotline.

Keeps stdout clean -- only Claude's answer goes there, so `hotline ... | pbcopy`
and `$(hotline ...)` do the obvious thing. All progress, narration, warnings and
errors go to stderr.

Exit codes: 0 success, 1 one or more operations failed, 2 usage error / user
aborted.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

from . import __version__
from .agents import DEFAULT_KEEP_DAYS, Registry
from .ccsocks import discover
from .channels import from_env as channels_from_env
from .channels import slug as channel_slug
from .config import load_env
from .errors import HotlineError
from .fresh import Event
from .guard import install_guard
from .router import Router, describe, parse_utterance
from .stops import install_hook, stops_dir


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="hotline",
        description="Talk to a Claude Code session -- a fresh one, or one already running.",
    )
    parser.add_argument("text", nargs="*", help="what to say; omit with --list or --install-hook")
    parser.add_argument(
        "--to",
        metavar="SESSION",
        help="inject into a live session instead of starting a fresh one. "
        "Accepts a name, a pid, a session-id prefix, a directory ('uxonews'), "
        "or an ordinal ('newest', 'the older one').",
    )
    parser.add_argument("--cwd", help="working directory for a fresh session")
    # The agent lifecycle. These are how a session says who it is, which is
    # cooperative by necessity: a subagent registers no session descriptor and
    # writes nothing to its parent's transcript, so hotline cannot discover one
    # by looking. It has to be told.
    parser.add_argument(
        "--declare", metavar="TASK",
        help="register this session and what it is working on (editable; re-declare to retask)",
    )
    parser.add_argument(
        "--parent", metavar="NAME",
        help="with --declare: the agent that spawned this one, for subagents",
    )
    parser.add_argument(
        "--no-channel", action="store_true",
        help="with --declare: do not give this agent a channel of its own",
    )
    parser.add_argument(
        "--keep-days", type=float, default=None, metavar="N",
        help="with --declare: retention after completion (default 3)",
    )
    parser.add_argument(
        "--done", action="store_true",
        help="mark this session finished; its channel is deleted and its record kept",
    )
    parser.add_argument(
        "--handoff", metavar="PATH",
        help="with --done: the handoff this agent wrote, which becomes its only record",
    )
    parser.add_argument("--agents", action="store_true", help="list known agents and exit")
    parser.add_argument(
        "--voice", action="store_true",
        help="give this agent a voice channel (created on demand, not at declaration)",
    )
    parser.add_argument(
        "--resume", metavar="NAME",
        help="bring a finished agent back: new session seeded from its handoff, "
             "channel recreated, task re-declared",
    )
    parser.add_argument(
        "--session-id", metavar="ID",
        help="which session to act on (default $CLAUDE_CODE_SESSION_ID)",
    )
    parser.add_argument("--list", action="store_true", help="list live sessions and exit")
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="install the Stop hook that makes --to able to hear replies",
    )
    parser.add_argument(
        "--timeout", type=float, default=300.0, metavar="SEC", help="give up after SEC (default 300)"
    )
    parser.add_argument(
        "--no-guard",
        action="store_true",
        help="with --install-hook: skip the PreToolUse denylist (it is on by default)",
    )
    parser.add_argument(
        "--no-bypass",
        action="store_true",
        help="do not pass --permission-mode bypassPermissions to a fresh session",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="narrate tool calls as they happen")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress non-error output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _agent_command(args: argparse.Namespace, log: Callable[[str], None]) -> int:
    """`--declare`, `--done` and `--agents`, which never touch a model."""
    # The daemon loads .env at startup; the CLI never did, because until now
    # nothing in it needed a token. An agent runs these commands from its own
    # shell, where the bot token is not exported -- without this, every agent
    # would be told "no Discord configured" and quietly get no channel.
    load_env()
    registry = Registry()

    if args.agents:
        known = sorted(registry.agents.values(), key=lambda a: a.declared_at, reverse=True)
        if not known:
            log("no agents have declared themselves")
            return 0
        for agent in known:
            print(agent.describe())
        return 0

    if args.resume:
        return _resume(args.resume, registry, args.cwd, log)

    session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        print(
            "hotline: error: no session id. Run this inside a Claude session, or "
            "pass --session-id.",
            file=sys.stderr,
        )
        return 2

    if args.voice:
        speaker = registry.get(session_id)
        if speaker is None:
            print(
                "hotline: error: declare yourself first, so the voice channel has "
                "a name and gets cleaned up when you are done.",
                file=sys.stderr,
            )
            return 1
        manager = channels_from_env()
        if manager is None:
            print("hotline: error: Discord is not configured", file=sys.stderr)
            return 1
        try:
            speaker.voice_channel_id = manager.create_voice(speaker.name)
        except HotlineError as exc:
            print(f"hotline: error: {exc}", file=sys.stderr)
            return 1
        registry.save()
        # stdout, because this is the answer: the agent repeats it to Bogdan.
        print(channel_slug(speaker.name))
        return 0

    if args.declare:
        name = _session_name(session_id) or session_id[:8]
        agent = registry.declare(
            session_id,
            name,
            args.declare.strip(),
            parent=args.parent,
            wants_channel=not args.no_channel,
            keep_days=args.keep_days if args.keep_days is not None else DEFAULT_KEEP_DAYS,
        )
        log(f"declared: {agent.describe()}")
        if agent.wants_channel and agent.channel_id is None:
            manager = channels_from_env()
            if manager is None:
                log("no Discord configured; skipping the channel")
            else:
                try:
                    agent.channel_id = manager.create_text(agent.name, topic=agent.task)
                    registry.save()
                    log(f"channel: #{channel_slug(agent.name)}")
                except HotlineError as exc:
                    # The agent is registered either way. Losing its channel is
                    # worth saying out loud, but it is not worth failing the
                    # declaration and leaving the session unregistered.
                    log(f"warning: could not create the channel: {exc}")
        elif agent.channel_id is not None:
            manager = channels_from_env()
            if manager is not None:
                with contextlib.suppress(HotlineError):
                    manager.retopic(agent.channel_id, agent.task)
        return 0

    finished = registry.complete(session_id, handoff=args.handoff)
    if finished is None:
        print(
            "hotline: error: this session never declared itself, so there is "
            "nothing to finish. Use --declare first.",
            file=sys.stderr,
        )
        return 1
    if not finished.handoff:
        # Bogdan chose disposable channels, so the handoff is the only thing that
        # outlives the work. Saying nothing when it is missing would let the whole
        # record of an agent vanish quietly.
        log("warning: finished with no --handoff; nothing will survive the channel")

    manager = channels_from_env()
    for attr in ("channel_id", "voice_channel_id"):
        cid = getattr(finished, attr)
        if cid is None or manager is None:
            continue
        try:
            manager.delete(cid)
            log(f"deleted its {'voice ' if attr.startswith('voice') else ''}channel")
        except HotlineError as exc:
            log(f"warning: could not delete channel {cid}: {exc}")
        else:
            setattr(finished, attr, None)
    registry.save()
    log(f"done: {finished.name}")
    return 0


def _resume(name: str, registry: Registry, cwd: str | None, log: Callable[[str], None]) -> int:
    """Bring a finished agent back from its handoff.

    This is the counterweight to disposable channels. Deleting the channel on
    completion means the handoff is the only thing that survives, so there has to
    be a way to turn that handoff back into a working agent -- otherwise "done"
    is indistinguishable from "lost".

    A new session, not the old one: the old process is gone. What continues is
    the work, seeded with what the last agent wrote down about it.
    """
    from . import tmuxen

    agent = registry.by_name(name)
    if agent is None:
        print(f"hotline: error: no agent called {name!r}. Try --agents.", file=sys.stderr)
        return 1
    if not agent.handoff:
        print(
            f"hotline: error: {agent.name} finished without a handoff, so there is "
            "nothing to resume it from.",
            file=sys.stderr,
        )
        return 1
    handoff = Path(agent.handoff)
    try:
        brief = handoff.read_text()
    except OSError as exc:
        print(f"hotline: error: cannot read {handoff}: {exc}", file=sys.stderr)
        return 1

    try:
        session = asyncio.run(tmuxen.spawn(agent.name, cwd=cwd or None, name=agent.name))
    except HotlineError as exc:
        print(f"hotline: error: could not start a session: {exc}", file=sys.stderr)
        return 1
    log(f"resumed {agent.name} as {session.name} (tmux: {tmuxen.tmux_name(agent.name)})")

    # Re-register under the NEW session id. The old record is retired rather than
    # edited, so the resumed agent gets its own retention clock.
    registry.forget(agent.session_id)
    revived = registry.declare(
        # `agent.name`, not `session.name`: resuming by name and getting back
        # something called `hotline-36` loses the identity you resumed.
        session.session_id, agent.name, agent.task,
        parent=agent.parent, wants_channel=agent.wants_channel, keep_days=agent.keep_days,
    )

    manager = channels_from_env()
    if manager is not None and revived.wants_channel:
        try:
            revived.channel_id = manager.create_text(revived.name, topic=revived.task)
            registry.save()
            log(f"channel: #{channel_slug(revived.name)}")
        except HotlineError as exc:
            log(f"warning: could not recreate the channel: {exc}")

    seed = (
        f"You are resuming work that a previous session finished a stint on. "
        f"Its task was: {agent.task}\n\n"
        f"This is the handoff it left at {handoff}:\n\n{brief}\n\n"
        "Read it, say in one sentence what you understand the state to be, and wait."
    )
    try:
        reply = asyncio.run(Router().ask_session(session.name, seed, timeout=300.0))
    except HotlineError as exc:
        log(f"warning: session started but did not answer: {exc}")
        return 0
    print(reply.text)
    return 0


def _session_name(session_id: str) -> str | None:
    for session in discover(include_self=True, include_programmatic=True):
        if session.session_id == session_id:
            return session.name
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"hotline: error: {exc}", file=sys.stderr)
        return 2

    def log(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr)

    def vlog(message: str) -> None:
        if args.verbose and not args.quiet:
            print(message, file=sys.stderr)

    router = Router(default_cwd=args.cwd, bypass=not args.no_bypass)

    if args.install_hook:
        path, changed = install_hook()
        log(f"Stop hook {'installed' if changed else 'already registered'}: {path}")
        if args.no_guard:
            log("PreToolUse guard: skipped (--no-guard)")
        else:
            guard, guard_changed = install_guard()
            log(f"PreToolUse guard {'installed' if guard_changed else 'already registered'}: {guard}")
        log(f"spool: {stops_dir()}")
        return 0

    if args.declare or args.done or args.agents or args.resume or args.voice:
        return _agent_command(args, log)

    if args.list:
        live = router.sessions()
        if not live:
            log("no live Claude sessions")
            return 0
        for index, session in enumerate(live):
            marker = "newest" if index == 0 else ("oldest" if index == len(live) - 1 else "")
            print(f"{describe(session)}  {session.status or ''} {marker}".rstrip())
        return 0

    text = " ".join(args.text).strip()
    if not text:
        print("hotline: error: nothing to say (give some text, or --list)", file=sys.stderr)
        return 2

    def narrate(event: Event) -> None:
        if event.kind == "tool" or event.kind == "summary":
            vlog(f"  ... {event.detail}")
        elif event.kind == "rate_limit":
            log(f"hotline: rate limit: {event.detail}")

    try:
        if args.to:
            log(f"-> {describe(router.resolve(args.to))}")
            reply = asyncio.run(
                router.ask_session(args.to, text, narrator=narrate, timeout=args.timeout)
            )
        else:
            route = parse_utterance(text)
            if route.mode != "fresh" and route.target:
                log(f"-> {route.mode} {route.target}")
                reply = asyncio.run(
                    router.ask_session(route.target, route.text, narrator=narrate, timeout=args.timeout)
                )
            else:
                log("-> fresh session")
                reply = asyncio.run(
                    router.ask_fresh(route.text, narrator=narrate, timeout=args.timeout)
                )
    except HotlineError as exc:
        print(f"hotline: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("hotline: aborted", file=sys.stderr)
        return 2

    print(reply.text)
    return 0
