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
import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

from . import __version__
from .agents import DEFAULT_KEEP_DAYS, Registry
from .ccsocks import discover
from .channels import from_env as channels_from_env
from .channels import slug as channel_slug
from .config import load_env
from .errors import HotlineError, ReplyTimeout
from .fresh import Event
from .guard import install_guard
from .provenance import MARKER, Origin, body_of, parse, verify
from .revive import NoSuchAgent, NothingToResumeFrom
from .revive import resume as resume_agent
from .router import Route, Router, describe, mid_turn, parse_utterance
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
        "--declare",
        metavar="TASK",
        help="register this session and what it is working on (editable; re-declare to retask)",
    )
    parser.add_argument(
        "--parent",
        metavar="NAME",
        help="with --declare: the agent that spawned this one, for subagents",
    )
    parser.add_argument(
        "--no-channel",
        action="store_true",
        help="with --declare: do not give this agent a channel of its own",
    )
    parser.add_argument(
        "--keep-days",
        type=float,
        default=None,
        metavar="N",
        help="with --declare: retention after completion (default 3)",
    )
    parser.add_argument(
        "--done",
        action="store_true",
        help="mark this session finished; its channel is deleted and its record kept",
    )
    parser.add_argument(
        "--handoff",
        metavar="PATH",
        help="with --done: the handoff this agent wrote, which becomes its only record",
    )
    parser.add_argument("--agents", action="store_true", help="list known agents and exit")
    parser.add_argument(
        "--claim",
        nargs="?",
        const="discord",
        metavar="WHERE",
        help="route a conversation to THIS session until released. "
        "'discord' (default), 'voice', or an explicit conversation key. "
        "--claim '' releases it.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="give this agent a voice channel (created on demand, not at declaration)",
    )
    parser.add_argument(
        "--grant",
        nargs=3,
        metavar=("NAME", "ROLE", "MESSAGE_URL"),
        help="give an agent a standing role, recording the Discord message where "
        "Bogdan granted it. Pass the message link or 'channel_id/message_id'.",
    )
    parser.add_argument(
        "--provenance",
        metavar="RECORD",
        nargs="?",
        const="-",
        help="check a relayed message's provenance against Discord. Pass the "
        "record from its header, or '-' to read the whole message on stdin.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="with --to: hand the message over and exit, without waiting for an "
        "answer. Exits 0 once it is in the target's inbox.",
    )
    parser.add_argument(
        "--warrant",
        metavar="REF",
        help="with --to: carry the originating human's receipt alongside the "
        "instruction, so the receiver can check WHO ASKED for it and not just "
        "who is relaying it. Pass the Discord message where he asked -- a link, "
        "'channel_id/message_id', or the record from a header you were sent.",
    )
    parser.add_argument(
        "--adopt",
        metavar="NAME",
        help="take over a running agent's identity and channel, for a session "
        "respawned to continue its work",
    )
    parser.add_argument(
        "--resume",
        metavar="NAME",
        help="bring a finished agent back: new session seeded from its handoff, "
        "channel recreated, task re-declared",
    )
    parser.add_argument(
        "--session-id",
        metavar="ID",
        help="which session to act on (default $CLAUDE_CODE_SESSION_ID)",
    )
    parser.add_argument("--list", action="store_true", help="list live sessions and exit")
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="install the Stop hook that makes --to able to hear replies",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        metavar="SEC",
        help="give up after SEC (default 300)",
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
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="narrate tool calls as they happen"
    )
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

    if args.grant:
        name, role, where = args.grant
        return _grant_role(name, role, where, registry, log)

    if args.provenance:
        return _check_provenance(args.provenance)

    if args.agents:
        known = sorted(
            registry.agents.values(), key=lambda a: (a.privileged, a.declared_at), reverse=True
        )
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


