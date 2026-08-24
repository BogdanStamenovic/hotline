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
import sys
from collections.abc import Sequence
from typing import NoReturn

from . import __version__
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
