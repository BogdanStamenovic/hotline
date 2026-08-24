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
from .router import Route, Router, describe, parse_utterance
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
        "--claim", nargs="?", const="discord", metavar="WHERE",
        help="route a conversation to THIS session until released. "
             "'discord' (default), 'voice', or an explicit conversation key. "
             "--claim '' releases it.",
    )
    parser.add_argument(
        "--voice", action="store_true",
        help="give this agent a voice channel (created on demand, not at declaration)",
    )
    parser.add_argument(
        "--adopt", metavar="NAME",
        help="take over a running agent's identity and channel, for a session "
             "respawned to continue its work",
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

    if args.claim is not None:
        return _claim(args.claim, args.session_id, log)

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

    if args.adopt:
        adopted = registry.adopt(args.adopt, session_id)
        if adopted is None:
            print(
                f"hotline: error: no agent called {args.adopt!r} to adopt. "
                "Use --agents to see them, or --declare to start a new one.",
                file=sys.stderr,
            )
            return 1
        log(f"adopted: {adopted.describe()}")
        if adopted.channel_id is None:
            log("it had no channel; use --declare if you want one")
        else:
            log(f"channel: #{channel_slug(adopted.name)}")
        return 0

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


def _control_command(
    route: Route, router: Router, log: Callable[[str], None]
) -> int | None:
    """`session list`, `session kill`, `resources`, `help` -- answered here.

    A thin re-implementation rather than a call into SessionPool: the pool owns
    per-conversation state (what you are connected to, which listing you were
    shown) and a one-shot CLI invocation has none of that. What it shares with the
    pool is the router, which is where resolution actually lives.
    """
    from .pool import HELP_TEXT, describe_resources

    if route.action == "help":
        print(HELP_TEXT)
        return 0
    if route.action == "resources":
        print(describe_resources())
        return 0
    if route.action == "list":
        live = router.sessions()
        if not live:
            log("no live Claude sessions")
            return 0
        for index, session in enumerate(live, 1):
            where = (
                f"tmux attach -t {session.tmux_session}"
                if session.tmux_session
                else "no pane"
            )
            print(f"{index}. {describe(session)} [{session.status or '?'}] ({where})")
        return 0
    if route.action == "kill" and route.target:
        try:
            router.resolve(route.target)
        except HotlineError:
            # Not a session. "kill the process on port 9999" is a question, and
            # answering it with a resolution error would make the feature eat
            # ordinary sentences -- the same rule the pool follows.
            return None
        try:
            print(asyncio.run(router.kill_session(route.target)))
        except HotlineError as exc:
            print(f"hotline: error: {exc}", file=sys.stderr)
            return 1
        return 0
    # `where am i` and `detach` are about a conversation, which a one-shot
    # invocation does not have. Saying so beats pretending.
    print(
        f"hotline: {route.action!r} only means something inside a conversation "
        "(Discord, voice or the phone), not in a one-shot command.",
        file=sys.stderr,
    )
    return 2


def _claim(where: str, session_id: str | None, log: Callable[[str], None]) -> int:
    """Make this session the one a conversation reaches.

    Goes through the daemon rather than editing the bindings file, because the
    daemon holds the conversations in memory and rewrites that file itself -- a
    CLI that wrote it directly would be silently overwritten by the next turn.
    """
    import json as _json
    import urllib.error
    import urllib.request

    key = _conversation_key(where)
    if key is None:
        print(
            f"hotline: error: don't know which conversation {where!r} means. "
            "Use 'discord', 'voice', or an explicit key.",
            file=sys.stderr,
        )
        return 2

    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID")
    target = ""
    if sid:
        for session in discover(include_self=True, include_programmatic=True):
            if session.session_id == sid:
                target = str(session.pid)
                break
        else:
            print(
                "hotline: error: this session is not visible to hotline, so it "
                "cannot be claimed. Is it a live Claude session?",
                file=sys.stderr,
            )
            return 1

    port = os.environ.get("HOTLINE_PORT", "8788")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/bind",
        data=_json.dumps({"key": key, "session": target}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            answer = _json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print(f"hotline: error: {exc.code} {exc.read().decode()[:200]}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"hotline: error: hotlined is not reachable ({exc})", file=sys.stderr)
        return 1

    bound = answer.get("attached_to")
    if bound:
        log(f"{key} now reaches {bound}")
    else:
        log(f"{key} released; it goes back to its own session")
    return 0


def _conversation_key(where: str) -> str | None:
    """Turn 'discord' or 'voice' into the key that transport actually uses."""
    plain = where.strip().lower()
    if plain in ("", "release", "none"):
        plain = ""
    if plain == "discord":
        # The .env calls it DISCORD_TEXT_CHANNEL_ID; the older name is accepted
        # too. Guessing this wrong once already cost a debugging round.
        channel = os.environ.get("DISCORD_TEXT_CHANNEL_ID") or os.environ.get(
            "DISCORD_CHANNEL_ID"
        )
        return f"discord-{channel}" if channel else _live_key("discord-")
    if plain == "voice":
        channel = os.environ.get("DISCORD_VOICE_CHANNEL_ID")
        return f"voice-{channel}" if channel else _live_key("voice-")
    return where.strip() or None


def _live_key(prefix: str) -> str | None:
    """Fall back to a conversation the daemon actually has.

    If the channel id is not in the environment under any name we know, the
    running daemon still knows which conversations exist -- and there is normally
    exactly one per transport. Better than refusing over a variable name.
    """
    import json as _json
    import urllib.request

    port = os.environ.get("HOTLINE_PORT", "8788")
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/sessions", timeout=10
        ) as response:
            stats = _json.loads(response.read()).get("pool", {})
    except (OSError, ValueError):
        return None
    keys = [k["key"] for k in stats.get("keys", []) if str(k["key"]).startswith(prefix)]
    return keys[0] if len(keys) == 1 else None


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

    if (
        args.declare or args.done or args.agents or args.resume or args.voice
        or args.adopt
        or args.claim is not None
    ):
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

    # Control phrases are handled by hotline, not by a model. Without this
    # `hotline "session kill data-b1"` spawns a fresh session and asks it to kill
    # something -- which is how two stray sessions and a hung shell got made while
    # cleaning up two stray sessions.
    fell_through = False
    if not args.to:
        control = parse_utterance(text)
        if control.mode == "control":
            handled = _control_command(control, router, log)
            if handled is not None:
                return handled
            # It looked like a command and was not one ("kill the process on port
            # 9999"). Force it to a plain question, or the dispatch below sees
            # mode="control" with a target and tries to inject into a session that
            # does not exist.
            fell_through = True

    try:
        if args.to:
            log(f"-> {describe(router.resolve(args.to))}")
            reply = asyncio.run(
                router.ask_session(args.to, text, narrator=narrate, timeout=args.timeout)
            )
        else:
            route = Route("fresh", None, text) if fell_through else parse_utterance(text)
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