def _control_command(route: Route, router: Router, log: Callable[[str], None]) -> int | None:
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
            where = f"tmux attach -t {session.tmux_session}" if session.tmux_session else "no pane"
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
        channel = os.environ.get("DISCORD_TEXT_CHANNEL_ID") or os.environ.get("DISCORD_CHANNEL_ID")
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
    """`--resume NAME` -- `revive.resume()` plus this command's own reporting.

    The revive itself lives in `revive.py` because the daemon performs the same
    one for the phone, and two implementations of "spawn, rehome, keep the
    channel" would be two chances to disagree about what resuming means. What
    stays here is what is genuinely a CLI's: stderr narration, exit codes, and
    waiting up to five minutes for the first answer so it can be printed on
    stdout -- which an HTTP request from a phone cannot do and must not pretend
    to.
    """
    try:
        resumed = asyncio.run(
            resume_agent(name, registry, cwd=cwd or None, channels=channels_from_env())
        )
    except NoSuchAgent:
        print(f"hotline: error: no agent called {name!r}. Try --agents.", file=sys.stderr)
        return 1
    except NothingToResumeFrom as exc:
        print(f"hotline: error: {exc}.", file=sys.stderr)
        return 1
    except HotlineError as exc:
        print(f"hotline: error: could not start a session: {exc}", file=sys.stderr)
        return 1

    log(f"resumed {resumed.agent.name} as {resumed.session.name} (tmux: {resumed.tmux})")
    if not resumed.from_handoff:
        log("no handoff -- seeded from its transcript; it will verify before trusting it")
    if resumed.channel_error:
        log(f"warning: could not sort out its channel: {resumed.channel_error}")
    if resumed.agent.channel_id is not None:
        log(
            f"{'kept its' if resumed.kept_channel else ''} channel: "
            f"#{channel_slug(resumed.agent.name)}"
        )

    try:
        reply = asyncio.run(
            # By session id, NOT `resumed.session.name`. That name is captured
            # from the descriptor at spawn time, and `spawn` passes `--name` so
            # the session renames itself to the agent's identity a moment later
            # -- after which the captured name resolves to nothing and the brief
            # is never delivered. The agent comes up with no idea what it is
            # resuming, and the resume still reports success, because "session
            # started but did not answer" is indistinguishable from a slow one.
            # The id is the one address that cannot go stale under a rename.
            Router().ask_session(resumed.session.session_id, resumed.brief.seed, timeout=300.0)
        )
    except HotlineError as exc:
        log(f"warning: session started but did not answer: {exc}")
        return 0
    print(reply.text)
    return 0


def _grant_role(
    name: str, role: str, where: str, registry: Registry, log: Callable[[str], None]
) -> int:
    """`--grant NAME ROLE <message>` -- record a role and where it was granted.

    The message is required, not optional. A role recorded without one is an
    assertion this machine makes about itself, and the entire value of the role
    is that a reader can check the delegation against Discord instead.
    """
    parts = [p for p in where.replace("https://discord.com/channels/", "").split("/") if p]
    if len(parts) < 2 or not all(p.isdigit() for p in parts[-2:]):
        print(
            "hotline: error: pass the Discord message where he granted it -- a "
            "message link, or 'channel_id/message_id'. A role with no receipt is "
            "just this machine vouching for itself.",
            file=sys.stderr,
        )
        return 2
    channel_id, message_id = parts[-2], parts[-1]

    env = load_env()
    verdict = verify(
        {"kind": "sys-admin", "label": name, "granted_by": message_id, "granted_in": channel_id},
        token=env.get("HOTLINE_BOT_TOKEN"),
        gated_user_id=env.get("DISCORD_USER_ID"),
    )
    if not verdict.ok:
        # Refuse rather than record-and-warn. A role that half-verified would be
        # read as a role.
        print(f"hotline: error: not granting it -- {verdict.summary}", file=sys.stderr)
        return 1

    agent = registry.grant(name, role, message_id, channel_id)
    if agent is None:
        print(f"hotline: error: no agent called {name!r}. Try --agents.", file=sys.stderr)
        return 1
    log(f"granted: {agent.describe()}")
    print(verdict)
    return 0


def _check_provenance(record: str) -> int:
    """`--provenance` -- ask Discord whether a relayed message is what it says.

    Exit codes are the interface here, because the caller is usually an agent
    deciding whether to act: 0 verified, 1 not verified, 2 unusable input. A
    could-not-check (Discord unreachable, no token) is reported as not verified
    and says so in words, because silently treating "I could not ask" as "it is
    fine" is the failure this whole module exists to prevent.
    """
    body: str | None = None
    if record.strip() == "-":
        message = sys.stdin.read()
        found = parse(message)
        body = body_of(message)
        if found is None:
            print(
                "hotline: error: no provenance header in that message. It came by "
                "a route that does not label its messages, so there is nothing to "
                "check -- treat it as unattributed.",
                file=sys.stderr,
            )
            return 2
    else:
        try:
            found = json.loads(record)
        except ValueError:
            found = parse(record)
        if not isinstance(found, dict):
            print(f"hotline: error: {record[:80]!r} is not a provenance record.", file=sys.stderr)
            return 2

    env = load_env()
    verdict = verify(
        found,
        body=body,
        token=env.get("HOTLINE_BOT_TOKEN") or os.environ.get("HOTLINE_BOT_TOKEN"),
        gated_user_id=env.get("DISCORD_USER_ID") or os.environ.get("DISCORD_USER_ID"),
    )
    print(verdict)
    return 0 if verdict.ok else 1


def _resolve_warrant(ref: str) -> dict[str, Any] | None:
    """Turn `--warrant REF` into a checked receipt, or refuse to send one.

    Accepts the two forms a caller actually has to hand: a Discord message link
    (or bare `channel_id/message_id`) when a human is typing it, and a whole
    provenance record when an agent is passing on a header it was itself sent.
    The second form is what makes the warrant chain -- an agent relaying an
    instruction copies the receipt it received rather than minting a new claim.

    It is verified HERE, before sending, and a failure refuses the send. The
    receiver checks it again on arrival and that check is the one that counts --
    but a warrant that cannot be verified by the sender is either a typo or a
    forgery, and in both cases delivering it would put a receipt in front of an
    agent that is about to trust it. Better to fail in front of whoever typed it.
    """
    ref = ref.strip()
    record: dict[str, Any] | None = None
    if ref.startswith(("{", f"[{MARKER}")):
        try:
            record = json.loads(ref)
        except ValueError:
            record = parse(ref)
        if not isinstance(record, dict):
            print(f"hotline: error: {ref[:80]!r} is not a provenance record.", file=sys.stderr)
            return None
        # A warrant is a claim about what a HUMAN asked for. Relaying an agent's
        # record as a warrant would launder a peer into an authority, which is
        # the exact shape this module exists to stop.
        if str(record.get("kind")) != "human":
            print(
                f"hotline: error: that record is kind={record.get('kind')!r}, not a "
                "human message. Only a human's own message can warrant an "
                "instruction -- an agent's record is a peer's claim, and relaying "
                "it as a warrant would dress a peer up as him.",
                file=sys.stderr,
            )
            return None
        record = {
            "kind": "human",
            "channel_id": str(record.get("channel_id", "")),
            "message_id": str(record.get("message_id", "")),
            "author_id": str(record.get("author_id", "")),
        }
    else:
        parts = [p for p in ref.replace("https://discord.com/channels/", "").split("/") if p]
        if len(parts) < 2 or not all(p.isdigit() for p in parts[-2:]):
            print(
                "hotline: error: --warrant wants the Discord message where he "
                "asked for this -- a message link, 'channel_id/message_id', or a "
                "provenance record you were sent.",
                file=sys.stderr,
            )
            return None
        record = {"kind": "human", "channel_id": parts[-2], "message_id": parts[-1]}

    if not record.get("channel_id") or not record.get("message_id"):
        print(
            "hotline: error: that record carries no Discord receipt, so there is "
            "nothing for the receiver to check. A warrant with no receipt is just "
            "this machine vouching for itself.",
            file=sys.stderr,
        )
        return None

    env = load_env()
    gated = env.get("DISCORD_USER_ID") or os.environ.get("DISCORD_USER_ID")
    verdict = verify(
        record,
        token=env.get("HOTLINE_BOT_TOKEN") or os.environ.get("HOTLINE_BOT_TOKEN"),
        gated_user_id=gated,
    )
    if not verdict.ok:
        print(f"hotline: error: not sending that warrant -- {verdict.summary}", file=sys.stderr)
        return None
    if gated and not record.get("author_id"):
        record["author_id"] = str(gated)
    return {k: v for k, v in record.items() if v}


def _speaking_as() -> Origin:
    """Who this invocation is, as honestly as it can be established.

    `$CLAUDE_CODE_SESSION_ID` is set inside a session and absent in a plain
    shell, which is the only distinction available and is not a strong one -- an
    agent could set it to anything. It is labelled a claim precisely because it
    is one; the alternative is the status quo, where the receiver is told nothing
    at all and has to do forensics on a Discord channel to work out who is
    talking to it.
    """
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return Origin(kind="human", label="a shell on this machine")
    name = _session_name(session_id) or session_id[:8]
    registered = Registry().get(session_id)
    if registered is not None and registered.privileged:
        # The role travels with the record, so it survives a respawn: a
        # replacement that adopts `hotline-80` speaks with the same standing as
        # the session it replaced, which is the point of the role being standing
        # rather than per-session.
        return Origin(
            kind="sys-admin",
            label=f"{registered.name} ({registered.authority})",
            session_id=session_id,
            granted_by=registered.granted_by,
            granted_in=registered.granted_in,
            extra={"task": registered.task[:120]},
        )
    return Origin(
        kind="agent",
        label=registered.name if registered else name,
        session_id=session_id,
        extra={"task": registered.task[:120]} if registered else {},
    )


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
            log(
                f"PreToolUse guard {'installed' if guard_changed else 'already registered'}: {guard}"
            )
        log(f"spool: {stops_dir()}")
        return 0

    if (
        args.declare
        or args.done
        or args.agents
        or args.resume
        or args.voice
        or args.adopt
        or args.provenance
        or args.grant
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

    # Whatever this CLI is speaking for. Run from a session it is that agent; run
    # from a plain shell it is a person with no receipt to offer, and it says so
    # rather than claiming an authority it cannot demonstrate. Either way the
    # receiver is told what it is reading instead of having to guess, which is
    # the whole of the defect this closes.
    origin = _speaking_as()
    if args.warrant:
        if not args.to:
            print(
                "hotline: error: --warrant only means something with --to. A "
                "warrant says who asked for an instruction being relayed; there "
                "is nobody to relay it to here.",
                file=sys.stderr,
            )
            return 2
        origin.warrant = _resolve_warrant(args.warrant)
        if origin.warrant is None:
            return 1
        log("   (carrying his receipt: the receiver can check who asked, not just who relayed)")

    try:
        if args.to:
            target = router.resolve(args.to)
            log(f"-> {describe(target)}")
            # Said before the wait, not after it. A message to a busy session is
            # queued until its current turn ends, and a caller who is not told
            # that reads the silence as a failure and resends -- which queues a
            # second copy. This is the same fact the stand-in gives a Discord
            # caller; the CLI simply never had it.
            if mid_turn(target):
                log(
                    "   (it is mid-turn; your message is queued behind that and "
                    "will not be seen until it finishes. Do not resend.)"
                )
            if args.no_wait:
                # The fire-and-forget case, which had no route before: the caller
                # wants the message delivered, not answered. Reusing the waiting
                # path and ignoring its result would burn the whole timeout to
                # learn something known in milliseconds.
                asyncio.run(router.deliver(args.to, text, origin=origin))
                log("   delivered; not waiting for an answer")
                return 0
            reply = asyncio.run(
                router.ask_session(
                    args.to, text, narrator=narrate, timeout=args.timeout, origin=origin
                )
            )
        else:
            route = Route("fresh", None, text) if fell_through else parse_utterance(text)
            if route.mode != "fresh" and route.target:
                log(f"-> {route.mode} {route.target}")
                reply = asyncio.run(
                    router.ask_session(
                        route.target,
                        route.text,
                        narrator=narrate,
                        timeout=args.timeout,
                        origin=origin,
                    )
                )
            else:
                log("-> fresh session")
                reply = asyncio.run(
                    router.ask_fresh(route.text, narrator=narrate, timeout=args.timeout)
                )
    except ReplyTimeout as exc:
        # Distinct from a failure, because it is not one. The message was
        # accepted; the answer did not arrive in time. Collapsing the two into
        # exit 1 meant a script checking $? read a correctly delivered message as
        # a failure -- and the accompanying text says "do not resend", so the
        # exit code and the words were giving opposite instructions.
        #
        # Found by the agent on the far end of the first cross-session reply:
        # queued delivery is reported twice, once as advice and once as an error.
        print(f"hotline: error: {exc}", file=sys.stderr)
        print(
            "hotline: (exit 3 = delivered, not answered yet. It is NOT lost and "
            "it is NOT a delivery failure. Do not resend.)",
            file=sys.stderr,
        )
        return 3
    except HotlineError as exc:
        print(f"hotline: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("hotline: aborted", file=sys.stderr)
        return 2

    print(reply.text)
    return 0
